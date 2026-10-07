from __future__ import annotations

from pathlib import Path

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk

from .archive_create import SizeSelector




class Phase2OperationDialog(Gtk.Window):
    """Kleiner, wiederverwendbarer Dialog für Phase-2-DAR-Operationen."""

    def __init__(self, parent, operation: str, current_archive: str | None, on_run):
        titles = {
            "compare": "Archiv mit Dateisystem vergleichen",
            "isolate": "Katalog isolieren",
            "repair": "Unterbrochenes Archiv reparieren",
            "xform": "Slices mit dar_xform umformen",
            "test": "Archivintegrität prüfen",
        }
        super().__init__(title=titles[operation], transient_for=parent, modal=True)
        self.operation = operation
        self.on_run = on_run
        self.set_default_size(620, -1)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_margin_top(16); box.set_margin_bottom(16)
        box.set_margin_start(16); box.set_margin_end(16)
        self.set_child(box)

        info = Gtk.Label()
        info.set_wrap(True); info.set_xalign(0)
        descriptions = {
            "compare": "DAR vergleicht den Inhalt des Archivs mit einem vorhandenen Dateisystem. Neue Dateien im Dateisystem werden von DAR dabei nicht als Archivabweichung gewertet.",
            "isolate": "Erzeugt einen separaten isolierten Katalog. Das Quellarchiv wird nicht verändert.",
            "repair": "Erzeugt aus einem unterbrochenen Archiv eine reparierte Kopie. Das beschädigte/unterbrochene Original bleibt unverändert.",
            "xform": "Ändert ausschließlich die Slice-Struktur eines vorhandenen Archivs. Daten werden dabei nicht dekomprimiert oder entschlüsselt.",
            "test": "Prüft die interne Konsistenz des Archivs. Mit einem isolierten Katalog kann auch ein beschädigter interner Katalog umgangen werden.",
        }
        info.set_text(descriptions[operation])
        box.append(info)

        self.archive_entry = self._entry(box, "Archiv", current_archive or "")

        if operation == "compare":
            self.fs_entry = self._entry(box, "Dateisystemwurzel", str(Path.home()))
            self._browse_button(box, "Dateisystem wählen", self._choose_fs)
        elif operation == "test":
            self.catalog_entry = self._entry(box, "Optionaler isolierter Katalog", "")
            self._browse_button(box, "Katalog wählen", self._choose_catalog)
        else:
            default_name = self._default_destination(operation, current_archive)
            self.destination_entry = self._entry(box, "Zielarchiv", default_name)
            self._browse_button(box, "Zielverzeichnis wählen", self._choose_destination_dir)

            if operation in {"isolate", "repair"}:
                # Gtk.PasswordEntry besitzt in der verwendeten GTK4-Version
                # kein set_placeholder_text(). Für das optionale Passwort ist
                # ein normales Entry mit unsichtbarer Darstellung ausreichend.
                self.password_entry = Gtk.Entry()
                self.password_entry.set_visibility(False)
                self.password_entry.set_placeholder_text("Optional – leer lassen für interaktive Abfrage")
                self.password_entry.set_hexpand(True)
                box.append(Gtk.Label(label="Passwort (optional):", xalign=0))
                box.append(self.password_entry)

            if operation in {"isolate", "repair", "xform"}:
                box.append(Gtk.Label(label="Slice-Größe:", xalign=0))
                self.slice_size = SizeSelector()
                box.append(self.slice_size)

                box.append(Gtk.Label(label="1. Slicegröße (optional):", xalign=0))
                self.first_slice_size = SizeSelector()
                self.first_slice_size.set_size("")
                box.append(self.first_slice_size)

        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        buttons.set_halign(Gtk.Align.END)
        box.append(buttons)
        cancel = Gtk.Button(label="Abbrechen")
        run = Gtk.Button(label="Im Terminal ausführen")
        run.add_css_class("suggested-action")
        buttons.append(cancel); buttons.append(run)
        cancel.connect("clicked", lambda *_: self.close())
        run.connect("clicked", self._run_clicked)

    def _entry(self, box, label, value):
        box.append(Gtk.Label(label=label + ":", xalign=0))
        entry = Gtk.Entry()
        entry.set_text(value)
        entry.set_hexpand(True)
        box.append(entry)
        return entry

    def _browse_button(self, box, label, callback):
        b = Gtk.Button(label=label)
        b.set_halign(Gtk.Align.START)
        b.connect("clicked", callback)
        box.append(b)

    def _default_destination(self, operation, current):
        if not current:
            return str(Path.home() / "dar-phase2-result")
        p = Path(current)
        stem = p.name
        if "." in stem:
            stem = stem.rsplit(".", 1)[0]
        suffix = {"isolate": "_catalog", "repair": "_repaired", "xform": "_xform"}[operation]
        return str(p.parent / f"{stem}{suffix}")

    def _choose_fs(self, _button):
        dialog = Gtk.FileDialog(title="Dateisystemwurzel wählen")
        dialog.select_folder(self, None, self._finish_fs)

    def _finish_fs(self, dialog, result):
        try:
            f = dialog.select_folder_finish(result)
            if f and f.get_path():
                self.fs_entry.set_text(f.get_path())
        except Exception:
            pass

    def _choose_catalog(self, _button):
        dialog = Gtk.FileDialog(title="Isolierten Katalog auswählen")
        dialog.open(self, None, self._finish_catalog)

    def _finish_catalog(self, dialog, result):
        try:
            f = dialog.open_finish(result)
            if f and f.get_path():
                self.catalog_entry.set_text(f.get_path())
        except Exception:
            pass

    def _choose_destination_dir(self, _button):
        dialog = Gtk.FileDialog(title="Zielverzeichnis wählen")
        dialog.select_folder(self, None, self._finish_destination_dir)

    def _finish_destination_dir(self, dialog, result):
        try:
            f = dialog.select_folder_finish(result)
            if f and f.get_path():
                old = Path(self.destination_entry.get_text())
                self.destination_entry.set_text(str(Path(f.get_path()) / old.name))
        except Exception:
            pass

    def _run_clicked(self, _button):
        try:
            kwargs = {"archive": self.archive_entry.get_text().strip()}
            if not kwargs["archive"]:
                raise ValueError("Kein Archiv angegeben.")
            if self.operation == "compare":
                kwargs["filesystem_root"] = self.fs_entry.get_text().strip()
            elif self.operation == "test":
                kwargs["isolated_catalog"] = self.catalog_entry.get_text().strip() or None
            else:
                kwargs["destination"] = self.destination_entry.get_text().strip()
                if hasattr(self, "password_entry"):
                    kwargs["password"] = self.password_entry.get_text() or None
                if hasattr(self, "slice_size"):
                    kwargs["slice_size"] = int(self.slice_size.get_bytes_text())
                    first = self.first_slice_size.get_bytes_text()
                    kwargs["first_slice_size"] = int(first) if first else None
            self.on_run(self.operation, kwargs)
            self.close()
        except Exception as exc:
            err = Gtk.AlertDialog(message=str(exc))
            err.show(self)
