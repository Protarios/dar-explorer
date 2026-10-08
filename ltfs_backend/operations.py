"""LTFS-Operationen, unabhängig von der GTK-/DAR-Prozesssteuerung."""

from __future__ import annotations

import os
import shutil
from pathlib import Path


def ltfs_executable() -> str | None:
    """Sucht LTFS, ohne mit DAR-Binaries zu interagieren."""
    return shutil.which("ltfs")


def unmount_executable() -> list[str] | None:
    """FUSE-Unmount bevorzugen; keinesfalls einen erzwungenen/lazy Unmount."""
    for binary in ("fusermount3", "fusermount", "umount"):
        found = shutil.which(binary)
        if found:
            return [found, "-u"] if binary.startswith("fusermount") else [found]
    return None


def validate_mountpoint(mountpoint: str) -> str:
    value = mountpoint.strip()
    if not value:
        raise ValueError("Kein LTFS-Einhängepunkt angegeben.")
    if any(c in value for c in "\n\r\x00"):
        raise ValueError("Ungültiger LTFS-Einhängepunkt.")
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValueError("Der LTFS-Einhängepunkt muss ein absoluter Pfad sein.")
    return str(path)


def build_mount_command(device: str, mountpoint: str, *, executable: str | None = None) -> list[str]:
    """Baut ausschließlich einen normalen LTFS-Mount, ohne Format/Eject."""
    device = device.strip()
    if not device or any(c in device for c in "\n\r\x00"):
        raise ValueError("Bitte ein gültiges LTFS-Bandlaufwerk (z. B. /dev/sg3) angeben.")
    binary = executable or ltfs_executable()
    if not binary:
        raise FileNotFoundError("'ltfs' ist nicht installiert oder nicht im PATH.")
    return [binary, "-o", f"devname={device}", validate_mountpoint(mountpoint)]


def build_unmount_command(mountpoint: str, *, command: list[str] | None = None) -> list[str]:
    binary_and_args = command if command is not None else unmount_executable()
    if not binary_and_args:
        raise FileNotFoundError("Weder fusermount3, fusermount noch umount ist verfügbar.")
    return [*binary_and_args, validate_mountpoint(mountpoint)]


def _unescape_mountinfo(text: str) -> str:
    """Escapes in /proc/self/mountinfo (Octalnotation für Spaces etc.)."""
    import re
    return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), text)


def filesystem_at(mountpoint: str, *, mountinfo: str | None = None) -> str | None:
    """Liefert den Dateisystemtyp für einen exakten Mountpoint (ohne FUSE-I/O)."""
    target = os.path.normpath(os.path.abspath(os.path.expanduser(mountpoint)))
    if mountinfo is None:
        try:
            with open("/proc/self/mountinfo", "r", encoding="utf-8") as f:
                mountinfo = f.read()
        except OSError:
            return None
    matches = []
    for line in mountinfo.splitlines():
        before, sep, after = line.partition(" - ")
        if not sep:
            continue
        parts = before.split()
        info = after.split()
        if len(parts) >= 5 and info and _unescape_mountinfo(parts[4]) == target:
            matches.append(info[0])
    return matches[-1] if matches else None


def is_ltfs_filesystem(fs_type: str | None) -> bool:
    return bool(fs_type and "ltfs" in fs_type.casefold())


def build_device_list_command(*, executable: str | None = None) -> list[str]:
    """LTFS-eigene Geräteauflistung; liest kein vorhandenes Bandarchiv."""
    binary = executable or ltfs_executable()
    if not binary:
        raise FileNotFoundError("'ltfs' ist nicht installiert oder nicht im PATH.")
    return [binary, "-o", "device_list"]


def validate_tape_device(device: str) -> str:
    """Linux-Tape-Device für mt, getrennt vom LTFS-SG-Device.

    Ein Gerät wie /dev/sg3 wird bewusst abgewiesen. Das Benutzerfeld darf
    alternativ einen persistenten Symlink unter /dev/tape/by-id enthalten.
    """
    import re
    value = device.strip()
    if any(c in value for c in "\n\r\x00"):
        raise ValueError("Ungültiger Name des mt-Geräts.")
    if not (re.fullmatch(r"/dev/nst\d+(?:[lma])?", value)
            or re.fullmatch(r"/dev/tape/by-id/[A-Za-z0-9_.:-]+", value)):
        raise ValueError("Für mt bitte /dev/nst0 oder einen Symlink /dev/tape/by-id/... verwenden (nicht /dev/sgX).")
    return value


def mt_executable() -> str | None:
    return shutil.which("mt")


def build_tape_status_command(device: str, *, executable: str | None = None) -> list[str]:
    binary = executable or mt_executable()
    if not binary:
        raise FileNotFoundError("'mt' ist nicht installiert oder nicht im PATH (z. B. Paket mt-st).")
    return [binary, "-f", validate_tape_device(device), "status"]


def build_tape_eject_command(device: str, *, executable: str | None = None) -> list[str]:
    """Nur manueller Aufruf nach bestätigtem abgeschlossenem LTFS-Unmount."""
    binary = executable or mt_executable()
    if not binary:
        raise FileNotFoundError("'mt' ist nicht installiert oder nicht im PATH (z. B. Paket mt-st).")
    return [binary, "-f", validate_tape_device(device), "offline"]


# Eine unbekannte Kassette gilt grundsätzlich NICHT als leer. Die
# Hardware-/Katalogprüfung erfolgt in der GUI durch explizite Freigabe.
def mkltfs_executable() -> str | None:
    return shutil.which("mkltfs")


def validate_format_device(device: str) -> str:
    """Nur einzelne, eindeutige Laufwerks-IDs (keine Shell-/CLI-Optionen)."""
    import re
    value = device.strip()
    # Nicht alle LTFS-Backends verwenden /dev/sg*: Seriennummern und
    # Tape-Symlinks werden ebenfalls unterstützt.
    allowed = (
        re.fullmatch(r"/dev/(?:sg|nst|st)\d+", value)
        or re.fullmatch(r"/dev/tape/by-id/[A-Za-z0-9_.:-]+", value)
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{3,63}", value)
    )
    if not allowed:
        raise ValueError(
            "Als Formatiergerät nur /dev/sgN, /dev/stN, /dev/nstN, "
            "/dev/tape/by-id/... oder eine eindeutige Laufwerksseriennummer angeben."
        )
    return value


def validate_cartridge_label(label: str) -> str:
    """Zur manuellen Gegenkontrolle des physischen Kassettenetiketts."""
    import re
    value = label.strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{1,31}", value):
        raise ValueError("Bandkennung: 2–32 Zeichen (Buchstaben, Zahlen, - oder _).")
    return value


def build_format_command(device: str, *, volume_name: str = "",
                         executable: str | None = None) -> list[str]:
    """LTFS neu anlegen – nie --force, --wipe, --long-wipe oder sudo."""
    binary = executable or mkltfs_executable()
    if not binary:
        raise FileNotFoundError("'mkltfs' ist nicht installiert oder nicht im PATH.")
    command = [binary, "-d", validate_format_device(device)]
    if any(ord(c) < 32 or ord(c) == 127 for c in volume_name):
        raise ValueError("Ungültiger LTFS-Volumename (keine Steuerzeichen).")
    name = volume_name.strip()
    if name:
        if len(name) > 64:
            raise ValueError("Ungültiger LTFS-Volumename (maximal 64 Zeichen).")
        command.extend(["-n", name])
    return command


def any_ltfs_mounted(*, mountinfo: str | None = None) -> bool:
    """Konservative Schutzprüfung: kein Formatieren bei irgendeinem LTFS-Mount."""
    if mountinfo is None:
        try:
            with open("/proc/self/mountinfo", encoding="utf-8") as handle:
                mountinfo = handle.read()
        except OSError as exc:
            raise RuntimeError("Mount-Tabelle nicht lesbar; Formatierung gesperrt.") from exc
    for line in mountinfo.splitlines():
        _before, sep, after = line.partition(" - ")
        if sep and after.split() and is_ltfs_filesystem(after.split()[0]):
            return True
    return False
