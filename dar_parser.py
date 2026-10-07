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
    size_bytes: int = 0
    timestamp: int = 0

def normalize_archive_argument(path: str) -> str:
    path = os.path.expanduser(path)
    # KORREKTUR: '$' statt '\$' am Ende, damit das Zeilenende korrekt erkannt wird!
    m = re.match(r"^(.*)\.(\d+)\.dar$", path)
    return m.group(1) if m else path

def parse_size_bytes(size_val: str | int | float | None) -> int:
    """Convert DAR's (possibly localized) size representation to bytes.

    DAR may emit localized units such as ``o``, ``Ko``, ``Mo`` and ``Go``
    (French ``octet`` notation), as well as the English/German forms B, KB,
    MB, ... .  Never pass these strings directly to ``int()``.
    """
    if size_val is None:
        return 0
    if isinstance(size_val, (int, float)):
        return max(0, int(size_val))

    raw = str(size_val).replace("\u00a0", " ").strip()
    if not raw or raw == "—":
        return 0

    # Number + optional unit.  Accept decimal comma/dot and French/German
    # thousands separators.  DAR's localized output commonly uses forms like
    # ``17 o``, ``2 Ko``, ``1,5 Mo`` or ``1.234,5 Mo``.
    m = re.match(r"^\s*([+-]?(?:\d+(?:[.,]\d+)?|\d{1,3}(?:[. ]\d{3})+(?:,\d+)?))\s*([A-Za-z]+)?\s*$", raw)
    if not m:
        digits = re.sub(r"[^0-9]", "", raw)
        return int(digits) if digits else 0

    number = m.group(1).replace(" ", "")
    unit = (m.group(2) or "B").lower()

    if "," in number and "." in number:
        # German/French form: 1.234,5
        number = number.replace(".", "").replace(",", ".")
    elif "," in number:
        number = number.replace(",", ".")

    try:
        value = float(number)
    except ValueError:
        return 0

    # DAR/localized forms.  ``o`` = octet, ``ko/mo/go/to`` = powers of 1024
    # in DAR's traditional size formatting.  Also accept SI and IEC spellings.
    multipliers = {
        "b": 1, "o": 1, "octet": 1, "octets": 1,
        "k": 1024, "ko": 1024, "kio": 1024, "kib": 1024, "kb": 1000,
        "m": 1024**2, "mo": 1024**2, "mio": 1024**2, "mib": 1024**2, "mb": 1000**2,
        "g": 1024**3, "go": 1024**3, "gio": 1024**3, "gib": 1024**3, "gb": 1000**3,
        "t": 1024**4, "to": 1024**4, "tio": 1024**4, "tib": 1024**4, "tb": 1000**4,
        "p": 1024**5, "po": 1024**5, "pio": 1024**5, "pib": 1024**5, "pb": 1000**5,
        "mio": 1_000_000, "mrd": 1_000_000_000,
    }
    # Long German localized forms.
    multipliers.update({
        "milliarde": 1_000_000_000, "milliarden": 1_000_000_000,
    })

    multiplier = multipliers.get(unit)
    if multiplier is None:
        # Unknown unit: try the numeric part only rather than crashing the GUI.
        return max(0, int(value))
    return max(0, int(value * multiplier))


def format_size(size_val: str | int | float) -> str:
    """Return a consistent human-readable size using B/KB/MB/GB/TB.

    The internal calculation uses 1024-byte steps, matching DAR's traditional
    binary size display, but deliberately uses the familiar ``KB/MB/GB`` labels
    instead of DAR's abbreviated ``K/M/G`` output.
    """
    bytes_num = parse_size_bytes(size_val)
    if bytes_num <= 0:
        return "0 B" if size_val not in (None, "", "—") else "—"

    units = ("B", "KB", "MB", "GB", "TB", "PB")
    value = float(bytes_num)
    idx = 0
    while value >= 1024.0 and idx < len(units) - 1:
        value /= 1024.0
        idx += 1

    if idx == 0:
        return f"{int(value)} B"
    formatted = f"{value:.1f}".rstrip("0").rstrip(".")
    return f"{formatted} {units[idx]}"

def parse_timestamp(timestamp_str: str | int | None) -> int:
    """Konvertiert einen Unix-Timestamp; ungültige DAR-Werte werden zu 0."""
    if timestamp_str is None:
        return 0
    try:
        return int(timestamp_str)
    except (ValueError, TypeError):
        return 0


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
                            date=format_date(elem.attrib.get("date", "—")),
                            size_bytes=parse_size_bytes(elem.attrib.get("size", "0")),
                            timestamp=parse_timestamp(elem.attrib.get("date", "0")),
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


