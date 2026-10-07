#!/usr/bin/env python3
"""Persistente DAR-Explorer-Konfiguration.

Teilarchiv-Lager ist bewusst keine Einstellung mehr: Slices werden über die
normalen Datei-/Verzeichnisdialoge und DARs interaktive Medienabfragen behandelt.
"""

import json
from pathlib import Path

CONFIG_DIR = Path.home() / ".config" / "dar-explorer"
CONFIG_FILE = CONFIG_DIR / "config.json"
DEFAULT_CONFIG: dict = {}


def load_config() -> dict:
    if not CONFIG_FILE.exists():
        return {}
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {}
        # Alte Einstellung beim Laden automatisch entfernen.
        data.pop("slice_lager_path", None)
        return data
    except Exception:
        return {}


def save_config(config_data: dict):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    clean = dict(config_data or {})
    clean.pop("slice_lager_path", None)
    CONFIG_FILE.write_text(json.dumps(clean, indent=4, ensure_ascii=False), encoding="utf-8")
