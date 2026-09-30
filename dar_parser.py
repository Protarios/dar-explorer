#!/usr/bin/env python3
"""
DAR Explorer - Ausfallsicherer XML-Parser (Final).

Nutzt 'dar -l -T xml', um das Archiv-Inhaltsverzeichnis als strukturiertes XML
einzulesen. Parsing-Fehler gehören damit der Vergangenheit an.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
import re
import subprocess
import xml.etree.ElementTree as ET


@dataclass(slots=True)
class ParsedEntry:
    path: str
    is_dir: bool
    permissions: str
    user: str
    group: str
    size: str
    date: str


def normalize_archive_argument(path: str) -> str:
    path = os.path.expanduser(path)
    m = re.match(r"^(.*)\.(\d+)\.dar\$", path)
    if m:
        return m.group(1)
    return path


def run_dar_listing(path: str) -> tuple[list[ParsedEntry], str]:
    """
    Führt DAR mit der Option -T xml aus und liest die Struktur als XML ein.
    """
    archive_arg = normalize_archive_argument(path)

    env = os.environ.copy()
    env["LC_ALL"] = "C"
    env["LANG"] = "C"

    try:
        # -T xml aktiviert die strukturierte XML-Ausgabe
        process = subprocess.Popen(
            ["dar", "-l", archive_arg, "-T", "xml", "-w"],
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

    # XML-Daten vollständig auslesen
    stdout_data, stderr_data = process.communicate()
    returncode = process.wait()

    if returncode != 0:
        details = stderr_data.strip() or stdout_data.strip()
        raise RuntimeError(
            f"`dar -l -T xml` wurde mit Exit-Code {returncode} beendet."
            + (f"\n\n{details}" if details else "")
        )

    if not stdout_data.strip():
        raise RuntimeError("Die XML-Ausgabe von DAR war komplett leer.")

    entries: list[ParsedEntry] = []

    try:
        # Wir betten den Text in ein virtuelles Root-Element ein,
        # falls DAR kein valides XML-Root-Tag mitsendet
        if not stdout_data.strip().startswith("<?xml"):
            stdout_data = f"<archive>{stdout_data}</archive>"

        root = ET.fromstring(stdout_data)

        # Interner Stack, um den aktuellen Verzeichnispfad zu tracken
        # Da DAR die Verzeichnisse verschachtelt ausgibt, bauen wir den Pfad live auf
        def parse_element(element: ET.Element, current_path_parts: list[str]):
            tag = element.tag.lower()
            name = element.attrib.get("name", "").strip()

            # Falls das Element keinen Namen hat (z.B. das künstliche Root), überspringen
            if not name:
                for child in element:
                    parse_element(child, current_path_parts)
                return

            # Pfad für das aktuelle Element zusammensetzen
            new_parts = current_path_parts + [name]
            full_path = "/".join(new_parts)

            if tag == "directory":
                # Verzeichnis registrieren
                entries.append(
                    ParsedEntry(
                        path=full_path,
                        is_dir=True,
                        permissions=element.attrib.get("permissions", "drwxr-xr-x"),
                        user=element.attrib.get("user", "unknown"),
                        group=element.attrib.get("group", "unknown"),
                        size="—",
                        date=element.attrib.get("date", "—"),
                    )
                )
                # Rekursiv tiefer in die Unterordner gehen
                for child in element:
                    parse_element(child, new_parts)

            elif tag == "file":
                # Datei registrieren
                entries.append(
                    ParsedEntry(
                        path=full_path,
                        is_dir=False,
                        permissions=element.attrib.get("permissions", "-rw-r--r--"),
                        user=element.attrib.get("user", "unknown"),
                        group=element.attrib.get("group", "unknown"),
                        size=element.attrib.get("size", "0"),
                        date=element.attrib.get("date", "—"),
                    )
                )

        # Rekursion starten
        parse_element(root, [])

    except ET.ParseError as err:
        raise RuntimeError(
            f"Fehler beim Parsen der DAR-XML-Struktur: {err}\n"
            f"Bitte prüfen Sie die 'DAR-Ausgabe' im Logfenster."
        )

    if not entries:
        raise RuntimeError("Es wurden keine gültigen Dateien oder Ordner im XML gefunden.")

    return entries, stderr_data.strip()
