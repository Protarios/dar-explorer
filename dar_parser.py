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

def format_size(size_val: str | int) -> str:
    """
    Konvertiert Byte-Zahlen oder vorgefertigte Größen-Strings in kompaktes,
    gut lesbares Format (z.B. '12 K', '13.5 G').
    """
    if not size_val or size_val == "—":
        return "—"

    # Falls der Wert als String vorliegt, bereinigen und versuchen als Zahl zu interpretieren
    if isinstance(size_val, str):
        raw = size_val.strip()
        if not raw:
            return "0 B"

        # Falls "Mio" oder "Milliard" aus lokalisierter dar-Ausgabe kommt
        if "mio" in raw.lower():
            raw = re.sub(r"[^\d,\.]", "", raw).replace(",", ".")
            try:
                bytes_num = float(raw) * 1_000_000
            except ValueError:
                return size_val
        elif "mrd" in raw.lower() or "milliarde" in raw.lower():
            raw = re.sub(r"[^\d,\.]", "", raw).replace(",", ".")
            try:
                bytes_num = float(raw) * 1_000_000_000
            except ValueError:
                return size_val
        else:
            # Versuche reine Ziffern zu extrahieren
            clean_str = re.sub(r"[^\d]", "", raw)
            if not clean_str:
                return raw
            bytes_num = int(clean_str)
    else:
        bytes_num = int(size_val)

    if bytes_num <= 0:
        return "0 B"

    # Umrechnung in Einheiten B, K, M, G, T
    units = ["B", "K", "M", "G", "T", "P"]
    val = float(bytes_num)
    unit_idx = 0

    while val >= 1024.0 and unit_idx < len(units) - 1:
        val /= 1024.0
        unit_idx += 1

    if unit_idx == 0:
        return f"{int(val)} B"

    # Eine Nachkommastelle bei Fließkommazahlen, z.B. 13.5 G
    formatted = f"{val:.1f}".rstrip("0").rstrip(".")
    return f"{formatted} {units[unit_idx]}"


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

        # KORREKTUR: Wir schließen den Parser nur, wenn wir auch Einträge gefunden haben.
        # Wenn 'entries' leer ist, hat DAR wegen eines falschen Passworts abgebrochen.
        # Das Schließen von xml_parser würde hier sonst den 'no element found' Absturz erzeugen!
        if entries:
            try:
                xml_parser.close()
            except ET.ParseError:
                pass

    stderr_data = process.stderr.read().decode("utf-8", errors="replace") if process.stderr else ""
    returncode = process.wait()

    if returncode != 0:
        error_msg = stderr_data.strip()
        # Spezifische Prüfung auf Passwortfehler im Fehlerstrom von DAR
        if "password" in error_msg.lower() or "auth" in error_msg.lower() or "krypt" in error_msg.lower():
            raise RuntimeError("Das eingegebene Passwort ist falsch oder ungültig.")
        if not error_msg:
            error_msg = f"DAR abgebrochen (Exit-Code {returncode}). Falsches Passwort oder beschädigte Datei?"
        raise RuntimeError(error_msg)

    if not entries:
        raise RuntimeError("Das Passwort ist falsch oder das Archiv enthält keine lesbaren Daten.")

    return entries, ""


