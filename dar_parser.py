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
    # KORREKTUR: '$' statt '\$' am Ende, damit das Zeilenende korrekt erkannt wird!
    m = re.match(r"^(.*)\.(\d+)\.dar$", path)
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


def run_dar_listing(path: str, password_callback=None, progress_callback=None, encryption_password=None) -> tuple[list[ParsedEntry], str]:
    import io
    import xml.etree.ElementTree as ET

    archive_arg = normalize_archive_argument(path)
    env = os.environ.copy()
    env["LC_ALL"] = "C"
    env["LANG"] = "C"

    cmd = ["dar", "-l", archive_arg, "-T", "xml", "-w"]
    if encryption_password:
        cmd.extend(["-K", encryption_password])

    try:
        process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            close_fds=True,
            env=env,
        )
    except Exception as exc:
        raise RuntimeError(f"DAR konnte nicht gestartet werden: {exc}")

    if process.stdin:
        process.stdin.close()

    entries: list[ParsedEntry] = []
    path_stack: list[str] = []
    xml_parser = ET.XMLPullParser(["start", "end"])
    line_counter = 0

    # WICHTIG: Wir nutzen select oder einfaches Auslesen. Da DAR bei fehlendem
    # Katalog sofort stoppt, lesen wir stdout zeilenweise.
    if process.stdout:
        for line in process.stdout:
            clean_line = line.strip()
            # Wenn DAR Fehler ausgibt, fängt es meist nicht mit XML an
            if not clean_line.startswith(b"<") and b"error" in clean_line.lower():
                break

            if not clean_line or not clean_line.startswith(b"<"):
                continue

            try:
                xml_parser.feed(line)
            except ET.ParseError:
                break

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
                            size="—", date="—"
                        ))
                        path_stack.append(name)
                    elif tag == "File" and name:
                        file_path = "/".join(path_stack + [name])
                        entries.append(ParsedEntry(
                            path=file_path, is_dir=False,
                            permissions=elem.attrib.get("permissions", "-rw-r----"),
                            user=elem.attrib.get("user", "unknown"), group=elem.attrib.get("group", "unknown"),
                            size=format_size(elem.attrib.get("size", "0")),
                            date=format_date(elem.attrib.get("date", "—"))
                        ))
                elif event == "end":
                    if tag == "Directory" and name and path_stack and path_stack[-1] == name:
                        path_stack.pop()
                    elem.clear()

            if progress_callback:
                line_counter += 1
                if line_counter >= 5000:
                    progress_callback(len(entries))
                    line_counter = 0

        xml_parser.close()

    # Fehlerkanal auslesen, falls stdout versiegt ist
    stderr_data = process.stderr.read().decode("utf-8", errors="replace") if process.stderr else ""
    returncode = process.wait()

    if returncode != 0:
        # Hier fangen wir die Meldung ab, dass die letzte Slice fehlt!
        error_msg = stderr_data.strip()
        if not error_msg:
            error_msg = f"DAR abgebrochen (Exit-Code {returncode}). Letzte Slice des Archivs fehlt vermutlich!"
        raise RuntimeError(error_msg)

    if not entries:
        raise RuntimeError("Es wurden keine Einträge aus dem Archiv gelesen. Letzte Slice fehlt oder Passwort falsch.")

    return entries, ""

