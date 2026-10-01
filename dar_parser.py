#!/usr/bin/env python3
"""
DAR Explorer - Ausfallsicherer XML-Parser (Mit Größen- und Datumsformatierung).
"""

from __future__ import annotations
from dataclasses import dataclass
import datetime
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

def format_size(size_bytes_str: str) -> str:
    """
    Konvertiert die Byte-Zahl aus dem XML in ein gut lesbares Format wie 'df -h'.
    Schneidet störende Einheiten wie 'o' ab, um Abstürze zu verhindern.
    """
    try:
        clean_str = re.sub(r"[ \toB]", "", size_bytes_str)
        if not clean_str:
            return "0 B"

        bytes_val = int(clean_str)
        if bytes_val < 0:
            return "0 B"

        for unit in ["B", "KB", "MB", "GB", "TB", "PB"]:
            if bytes_val < 1000:
                if unit == "B":
                    return f"{bytes_val} B"
                return f"{bytes_val:.1f} {unit}".replace(".", ",")
            bytes_val /= 1000

        return f"{bytes_val:.1f} EB".replace(".", ",")
    except (ValueError, TypeError):
        return size_bytes_str


def format_date(timestamp_str: str) -> str:
    """Konvertiert den Unix-Timestamp aus dem XML in ein lesbares Datumsformat."""
    try:
        ts = int(timestamp_str)
        dt = datetime.datetime.fromtimestamp(ts)
        return dt.strftime("%d.%m.%Y %H:%M:%S")
    except (ValueError, TypeError):
        return timestamp_str


def run_dar_listing(path: str, password_callback=None, progress_callback=None) -> tuple[list[ParsedEntry], str]:
    import io
    import xml.etree.ElementTree as ET

    archive_arg = normalize_archive_argument(path)
    env = os.environ.copy()
    env["LC_ALL"] = "C"
    env["LANG"] = "C"

    try:
        process = subprocess.Popen(
            ["dar", "-l", archive_arg, "-T", "xml", "-w"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            close_fds=True,
            env=env,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("DAR wurde nicht gefunden. Bitte prüfen, ob `dar` im PATH liegt.") from exc
    except OSError as exc:
        raise RuntimeError(f"DAR konnte nicht gestartet werden: {exc}") from exc

    if process.stdin:
        process.stdin.close()

    entries: list[ParsedEntry] = []
    path_stack: list[str] = []

    xml_parser = ET.XMLPullParser(["start", "end"])
    last_reported_count = 0

    if process.stdout:
        for line in process.stdout:
            xml_parser.feed(line)

            for event, elem in xml_parser.read_events():
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
                    elem.clear()

            # Übergibt den Fortschritt stabil an die Bridge
            if progress_callback:
                current_count = len(entries)
                if current_count - last_reported_count >= 5000:
                    progress_callback(current_count)
                    last_reported_count = current_count

        if progress_callback:
            progress_callback(len(entries))

        xml_parser.close()

    stderr_data = ""
    if process.stderr:
        stderr_data = process.stderr.read().decode("utf-8", errors="replace")

    returncode = process.wait()

    if returncode != 0:
        error_msg = stderr_data.strip() or f"Exit-Code {returncode}"
        raise RuntimeError(f"DAR meldete einen Fehler beim Einlesen:\n{error_msg}")

    if not entries:
        raise RuntimeError("Es wurden keine Einträge aus dem Archiv gelesen. Ist das Archiv verschlüsselt oder beschädigt?")

    return entries, ""
