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

def run_dar_listing(path: str, password_callback=None) -> tuple[list[ParsedEntry], str]:
    import pty
    import os
    import io

    archive_arg = normalize_archive_argument(path)
    env = os.environ.copy()
    env["LC_ALL"] = "C"
    env["LANG"] = "C"

    # Wir nutzen ein PTY (Pseudo-Terminal), damit DAR die Passwort-Eingabe akzeptiert
    master_fd, slave_fd = pty.openpty()

    try:
        process = subprocess.Popen(
            ["dar", "-l", archive_arg, "-T", "xml", "-w"],
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            close_fds=True,
            env=env,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("DAR wurde nicht gefunden. Bitte prüfen, ob `dar` im PATH liegt.") from exc
    except OSError as exc:
        raise RuntimeError(f"DAR konnte nicht gestartet werden: {exc}") from exc

    os.close(slave_fd)

    entries: list[ParsedEntry] = []
    path_stack: list[str] = []
    xml_accumulator = []

    buffer = ""
    parsing_started = False

    while True:
        try:
            char_bytes = os.read(master_fd, 1)
        except OSError:
            break
        if not char_bytes:
            break

        char = char_bytes.decode("utf-8", errors="replace")

        if not parsing_started:
            buffer += char
            # Prüfen, ob eine Zeile abgeschlossen ist
            if char == "\n":
                # Wenn es normaler Text vor dem XML ist (z.B. die Warnung), sammeln wir ihn nicht fürs XML
                buffer = ""
            else:
                clean_buf = buffer.strip()
                # ERKENNUNG DER PASSWORT-ABFRAGE
                if clean_buf.endswith("requires a password:"):
                    if password_callback:
                        # Popup in der GUI triggern
                        password = password_callback(buffer)
                        if password is not None:
                            os.write(master_fd, (password + "\n").encode("utf-8"))
                        else:
                            process.terminate()
                            raise RuntimeError("Archiv-Einlesen vom Benutzer abgebrochen (kein Passwort angegeben).")
                    buffer = ""

                # Sobald das erste XML-Zeichen auftaucht, schalten wir um
                elif "<" in buffer:
                    parsing_started = True
                    # Den bisherigen Inhalt des Puffers (das '<' und evtl. Reste) fürs XML retten
                    xml_accumulator.append(buffer.encode("utf-8"))
                    buffer = ""
        else:
            # Wenn wir im XML-Modus sind, sammeln wir die Bytes direkt für den Parser
            xml_accumulator.append(char_bytes)

    # Den gesammelten XML-Stream im Speicher als Datei-ähnliches Objekt aufbereiten
    xml_data = b"".join(xml_accumulator)

    if not xml_data:
        process.wait()
        raise RuntimeError("Es wurden keine XML-Daten von DAR empfangen. Falsches Passwort?")

    # Nun jagen wir die sauberen XML-Daten durch den bewährten ElementTree-Parser
    try:
        # Inkrementelles XML-Parsing aus dem Speicher-Stream
        context = ET.iterparse(io.BytesIO(xml_data), events=("start", "end"))
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
                elem.clear()

    except ET.ParseError as err:
        process.terminate()
        raise RuntimeError(f"XML Struktur-Fehler beim Laden: {err}. Eventuell falsches Passwort?") from err

    process.wait()
    return entries, ""
