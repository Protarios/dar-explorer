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
    Führt DAR aus und parst die XML-Ausgabe stabil via Streaming.
    Verhindert Hierarchie-Konflikte bei doppelten XML-Knoten.
    """
    archive_arg = normalize_archive_argument(path)

    env = os.environ.copy()
    env["LC_ALL"] = "C"
    env["LANG"] = "C"

    try:
        process = subprocess.Popen(
            ["dar", "-l", archive_arg, "-T", "xml", "-w"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("DAR wurde nicht gefunden.")
    except OSError as exc:
        raise RuntimeError(f"DAR Fehler: {exc}")

    entries: list[ParsedEntry] = []
    path_stack: list[str] = []

    total_tags_seen = 0
    directory_tags_seen = 0
    file_tags_seen = 0
    immich_matches_parsed = 0

    try:
        context = ET.iterparse(process.stdout, events=("start", "end"))

        for event, elem in context:
            tag = elem.tag.lower()
            name = elem.attrib.get("name", "").strip()

            if event == "start":
                total_tags_seen += 1

                if tag == "directory" and name:
                    directory_tags_seen += 1
                    full_path = "/".join(path_stack + [name])

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
                    path_stack.append(name)

                elif tag == "file" and name:
                    file_tags_seen += 1
                    file_path = "/".join(path_stack + [name])

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
                    if path_stack and path_stack[-1] == name:
                        path_stack.pop()
                elem.clear()

    except ET.ParseError as err:
        process.terminate()
        raise RuntimeError(f"XML Parse Fehler: {err}")

    stderr_output = process.stderr.read().decode("utf-8", errors="replace")
    process.wait()

    # REINES DEBUGGING: Wir gucken uns Stichproben aus der Masse an
    print("\n--- PARSER STICHPROBE ---")
    immich_entries = [e for e in entries if "immich" in e.path.lower()]
    print(f"Gesamtanzahl Immich-Objekte in der Liste: {len(immich_entries)}")

    # Die ersten 5 zeigen
    print("Die ersten 5:")
    for i, e in enumerate(immich_entries[:5]):
        print(f"  [{i+1}] Pfad: '{e.path}' (Ordner? {e.is_dir})")

    # Stichprobe aus der Mitte zeigen (falls vorhanden)
    if len(immich_entries) > 500:
        print("Stichprobe aus der Mitte (Index 500-510):")
        for i, e in enumerate(immich_entries[500:510]):
            print(f"  [500+{i}] Pfad: '{e.path}' (Ordner? {e.is_dir})")
    print("-------------------------\n")



    return entries, stderr_output.strip()
