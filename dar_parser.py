#!/usr/bin/env python3
"""
DAR Explorer - Ausfallsicherer XML-Parser mit Debugging.
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
    Führt DAR aus und parst die XML-Ausgabe inkrementell mit integriertem Logging.
    """
    archive_arg = normalize_archive_argument(path)

    env = os.environ.copy()
    env["LC_ALL"] = "C"
    env["LANG"] = "C"

    try:
        # Wir lesen im Binärmodus, um iterparse direkt den Stream zu übergeben
        process = subprocess.Popen(
            ["dar", "-l", archive_arg, "-T", "xml", "-w"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            "DAR wurde nicht gefunden. Bitte prüfen, ob `dar` im PATH liegt."
        ) from exc
    except OSError as exc:
        raise RuntimeError(f"DAR konnte nicht gestartet werden: {exc}") from exc

    entries: list[ParsedEntry] = []

    # Der Stack speichert Tupel aus (XML-Element, "Name")
    path_stack: list[tuple[ET.Element, str]] = []

    total_tags_seen = 0
    directory_tags_seen = 0
    file_tags_seen = 0
    immich_matches_parsed = 0

    try:
        # iterparse liest direkt aus dem binären stdout-Stream des Prozesses
        context = ET.iterparse(process.stdout, events=("start", "end"))

        for event, elem in context:
            # Sichert die Erkennung unabhängig von der Groß-/Kleinschreibung des DAR-Tags
            tag = elem.tag.lower()
            name = elem.attrib.get("name", "").strip()

            if event == "start":
                total_tags_seen += 1

                if tag == "directory" and name:
                    directory_tags_seen += 1

                    # Pfad aus dem Tupel-Stack (Index 1) korrekt extrahieren
                    current_parts = [item[1] for item in path_stack] + [name]
                    full_path = "/".join(current_parts)

                    if "immich" in full_path.lower():
                        immich_matches_parsed += 1

                    entries.append(
                        ParsedEntry(
                            path=full_path,
                            is_dir=True,
                            permissions=elem.attrib.get("permissions", "drwxr-xr-x"),
                            user=elem.attrib.get("user", "unknown"),
                            group=elem.attrib.get("group", "unknown"),
                            size="—",
                            date=elem.attrib.get("date", "—"),
                        )
                    )
                    path_stack.append((elem, name))

                elif tag == "file" and name:
                    file_tags_seen += 1

                    current_parts = [item[1] for item in path_stack] + [name]
                    file_path = "/".join(current_parts)

                    if "immich" in file_path.lower():
                        immich_matches_parsed += 1

                    entries.append(
                        ParsedEntry(
                            path=file_path,
                            is_dir=False,
                            permissions=elem.attrib.get("permissions", "-rw-r--r--"),
                            user=elem.attrib.get("user", "unknown"),
                            group=elem.attrib.get("group", "unknown"),
                            size=elem.attrib.get("size", "0"),
                            date=elem.attrib.get("date", "—"),
                        )
                    )

            elif event == "end":
                if tag == "directory" and name:
                    # Sicherer Objektvergleich mit dem obersten Element des Stack-Tupels
                    if path_stack and path_stack[-1][0] == elem:
                        path_stack.pop()

                # RAM freigeben
                elem.clear()

    except ET.ParseError as err:
        process.terminate()
        print(f"\n[CRITICAL] XML-Streaming abgebrochen bei Tag-Nummer {total_tags_seen}: {err}")
        raise RuntimeError(f"XML Parse Fehler: {err}")

    # TERMINAL-LOGGING AUSGEBEN
    print("\n" + "="*50)
    print("DEBUG: DAR_PARSER STATISTIK")
    print(f"Gesehene XML-Tags gesamt: {total_tags_seen}")
    print(f"Gesehene Directory-Tags:  {directory_tags_seen}")
    print(f"Gesehene File-Tags:       {file_tags_seen}")
    print(f"Einträge mit 'Immich' im Pfad erfolgreich geparst: {immich_matches_parsed}")
    print(f"Generierte ParsedEntry-Objekte gesamt: {len(entries)}")
    print("="*50 + "\n")

    stderr_output = process.stderr.read().decode("utf-8", errors="replace")
    process.wait()

    return entries, stderr_output.strip()
