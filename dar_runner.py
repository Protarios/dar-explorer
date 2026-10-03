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

        archive_dir = os.getcwd()
        for arg in self.cmd:
            if arg.endswith(".dar"):
                potential_dir = os.path.abspath(os.path.dirname(os.path.expanduser(arg)))
                if os.path.exists(potential_dir):
                    archive_dir = potential_dir
                    break

        env = os.environ.copy()
        env["LC_ALL"] = "C"
        env["LANG"] = "C"

        # SICHERHEITS-BREMSE: Merkt sich die letzte Slice und wie oft sie angefordert wurde
        self.last_requested_slice = None
        self.slice_request_count = 0

        try:
            self.child = pexpect.spawn(cmd_string, env=env, encoding="utf-8", timeout=None, echo=False)

            patterns = [
                r"\[return\s*=\s*YES\s*\|\s*Esc\s*=\s*NO\]",
                r"(?i)password|passwort:",
                r"(?i)overwrite|ignore|exist\?",
                r"\r\n|\n",
                pexpect.EOF
            ]

            buffer = ""

            while True:
                index = self.child.expect(patterns)
                output_text = self.child.before if self.child.before else ""

                if index == 0:
                    # SLICE-WECHSEL ERKANNT!
                    prompt_text = output_text + " [return = YES | Esc = NO]"

                    match = re.search(r"([\w\.\-]+?\.(\d+)\.dar)", prompt_text)
                    if match:
                        missing_filename = match.group(1)
                        slice_num = int(match.group(2))

                        # Wir ermitteln das Verzeichnis, in dem DAR gerade operiert (z.B. /tmp/dar-explorer)
                        target_dir = os.path.abspath(os.getcwd())
                        if "dar-explorer" in target_dir.lower() or target_dir.startswith("/tmp"):
                            # TAPE-MODUS ERKANNT (Wir arbeiten in /tmp)
                            source_candidate = os.path.join(self.slice_lager_path, missing_filename)
                            target_destination = os.path.join(target_dir, missing_filename)

                            # AUTOCLEAN: Alte temporäre Datenslices entfernen, um SSD-Platz zu sparen!
                            try:
                                for file in os.listdir(target_dir):
                                    # Finde alle .dar Dateien im Arbeitsverzeichnis
                                    if file.endswith(".dar") and file != missing_filename:
                                        # WICHTIG: Schütze Teil 1 (Dummy) und die allerletzte Katalog-Slice (z.B. Teil 99)
                                        # Wir prüfen, ob die Dateinummer kleiner ist als die aktuell geladene Katalog-Slice
                                        f_match = re.search(r"\.(\d+)\.dar$", file)
                                        if f_match:
                                            f_num = int(f_match.group(1))
                                            if f_num != 1 and f_num < slice_num:
                                                file_to_remove = os.path.join(target_dir, file)
                                                if os.path.exists(file_to_remove):
                                                    os.remove(file_to_remove)
                                                    line_callback(f"[DAR-Tool] Bereinigung: Alte temporäre Slice {file} gelöscht.")
                            except Exception as e:
                                line_callback(f"[DAR-Tool] Fehler bei der automatischen Bereinigung: {e}")

                            line_callback(f"[DAR-Tool] Erwarte Slice {slice_num} vom Band. Bitte Tape einlegen...")

                            # 2. GUI Fallback aufrufen, damit der User den Bandwechsel bestätigen kann
                            user_response = interaction_callback(prompt_text)
                            if user_response is None:
                                self.terminate()
                                return -1

                            # Nach dem Klick auf "Ja" prüfen wir, ob die Datei JETZT auf dem frisch gemounteten Band liegt
                            if os.path.exists(source_candidate):
                                line_callback(f"[DAR-Tool] Slice auf eingelegtem Tape gefunden! Kopiere auf SSD...")
                                try:
                                    shutil.copy(source_candidate, target_destination)
                                    os.sync()
                                    line_callback(f"[DAR-Tool] Transfer abgeschlossen. Reiche Daten an DAR weiter.")
                                except Exception as e:
                                    line_callback(f"[DAR-Tool] Kopierfehler: {e}")

                                time.sleep(0.2)
                                self.child.send("\n")
                                buffer = ""
                                continue
                        else:
                            # STANDARD-MODUS (Lokale Festplatte, kein Kopieren/Löschen nötig)
                            source_candidate = os.path.join(self.slice_lager_path, missing_filename)
                            if os.path.exists(source_candidate):
                                shutil.copy(source_candidate, target_destination)
                                os.sync()
                                self.child.send("\n")
                                buffer = ""
                                continue

                    # Normaler Fallback, falls Datei nirgendwo existiert
                    user_response = interaction_callback(prompt_text)
                    if user_response is None:
                        self.terminate()
                        return -1
                    if user_response == "":
                        self.child.send("\n")
                    else:
                        self.child.send(f"{user_response}\n")
                    buffer = ""

                elif index == 1 or index == 2:
                    user_response = interaction_callback(output_text)
                    if user_response is None:
                        self.terminate()
                        return -1
                    if user_response == "":
                        self.child.send("\n")
                    else:
                        self.child.send(f"{user_response}\n")

                elif index == 3:
                    line = output_text.strip()
                    if line:
                        line_callback(line)

                elif index == 4:
                    if output_text.strip():
                        line_callback(output_text.strip())
                    break

            self.child.close()
            return self.child.exitstatus if self.child.exitstatus is not None else 0

        except Exception as exc:
            self.terminate()
            raise exc
