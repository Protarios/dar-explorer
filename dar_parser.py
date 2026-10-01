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
        # Bereinigung: Entfernt 'o', 'B' und Leerzeichen (z.B. "51 o" -> "51")
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
        context = ET.iterparse(process.stdout, events=("start", "end"))
        for event, elem in context:
            tag = elem.tag.capitalize()
            name = elem.attrib.get("name", "").strip()

            if event == "start":
                # DAR nutzt im XML oft das Tag 'Attributes' als Kind-Element für die Metadaten
                if tag == "Directory" and name:
                    full_path = "/".join(path_stack + [name])
                    path_stack.append(name)

                    # Platzhalter-Eintrag, Attribute kommen im nächsten Schritt oder direkt
                    entries.append(ParsedEntry(
                        path=full_path, is_dir=True,
                        permissions=elem.attrib.get("permissions", "drwxr-xr-x").strip(),
                        user=elem.attrib.get("user", "unknown"), group=elem.attrib.get("group", "unknown"),
                        size="—", date=format_date(elem.attrib.get("mtime", "—"))
                    ))
                elif tag == "File" and name:
                    file_path = "/".join(path_stack + [name])
                    entries.append(ParsedEntry(
                        path=file_path, is_dir=False,
                        permissions=elem.attrib.get("permissions", "-rw-r----").strip(),
                        user=elem.attrib.get("user", "unknown"), group=elem.attrib.get("group", "unknown"),
                        size=format_size(elem.attrib.get("size", "0")),
                        date=format_date(elem.attrib.get("mtime", "—"))
                    ))

                # WICHTIG: DAR lagert bei neueren Versionen die Metadaten in ein eigenes Unter-Tag <Attributes> aus
                elif tag == "Attributes" and entries:
                    last_entry = entries[-1]
                    # Wenn das Attribute-Tag existiert, überschreiben wir die Werte mit den echten XML-Daten
                    if elem.attrib.get("permissions"):
                        last_entry.permissions = elem.attrib.get("permissions", "").strip()
                    if elem.attrib.get("user"):
                        last_entry.user = elem.attrib.get("user", "unknown")
                    if elem.attrib.get("group"):
                        last_entry.group = elem.attrib.get("group", "unknown")
                    if elem.attrib.get("mtime"):
                        last_entry.date = format_date(elem.attrib.get("mtime"))
                    if elem.attrib.get("size") and not last_entry.is_dir:
                        last_entry.size = format_size(elem.attrib.get("size"))

            elif event == "end":
                if tag == "Directory" and name and path_stack and path_stack[-1] == name:
                    path_stack.pop()
                elem.clear()

    except ET.ParseError as err:
        process.terminate()
        raise RuntimeError(f"XML Parse Fehler: {err}") from err

    stderr_output = process.stderr.read().decode("utf-8", errors="replace")
    process.wait()
    return entries, stderr_output.strip()
