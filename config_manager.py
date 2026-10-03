#!/usr/bin/env python3
"""
DAR Explorer V2 - Konfigurations-Manager.
Speichert und lädt Einstellungen dauerhaft in einer JSON-Datei.
"""

import os
import json
from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "dar-explorer"
CONFIG_FILE = CONFIG_DIR / "config.json"

DEFAULT_CONFIG = {
    "slice_lager_path": str(Path.home() / "mein_band_archiv")
}

def load_config() -> dict:
    """Lädt die Konfiguration oder erstellt die Standardwerte, falls nicht vorhanden."""
    if not CONFIG_FILE.exists():
        save_config(DEFAULT_CONFIG)
        return DEFAULT_CONFIG
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            config = json.load(f)
            for key, val in DEFAULT_CONFIG.items():
                if key not in config:
                    config[key] = val
            return config
    except Exception:
        return DEFAULT_CONFIG

def save_config(config_data: dict):
    """Speichert die übergebenen Konfigurationsdaten sicher ab."""
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config_data, f, indent=4, ensure_ascii=False)
    except Exception as e:
        print(f"[Config] Fehler beim Speichern der Konfiguration: {e}")
