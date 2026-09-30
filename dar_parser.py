#!/usr/bin/env python3
"""
DAR Explorer - Ausfallsicherer XML-Parser (Streaming & Speicheroptimiert).
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
    return m.group(1) if m else path

def run_dar_listing(path: str) -> tuple[list[ParsedEntry], str]:
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
        raise RuntimeError("DAR wurde nicht gefunden. Bitte prüfen, ob `dar` im PATH liegt.") from exc
    except OSError as exc:
        raise RuntimeError(f"DAR konnte nicht gestartet werden: {exc}") from exc

    entries: list[ParsedEntry] = []
    path_stack: list[str] = []

    try:
        # Inkrementelles XML-Streaming direkt aus der Pipe
        context = ET.iterparse(process.stdout, events=("start", "end"))
        for event, elem in context:
            tag = elem.tag.capitalize()
            name = elem.attrib.get("name", "").strip()

            if event == "start":
                if tag == "Directory" and name:
                    full_path = "/".join(path_stack + [name])
                    entries.append(ParsedEntry(
                        path=full_path, is_dir=True,
                        permissions=elem.attrib.get("permissions", "drwxr-xr-x"),
                        user=elem.attrib.get("user", "unknown"), group=elem.attrib.get("group", "unknown"),
                        size="—", date=elem.attrib.get("date", "—")
                    ))
                    path_stack.append(name)
                elif tag == "File" and name:
                    file_path = "/".join(path_stack + [name])
                    entries.append(ParsedEntry(
                        path=file_path, is_dir=False,
                        permissions=elem.attrib.get("permissions", "-rw-r----"),
                        user=elem.attrib.get("user", "unknown"), group=elem.attrib.get("group", "unknown"),
                        size=elem.attrib.get("size", "0"), date=elem.attrib.get("date", "—")
                    ))
            elif event == "end":
                if tag == "Directory" and name and path_stack and path_stack[-1] == name:
                    path_stack.pop()
                elem.clear() # Speicher sofort freigeben

    except ET.ParseError as err:
        process.terminate()
        raise RuntimeError(f"XML Parse Fehler: {err}") from err

    stderr_output = process.stderr.read().decode("utf-8", errors="replace")
    process.wait()
    return entries, stderr_output.strip()
