from __future__ import annotations

from pathlib import Path

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk


UNITS = {
    "KB": 10**3,
    "MB": 10**6,
    "GB": 10**9,
    "TB": 10**12,
    "KiB": 2**10,
    "MiB": 2**20,
    "GiB": 2**30,
    "TiB": 2**40,
}


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
                self.password_entry = Gtk.PasswordEntry()
                self.password_entry.set_placeholder_text("Optional – leer lassen für interaktive Abfrage")
                box.append(Gtk.Label(label="Passwort (optional):", xalign=0))
                box.append(self.password_entry)

            if operation in {"isolate", "repair", "xform"}:
                size_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
                box.append(Gtk.Label(label="Slice-Größe:", xalign=0))
                self.size_entry = Gtk.Entry()
                self.size_entry.set_text("4")
                self.size_entry.set_width_chars(10)
                size_box.append(self.size_entry)
                self.unit = Gtk.DropDown.new_from_strings(list(UNITS))
                self.unit.set_selected(6 if operation == "xform" else 5)  # GiB / MiB
                size_box.append(self.unit)
                box.append(size_box)
                self.size_label = Gtk.Label(label="", xalign=0)
                self.size_label.add_css_class("dim-label")
                box.append(self.size_label)
                self.size_entry.connect("changed", self._update_size_label)
                self.unit.connect("notify::selected", self._update_size_label)
                self._update_size_label()

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

    def _update_size_label(self, *_):
        if not hasattr(self, "size_label"):
            return
        try:
            value = float(self.size_entry.get_text().replace(",", "."))
            names = list(UNITS)
            unit = names[self.unit.get_selected()]
            bytes_value = int(value * UNITS[unit])
            self.size_label.set_text(f"= {bytes_value:,} Bytes".replace(",", "."))
        except Exception:
            self.size_label.set_text("Ungültige Slice-Größe")

    def _size_bytes(self):
        value = float(self.size_entry.get_text().replace(",", "."))
        unit = list(UNITS)[self.unit.get_selected()]
        result = int(value * UNITS[unit])
        if result <= 0:
            raise ValueError("Die Slice-Größe muss größer als 0 sein.")
        return result

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
                if hasattr(self, "size_entry"):
                    kwargs["slice_size"] = self._size_bytes()
            self.on_run(self.operation, kwargs)
            self.close()
        except Exception as exc:
            err = Gtk.AlertDialog(message=str(exc))
            err.show(self)
