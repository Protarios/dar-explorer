import pexpect
from pathlib import Path
from typing import List, Optional, Callable

class DarManager:
    def __init__(self, path: str, password: Optional[str] = None, is_catalog: bool = False):
        """
        path: Entweder der Pfad zu einem Archiv/Slice ODER direkt zu einem isolierten Katalog.
        is_catalog: True, wenn der User direkt eine .dar-Katalogdatei übergeben hat.
        """
        self.path = Path(path)
        self.password = password
        self.is_catalog = is_catalog
        self.bin = "/usr/bin/dar"

    def list_contents(self, line_callback: Optional[Callable[[str], None]] = None) -> List[str]:
        """
        Listet den Inhalt auf.
        Wenn es ein Katalog ist oder das volle Archiv, liest dar das aus.
        """
        cmd = [self.bin, "-l", str(self.path)]

        if self.password:
            cmd.extend(["-K", f"pass:{self.password}"])

        output_lines = []

        def internal_callback(line: str):
            output_lines.append(line)
            if line_callback:
                line_callback(line)

        success = self._run_with_pexpect(cmd, line_callback=internal_callback)
        if not success:
            raise RuntimeError("Fehler beim Auslesen des Archivs / Katalogs.")

        return output_lines

    def generate_isolated_catalog(self, output_catalog_path: str, line_callback: Optional[Callable[[str], None]] = None) -> bool:
        """
        Erstellt einen isolierten Katalog (-C) aus dem Archiv (benötigt Zugriff auf das letzte Slice).
        """
        cmd = [self.bin, "-C", output_catalog_path, "-A", str(self.path)]

        if self.password:
            cmd.extend(["-K", f"pass:{self.password}"])

        return self._run_with_pexpect(cmd, line_callback=line_callback)

    def _run_with_pexpect(self, cmd: List[str], line_callback: Optional[Callable[[str], None]] = None) -> bool:
        """Führt den Befehl aus und bedient Passwort- oder Slice-Prompts via pexpect."""
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

            patterns = [
                r"requires a password:",
                r"password:",
                r"Enter password:",
                r"press.*enter.*slice",
                r"\r?\n",
                pexpect.EOF
            ]

            buffer = ""
            while True:
                index = child.expect(patterns, timeout=120)

                if child.before:
                    buffer += child.before

                if index in [0, 1, 2]:  # Passwort-Abfrage
                    if self.password:
                        child.sendline(self.password)
                        if line_callback:
                            line_callback("[dar]: [Passwort automatisch übergeben]")
                    else:
                        child.close(force=True)
                        if line_callback:
                            line_callback("[FEHLER] Passwort erforderlich, aber keines angegeben.")
                        return False
                elif index == 3:  # Slice-Wechsel / Band-Prompt
                    if line_callback:
                        line_callback("[dar]: [Nächster Slice / Medienspeicher angefordert - Bestätige]")
                    child.sendline("")
                elif index == 4:  # Zeilenumbruch im Output
                    if "\n" in buffer:
                        lines = buffer.split("\n")
                        for line in lines[:-1]:
                            cleaned = line.strip()
                            if cleaned and line_callback:
                                line_callback(f"[dar]: {cleaned}")
                        buffer = lines[-1]
                elif index == 5:  # Prozessende
                    if buffer.strip():
                        for line in buffer.splitlines():
                            cleaned = line.strip()
                            if cleaned and line_callback:
                                line_callback(f"[dar]: {cleaned}")
                    break

            child.wait()
            return child.exitstatus == 0

        except Exception as e:
            if line_callback:
                line_callback(f"[FEHLER] Pexpect Exception: {e}")
            return False
