#!/usr/bin/env python3
"""
DAR Explorer - Neuer Runner für dar_manager & Tape-Operationen.
Nutzt subprocess statt pexpect für maximale Stabilität ohne Hacky-Parsen.
"""

from __future__ import annotations
import subprocess
from pathlib import Path
from typing import Callable, List, Optional


class DarManagerRunner:
    def __init__(self, dmd_path: str, dar_manager_bin: str = "dar_manager"):
        self.dmd_path = Path(dmd_path)
        self.bin = dar_manager_bin

    def create_database(()) -> bool:
        """Erstellt eine neue, leere .dmd Datenbankdatei."""
        self.dmd_path.parent.mkdir(parents=True, exist_ok=True)
        cmd = [self.bin, "-N", "-C", str(self.dmd_path)]

        res = subprocess.run(cmd, capture_output=True, text=True)
        return res.returncode == 0

    def add_archive(self, archive_basename: str, password: Optional[str] = None) -> bool:
        """Fügt ein Archiv zur DMD-Datenbank hinzu."""
        cmd = [self.bin, "-N", "-B", str(self.dmd_path), "-A", archive_basename]
        if password:
            cmd.extend(["-K", f"pass:{password}"])

        res = subprocess.run(cmd, capture_output=True, text=True)
        return res.returncode == 0

    def restore(
        self,
        target_dir: str,
        password: Optional[str] = None,
        file_pattern: Optional[str] = None,
        line_callback: Optional[Callable[[str], None]] = None
    ) -> bool:
        """
        Führt den Restore aus und streamt Log-Zeilen in Echtzeit an die GUI.
        """
        Path(target_dir).mkdir(parents=True, exist_ok=True)
        cmd = [self.bin, "-N", "-B", str(self.dmd_path), "-r", "-R", target_dir]

        if password:
            cmd.extend(["-K", f"pass:{password}"])
        if file_pattern:
            cmd.extend(["-g", file_pattern])

        try:
            # Subprocess mit Pipe für Echtzeit-Ausgabe
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1
            )

            # Zeile für Zeile an die GUI weiterleiten
            if process.stdout:
                for line in iter(process.stdout.readline, ''):
                    if line_callback and line.strip():
                        line_callback(line.strip())

            process.wait()
            return process.returncode == 0

        except Exception as e:
            if line_callback:
                line_callback(f"[FEHLER] Fehler bei der Ausführung: {e}")
            return False
