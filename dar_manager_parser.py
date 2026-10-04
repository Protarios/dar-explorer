import os
import re
import subprocess
import tempfile
from typing import Callable, Optional

def execute_dar_cmd(
    cmd: list[str],
    log_callback: Optional[Callable[[str], None]] = None
) -> bool:
    """
    Führt dar/dar_manager aus, kappt STDIN gegen Hänger, liest STDOUT/STDERR live
    und leitet JEDE Ausgabe direkt an die GUI weiter.
    """
    env = os.environ.copy()
    env["LC_ALL"] = "C"
    env["PYTHONUNBUFFERED"] = "1"

    # WICHTIG: Batch-Modus erzwingen, damit dar NIEMALS auf Konsoleneingaben wartet
    if "dar" in cmd[0] and "-N" not in cmd:
        cmd.append("-N")

    try:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,  # Signalisiert dar sofort: Keine Tastatur vorhanden!
            text=True,
            bufsize=1,
            env=env
        )

        # STDOUT live in die GUI schreiben
        if process.stdout:
            for line in iter(process.stdout.readline, ''):
                clean_line = line.strip()
                if clean_line and log_callback:
                    log_callback(f"[dar]: {clean_line}")

        # Restliche STDERR-Fehler lesen und unzensiert in die GUI werfen
        _, stderr_data = process.communicate()
        if stderr_data and log_callback:
            for err_line in stderr_data.splitlines():
                if err_line.strip():
                    log_callback(f"[dar FEHLER]: {err_line.strip()}")

        return process.returncode == 0

    except Exception as e:
        if log_callback:
            log_callback(f"[SYSTEM FEHLER]: Prozess konnte nicht gestartet werden: {e}")
        return False


import os
import re
import subprocess
import tempfile
from typing import Callable, Optional

def add_catalog_to_db(
    db_path: str,
    slice_path: str,
    archive_original_name: str,
    password: str = None,
    log_callback: Optional[Callable[[str], None]] = None
) -> bool:
    """
    Fügt das Slice der DMD hinzu.
    Führt STDOUT und STDERR zusammen und liest Ausgaben ungepuffert aus,
    sodass Passwort-Abfragen und Fehler sofort live in der GUI sichtbar sind.
    """
    clean_catalog_base = re.sub(r"\.\d+\.dar$", "", slice_path)
    clean_orig_base = re.sub(r"\.\d+\.dar$", "", archive_original_name)

    cmd = [
        "dar_manager",
        "-B", db_path,
        "-A", clean_catalog_base, clean_orig_base,
        "-o", "-N"
    ]

    passfile_path = None
    env = os.environ.copy()
    env["LC_ALL"] = "C"
    env["PYTHONUNBUFFERED"] = "1"

    try:
        if password:
            with tempfile.NamedTemporaryFile(mode="w", delete=False) as pf:
                pf.write(f"{password}\n")
                passfile_path = pf.name
            cmd.extend(["-K", f"passfile:{passfile_path}"])

        if log_callback:
            log_callback(f"[System]: Starte dar_manager (Versuch mit Passfile: {bool(password)})...")

        # stderr=subprocess.STDOUT führt Fehler & normale Ausgaben in EINEM Stream zusammen
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=True,
            bufsize=0,  # Komplett ungepuffert
            env=env
        )

        # Liest Zeichen/Zeilen ungepuffert aus, damit nichts im Buffer hängen bleibt
        if process.stdout:
            while True:
                line = process.stdout.readline()
                if not line and process.poll() is not None:
                    break
                clean_line = line.strip()
                if clean_line and log_callback:
                    log_callback(f"[dar]: {clean_line}")

        process.wait()
        return process.returncode == 0

    except Exception as e:
        if log_callback:
            log_callback(f"[FEHLER]: Prozess konnte nicht ausgeführt werden: {e}")
        return False

    finally:
        if passfile_path and os.path.exists(passfile_path):
            try:
                os.unlink(passfile_path)
            except OSError:
                pass
