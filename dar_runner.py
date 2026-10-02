#!/usr/bin/env python3
"""
DAR Explorer V2 - Reparierter und stabilisierter pexpect-Runner.
Stellt die Grundfunktionalität der Extraktion ohne Typen-Konflikte wieder her.
"""

from __future__ import annotations
import os
import pexpect
import shlex
import re
import shutil
import time
from typing import Callable


class DarProcessRunner:
    def __init__(self, cmd: list[str], slice_lager_path: str):
        """
        :param cmd: Die Argumenten-Liste für DAR (z.B. ["dar", "-x", ...])
        :param slice_lager_path: Der Pfad zum Band-/Schnipsel-Lager (MUSS explizit übergeben werden!)
        """
        self.cmd = cmd
        self.slice_lager_path = os.path.expanduser(slice_lager_path)
        self.child = None

    def run(self, line_callback: Callable[[str], None], interaction_callback: Callable[[str], str | None]) -> int:
        cmd_string = " ".join(shlex.quote(x) for x in self.cmd)

        env = os.environ.copy()
        env["LC_ALL"] = "C"
        env["LANG"] = "C"

        try:
            # Startet DAR im kontrollierten Pseudoterminal
            self.child = pexpect.spawn(cmd_string, env=env, encoding="utf-8", timeout=None)

            # Eindeutige, saubere Trennmuster
            patterns = [
                r"\[return\s*=\s*YES\s*\|\s*Esc\s*=\s*NO\]",  # 0: Slice-Wechsel-Aufforderung
                r"(?i)password|passwort:",                    # 1: Passwortabfragen
                r"(?i)overwrite|ignore|exist\?",              # 2: Datei-Konflikte
                r"\r\n|\n",                                   # 3: Zeilenumbrüche für normales Loggen
                pexpect.EOF                                   # 4: Prozessende
            ]

            while True:
                index = self.child.expect(patterns)

                # self.child.before ist GARANTIERT ein reiner Python-String!
                output_text = self.child.before if self.child.before else ""

                if index == 0:
                    # SLICE-WECHSEL ERKANNT!
                    prompt_text = output_text + " [return = YES | Esc = NO]"

                    # Dateiname extrahieren (z.B. testarchiv.8.dar)
                    match = re.search(r"([\w\.\-]+?\.(\d+)\.dar)", prompt_text)
                    if match:
                        missing_filename = match.group(1)
                        source_candidate = os.path.join(self.slice_lager_path, missing_filename)
                        target_destination = os.path.join(os.getcwd(), missing_filename)

                        line_callback(f"[DAR-Tool] Automatische Suche nach: {missing_filename}...")

                        # 1. Automatisch im Lager nachsehen
                        if os.path.exists(source_candidate):
                            line_callback(f"[DAR-Tool] Slice autonom gefunden! Kopiere nach {target_destination}...")
                            shutil.copy(source_candidate, target_destination)

                            # Sichere Festplatten-Flushes
                            try:
                                os.sync()
                            except AttributeError:
                                pass
                            time.sleep(0.2)

                            # Sofortige automatische Bestätigung an DAR senden
                            self.child.sendline("")
                            continue

                    # 2. Fallback zur GUI, falls Datei nicht im Lager liegt
                    user_response = interaction_callback(prompt_text)

                    if user_response is None:
                        self.terminate()
                        return -1

                    self.child.sendline(user_response)

                elif index == 1 or index == 2:
                    # Passwort oder Überschreiben-Konflikt
                    user_response = interaction_callback(output_text)
                    if user_response is None:
                        self.terminate()
                        return -1
                    self.child.sendline(user_response)

                elif index == 3:
                    # Normaler Zeilenumbruch -> Zeile ins Log schreiben
                    line = output_text.strip()
                    if line:
                        line_callback(line)

                elif index == 4:
                    # EOF (Prozess fertig)
                    if output_text.strip():
                        line_callback(output_text.strip())
                    break

            self.child.close()
            return self.child.exitstatus if self.child.exitstatus is not None else 0

        except Exception as exc:
            self.terminate()
            raise exc

    def terminate(self):
        if self.child and self.child.isalive():
            self.child.close(force=True)
