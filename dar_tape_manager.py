import shutil
import subprocess
from pathlib import Path
from typing import Optional, List, Callable
import pexpect

class DarTapeManager:
    """
    Manager für dar_manager im Tape-Modus.
    Verwendet konsequent pexpect, um interaktive Passwort-Abfragen von dar/dar_manager
    zu bedienen, ohne temporäre Dateien oder Workarounds im RAM.
    """
    def __init__(self, dmd_path: str, dar_bin: str = "dar", dar_manager_bin: str = "dar_manager"):
        self.dmd_path = Path(dmd_path).resolve()

        self.dar_bin = shutil.which(dar_bin) or dar_bin
        self.bin = shutil.which(dar_manager_bin) or dar_manager_bin

        print(f"[DEBUG] DarTapeManager initialized with pexpect. Bin: {self.bin}")

    def _run_with_pexpect(self, cmd: List[str], password: Optional[str] = None, line_callback: Optional[Callable[[str], None]] = None) -> bool:
        """
        Führt einen Befehl über pexpect aus, streamt die Ausgabe sofort live
        und fängt Passwort-Prompts ab, falls welche auftauchen.
        """
        cmd_str = " ".join(cmd)
        if line_callback:
            line_callback(f"[EXEC]: {cmd_str}")

        try:
            child = pexpect.spawn(
                cmd[0],
                cmd[1:],
                encoding="utf-8",
                timeout=None,
                env={"LC_ALL": "C", "LANG": "C"}
            )

            # Muster für Prompts oder normalen Zeilenumbruch / Ende
            patterns = [
                r"requires a password:",
                r"password:",
                r"Enter password:",
                r"\r?\n",         # Jede normale Zeile
                pexpect.EOF
            ]

            buffer = ""
            while True:
                index = child.expect(patterns, timeout=120)

                # Was vor dem Treffer ausgegeben wurde
                if child.before:
                    buffer += child.before

                if index == 3:  # Zeilenumbruch gefunden -> Zeile ausgeben
                    if "\n" in buffer:
                        lines = buffer.split("\n")
                        for line in lines[:-1]:
                            cleaned = line.strip()
                            if cleaned and line_callback:
                                line_callback(f"[dar_manager]: {cleaned}")
                        buffer = lines[-1]
                elif index in [0, 1, 2]:  # Passwort-Prompt erkannt
                    if password:
                        child.sendline(password)
                        if line_callback:
                            line_callback("[dar_manager]: [Passwort automatisch übergeben]")
                    else:
                        child.close(force=True)
                        if line_callback:
                            line_callback("[FEHLER] dar/dar_manager verlangt ein Passwort, aber keines übergeben.")
                        return False
                elif index == 4:  # EOF (Prozess beendet)
                    if buffer.strip():
                        for line in buffer.splitlines():
                            cleaned = line.strip()
                            if cleaned and line_callback:
                                line_callback(f"[dar_manager]: {cleaned}")
                    break

            child.wait()
            return child.exitstatus == 0

            return child.exitstatus == 0

        except Exception as e:
            if line_callback:
                line_callback(f"[FEHLER] Pexpect Exception: {e}")
            return False

    def create_database(self, line_callback: Optional[Callable[[str], None]] = None) -> bool:
        """Erstellt eine neue .dmd-Datenbank in der RAM-Disk."""
        self.dmd_path.parent.mkdir(parents=True, exist_ok=True)

        # Falls eine alte Instanz in der RAM-Disk liegt, sauber entfernen
        if self.dmd_path.exists():
            try:
                self.dmd_path.unlink()
            except Exception:
                pass

        cmd = [self.bin, "-N", "-C", str(self.dmd_path)]

        if line_callback:
            line_callback(f"[EXEC]: {' '.join(cmd)}")

        try:
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                text=True,
                bufsize=1,
                env={"LC_ALL": "C", "LANG": "C"}
            )

            if process.stdout:
                for line in iter(process.stdout.readline, ''):
                    clean = line.strip()
                    if clean and line_callback:
                        line_callback(f"[dar_manager]: {clean}")

            process.wait()
            return process.returncode == 0
        except Exception as e:
            if line_callback:
                line_callback(f"[FEHLER]: Konnte Datenbank nicht in der RAM-Disk erstellen: {e}")
            return False

    def add_archive(self, archive_basename: str, password: Optional[str] = None, line_callback: Optional[Callable[[str], None]] = None) -> bool:
        """Fügt ein Band-Archiv zur DMD-Datenbank hinzu (vollständig pexpect-gesichert im RAM)."""
        cmd = [self.bin, "-B", str(self.dmd_path), "-A", archive_basename]
        return self._run_with_pexpect(cmd, password=password, line_callback=line_callback)

def restore_files(
        self,
        target_dir: str,
        password: Optional[str] = None,
        file_patterns: Optional[List[str]] = None,
        restore_ownership: bool = True,
        line_callback: Optional[Callable[[str], None]] = None
    ) -> bool:
        """Stellt Dateien über pexpect aus der RAM-Disk-Datenbank wieder her."""
        Path(target_dir).mkdir(parents=True, exist_ok=True)

        cmd = [self.bin, "-N", "-B", str(self.dmd_path)]

        # Jedes dar-Argument sauber über ein eigenes -o übergeben
        cmd.extend(["-o", "-R", "-o", target_dir])

        if not restore_ownership:
            cmd.extend(["-o", "-O"])

        if password:
            cmd.extend(["-o", "-K", "-o", password])

        cmd.append("-r")

        if file_patterns:
            for p in file_patterns:
                clean_p = p.lstrip("/")
                if clean_p:
                    cmd.append(clean_p)

        return self._run_with_pexpect(cmd, password=password, line_callback=line_callback)
