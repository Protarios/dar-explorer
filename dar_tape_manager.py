import subprocess
from pathlib import Path
from typing import List, Optional


class DarTapeManager:
    def __init__(self, dmd_path: str, dar_manager_bin: str = "dar_manager"):
        self.dmd_path = Path(dmd_path)
        self.bin = dar_manager_bin

    def create_database(self) -> bool:
        """Erstellt eine neue, leere .dmd Datenbankdatei."""
        self.dmd_path.parent.mkdir(parents=True, exist_ok=True)
        cmd = [self.bin, "-N", "-C", str(self.dmd_path)]

        result = subprocess.run(cmd, capture_output=True, text=True)
        return result.returncode == 0

    def add_archive(self, archive_basename: str, password: Optional[str] = None) -> bool:
        """
        Fügt ein (Band-)Archiv zur DMD-Datenbank hinzu.
        Nutzt das bereits bekannte Passwort direkt, um doppelte Abfragen zu vermeiden.
        """
        cmd = [self.bin, "-B", str(self.dmd_path)]

        # Wenn wir ein Passwort haben, übergeben wir es direkt via -o,
        # damit dar_manager/dar gar nicht erst interaktiv nachfragen müssen.
        if password:
            cmd.extend(["-o", f"-K pass:{password}"])

        cmd.extend(["-A", archive_basename])

        print(f"[DEBUG] dar_manager add_archive cmd: {' '.join(cmd)}")

        result = subprocess.run(cmd, capture_output=True, text=True, env={"LC_ALL": "C", "LANG": "C"})

        if result.returncode != 0:
            print(f"[ERROR] Fehler beim Import: {result.stderr or result.stdout}")
            return False

        return True

    def list_files(self) -> List[str]:
        """Listet den Inhalt der Datenbank auf."""
        cmd = [self.bin, "-N", "-B", str(self.dmd_path), "-l"]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            return result.stdout.splitlines()
        return []

    def restore_files(
        self,
        target_dir: str,
        password: Optional[str] = None,
        file_patterns: Optional[List[str]] = None,
        restore_ownership: bool = True
    ) -> bool:
        """
        Stellt Dateien aus den in der DMD erfassten Archiven im Zielverzeichnis wieder her.
        """
        Path(target_dir).mkdir(parents=True, exist_ok=True)
        cmd = [self.bin, "-N", "-B", str(self.dmd_path)]

        # Zielverzeichnis und Parameter über -o an dar übergeben
        cmd.extend(["-o", f"-R {target_dir}"])

        if not restore_ownership:
            cmd.extend(["-o", "-O"])

        if password:
            cmd.extend(["-o", f"-K pass:{password}"])

        cmd.append("-r")

        if file_patterns:
            for p in file_patterns:
                clean_p = p.lstrip("/")
                if clean_p:
                    cmd.append(clean_p)

        result = subprocess.run(cmd, capture_output=True, text=True)
        return result.returncode == 0
