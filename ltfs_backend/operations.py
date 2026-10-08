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
