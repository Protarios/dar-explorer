#!/usr/bin/env python3
"""
DAR Explorer V2 - Schnittstelle für dar_manager-Abfragen.
Liest registrierte Archive und extrahiert die Dateilisten aus der DMD-Datenbank.
"""

import subprocess
import os
import re

def get_registered_archives(db_path: str) -> list[dict]:
    """
    Liest alle in der DMD-Datenbank registrierten Archive aus.
    Gibt eine Liste von Dictionaries mit Nummer, Pfad und Basisname zurück.
    """
    if not os.path.exists(db_path):
        return []

    cmd = ["dar_manager", "-B", db_path, "-l"]
    env = os.environ.copy()
    env["LC_ALL"] = "C"

    try:
        res = subprocess.run(cmd, capture_output=True, text=True, env=env, check=True)
        lines = res.stdout.splitlines()

        archives = []
        parsing_started = False

        for line in lines:
            if "archive #" in line.lower() or "---" in line:
                parsing_started = True
                continue
            if not parsing_started:
                continue

            # Zeilen-Format: " 1 | /path/to/archive | basename"
            parts = [p.strip() for p in line.split("|")]
            if len(parts) >= 3 and parts[0].isdigit():
                archives.append({
                    "id": int(parts[0]),
                    "path": parts[1],
                    "basename": parts[2]
                })
        return archives
    except Exception as e:
        print(f"[Manager-Parser] Fehler beim Auslesen der Archive: {e}")
        return []

def add_catalog_to_db(db_path: str, catalog_path: str, archive_original_name: str) -> bool:
    """
    Fügt einen isolierten Katalog der dar_manager-Datenbank hinzu.
    Berechnet dabei das mathematische Raster für die Slice-Zuordnung.
    """
    # Regulären Basisnamen extrahieren
    clean_base = re.sub(r"\.\d+\.dar$", "", os.path.basename(catalog_path))
    clean_orig = re.sub(r"\.\d+\.dar$", "", archive_original_name)

    # dar_manager -B <db> -A <katalog_basis> <original_name_im_lager>
    cmd = ["dar_manager", "-B", db_path, "-A", catalog_path.replace(".1.dar", ""), clean_orig]

    try:
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except subprocess.CalledProcessError:
        return False
