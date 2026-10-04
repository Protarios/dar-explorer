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
        Führt einen Befehl über pexpect aus, fängt Passwort-Prompts ab und
        leitet die Ausgabe Zeile für Zeile an das Callback weiter.
        """
        cmd_str = " ".join(cmd)
        print(f"[DEBUG-PEXPECT] Executing: {cmd_str}")

        try:
            # Spawn mit pexpect
            child = pexpect.spawn(
                cmd[0],
                cmd[1:],
                encoding="utf-8",
                timeout=None,
                env={"LC_ALL": "C", "LANG": "C"}
            )

            # Muster, nach denen dar/dar_manager typischerweise nach einem Passwort fragen
            password_patterns = [
                r"requires a password:",
                r"password:",
                r"Enter password:",
                pexpect.TIMEOUT,
                pexpect.EOF
            ]

            while True:
                # Wir warten auf entweder Text, einen Passwort-Prompt, Timeout oder das Ende
                index = child.expect(password_patterns, timeout=120)

                # Gib eventuell aufgelaufene Ausgabe aus / ans Callback
                if child.before:
                    for line in child.before.splitlines():
                        cleaned = line.strip()
                        if cleaned and line_callback:
                            line_callback(cleaned)

                if index == 0 or index == 1 or index == 2:
                    # Passwort-Prompt erkannt!
                    if password:
                        child.sendline(password)
                    else:
                        # Kein Passwort übergeben, aber eins gefordert -> abbrechen
                        child.close(force=True)
                        if line_callback:
                            line_callback("[FEHLER] dar/dar_manager verlangt ein Passwort, aber keines übergeben.")
                        return False
                elif index == 3: # TIMEOUT
                    if line_callback:
                        line_callback("[FEHLER] Zeitüberschreitung bei der Befehlsausführung.")
                    child.close(force=True)
                    return False
                elif index == 4: # EOF (Prozess beendet)
                    break

            child.wait()
            return child.exitstatus == 0

        except Exception as e:
            if line_callback:
                line_callback(f"[FEHLER] Pexpect Exception: {e}")
            return False

    def create_database(self, line_callback: Optional[Callable[[str], None]] = None) -> bool:
        """Erstellt eine neue, leere .dmd-Datenbankdatei."""
        self.dmd_path.parent.mkdir(parents=True, exist_ok=True)
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
                line_callback(f"[FEHLER]: Konnte Datenbank nicht erstellen: {e}")
            return False

    def add_archive(self, archive_basename: str, password: Optional[str] = None, line_callback: Optional[Callable[[str], None]] = None) -> bool:
        """Fügt ein Band-Archiv zur DMD-Datenbank hinzu (vollständig pexpect-gesichert)."""
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
        """Stellt Dateien über pexpect wieder her."""
        Path(target_dir).mkdir(parents=True, exist_ok=True)

        cmd = [self.bin, "-N", "-B", str(self.dmd_path)]
        cmd.extend(["-o", f"-R {target_dir}"])

        if not restore_ownership:
            cmd.extend(["-o", "-O"])

        cmd.append("-r")

        if file_patterns:
            for p in file_patterns:
                clean_p = p.lstrip("/")
                if clean_p:
                    cmd.append(clean_p)

        return self._run_with_pexpect(cmd, password=password, line_callback=line_callback)
