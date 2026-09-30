#!/usr/bin/env python3
"""
DAR Explorer - parser and archive tree model.

The parser deliberately uses the output of `dar -l` instead of libdar.
This keeps the GUI independent of libdar's C++ API.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
import re
import subprocess


@dataclass(slots=True)
class ParsedEntry:
    path: str
    is_dir: bool
    permissions: str
    user: str
    group: str
    size: str
    date: str


# DAR's listing contains a date such as: Wed Jan 31 06:45:58 2024
# LC_ALL=C is forced when DAR is executed, so this is deterministic.
_DATE = (
    r"(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+"
    r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+"
    r"\d{1,2}\s+\d{2}:\d{2}:\d{2}\s+\d{4}"
)

# KORREKTUR: Der Größen-Regex erlaubt nun explizit Text wie "3 Mio", "2 Mio" oder "17 o"
_ENTRY_RE = re.compile(
    r"^.*?"                                        # Überschnitt [InRef]-Metadaten am Anfang
    r"(?P<permissions>[bcdlps-][rwxXsStT-]{9})\s+" # Berechtigungen (z.B. -rwxr-xr-x)
    r"(?P<user>\S+)\s+"                            # Benutzer (z.B. burda)
    r"(?P<group>\S+)\s+"                           # Gruppe (z.B. burda)
    r"(?P<size>.+?)\s+"                            # KORREKTUR: Akzeptiert ALLES (Zahlen + "Mio", "Kio", "o") bis zum Datum
    rf"(?P<date>{_DATE})\s+"                       # Das vordefinierte Datum
    r"(?P<path>.+)$"                               # Der Dateipfad bis zum Zeilenende
)




def normalize_archive_argument(path: str) -> str:
    """
    DAR identifies multi-slice archives by their basename.
    If the user selected foo.1.dar, use foo.
    """
    path = os.path.expanduser(path)
    m = re.match(r"^(.*)\.(\d+)\.dar\$", path)
    if m:
        return m.group(1)
    return path


def run_dar_listing(path: str) -> tuple[list[ParsedEntry], str]:
    """
    Führt DAR aus und streamt die Ausgabe zeilenweise, um Puffer-Blockaden
    bei sehr großen Archiven zu verhindern.
    """
    archive_arg = normalize_archive_argument(path)

    env = os.environ.copy()
    env["LC_ALL"] = "C"
    env["LANG"] = "C"
    env["COLUMNS"] = "4096"

    try:
        # KORREKTUR: Popen nutzen, um die Ausgabe live zu streamen
        process = subprocess.Popen(
            ["dar", "-l", archive_arg],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            "DAR wurde nicht gefunden. Bitte prüfen, ob `dar` im PATH liegt."
        ) from exc
    except OSError as exc:
        raise RuntimeError(f"DAR konnte nicht gestartet werden: {exc}") from exc

    entries: list[ParsedEntry] = []
    skipped = 0

    # Live-Streaming der Zeilen direkt aus dem stdout-Stream
    # Das leert den System-Puffer kontinuierlich und verhindert das Abschneiden!
    for line in process.stdout:
        line = line.rstrip()
        if not line or line.startswith("-" * 5):
            continue

        match = _ENTRY_RE.match(line)
        if not match:
            if "Permission" not in line and "filename" not in line:
                skipped += 1
            continue

        path_value = match.group("path").strip()
        if not path_value:
            continue

        permissions = match.group("permissions")
        entries.append(
            ParsedEntry(
                path=path_value,
                is_dir=permissions.startswith("d"),
                permissions=permissions,
                user=match.group("user"),
                group=match.group("group"),
                size=match.group("size").strip(),
                date=match.group("date"),
            )
        )

    # Warten, bis der Prozess fertig ist und stderr auslesen
    stderr_output = process.stderr.read()
    returncode = process.wait()

    if returncode != 0:
        details = stderr_output.strip()
        raise RuntimeError(
            f"`dar -l` wurde mit Exit-Code {returncode} beendet."
            + (f"\n\n{details}" if details else "")
        )

    if not entries:
        raise RuntimeError(
            "Es konnten keine Archiveinträge aus der DAR-Ausgabe gelesen werden."
        )

    warning = stderr_output.strip()
    if skipped and not warning:
        warning = f"{skipped} nicht relevante Ausgabezeile(n) wurden ignoriert."

    return entries, warning



def sort_key(entry: ParsedEntry):
    return (not entry.is_dir, entry.path.casefold())


def parse_listing(text: str) -> list[ParsedEntry]:
    entries: list[ParsedEntry] = []
    for line in text.splitlines():
        match = _ENTRY_RE.match(line.rstrip())
        if not match:
            continue
        entries.append(
            ParsedEntry(
                path=match.group("path").strip(),
                is_dir=match.group("permissions").startswith("d"),
                permissions=match.group("permissions"),
                user=match.group("user"),
                group=match.group("group"),
                size=match.group("size").strip(),
                date=match.group("date"),
            )
        )
    return entries
