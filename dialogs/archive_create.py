from __future__ import annotations

from pathlib import Path
import json

from gi.repository import Gio, Gtk, GLib, Pango

from dar_backend.archive import ArchiveCreateOptions, build_create_argv, shell_command
from dar_backend.capabilities import supported_algorithms, dar_version
from config.profiles import save_profile, load_profile, list_profiles


class SizeSelector(Gtk.Box):
    """Compact size editor with an explicit decimal/binary unit dropdown.

    The widget keeps the exact value in bytes internally.  DAR receives the
    resulting byte count, so there is no ambiguity about whether MB/GB means
    powers of 1000 or powers of 1024.  Existing DAR strings such as 4G or
    700M are accepted when loading old profiles.
    """

    UNITS = [
        ("B", 1),
        ("KB", 10**3),
        ("MB", 10**6),
        ("GB", 10**9),
        ("TB", 10**12),
        ("KiB", 2**10),
        ("MiB", 2**20),
        ("GiB", 2**30),
        ("TiB", 2**40),
    ]

    def __init__(self, placeholder="optional"):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.set_hexpand(True)

        self.value = Gtk.SpinButton.new(
            Gtk.Adjustment(value=4, lower=0, upper=10**12, step_increment=1, page_increment=10),
            1,
            3,
        )
        self.value.set_numeric(True)
        self.value.set_hexpand(True)
        self.value.set_width_chars(10)
        self.value.set_tooltip_text("Größe als Zahl eingeben")
        self.append(self.value)

        self.unit = Gtk.DropDown.new_from_strings([u[0] for u in self.UNITS])
        self.unit.set_selected(6)  # GiB
        self.unit.set_size_request(105, -1)
        self.unit.set_tooltip_text("Einheit auswählen")
        self.append(self.unit)

        self.bytes_label = Gtk.Label()
        self.bytes_label.set_xalign(0)
        self.bytes_label.set_width_chars(22)
        self.bytes_label.add_css_class("dim-label")
        self.append(self.bytes_label)

        self.placeholder = placeholder
        self.value.connect("value-changed", self._changed)
        self.unit.connect("notify::selected", self._changed)
        self._update_label()

    def _factor(self):
        idx = self.unit.get_selected()
        return self.UNITS[idx][1] if idx < len(self.UNITS) else 1

    def _changed(self, *_args):
        self._update_label()

    def _update_label(self):
        raw = self.value.get_value()
        size = int(round(raw * self._factor()))
        if size <= 0:
            self.bytes_label.set_text("—")
            return
        self.bytes_label.set_text(f"= {size:,} Byte".replace(",", "."))

    def get_bytes_text(self):
        size = int(round(self.value.get_value() * self._factor()))
        return str(size) if size > 0 else ""

    def set_size(self, text):
        text = (text or "").strip()
        if not text:
            self.value.set_value(0)
            self._update_label()
            return

        # Old profiles used DAR suffixes.  Interpret them exactly as DAR 2.8.x
        # does by default (binary units), then represent the result explicitly
        # in bytes.
        import re
        m = re.fullmatch(r"(\d+(?:[.,]\d+)?)\s*([kKmMgGtT]?)", text)
        if not m:
            self.value.set_value(0)
            self._update_label()
            return
        number = float(m.group(1).replace(",", "."))
        suffix = m.group(2).upper()
        factors = {"": 1, "K": 2**10, "M": 2**20, "G": 2**30, "T": 2**40}
        total = int(round(number * factors[suffix]))

        # Prefer a clean integer representation in the most suitable binary
        # unit, falling back to bytes when it is not an exact multiple.
        for idx in range(8, -1, -1):
            factor = self.UNITS[idx][1]
            if total >= factor and total % factor == 0:
                self.unit.set_selected(idx)
                self.value.set_value(total / factor)
                self._update_label()
                return
        self.unit.set_selected(0)
        self.value.set_value(total)
        self._update_label()


class ArchiveCreateDialog(Gtk.Window):
    """Modern GTK4/DAR create dialog.

    The dialog only collects options. The actual DAR process is intentionally
    started by the main window in its VTE PTY.
    """

    def __init__(self, parent, on_create):
        super().__init__(title="DAR-Archiv erstellen", transient_for=parent, modal=True)
        self.parent = parent
        self.on_create = on_create
        self.set_default_size(760, 760)
        self.set_hide_on_close(True)
        self._build()

    def _row(self, label, widget):
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        box.set_margin_bottom(8)
        lab = Gtk.Label(label=label)
        lab.set_xalign(0)
        lab.set_width_chars(24)
        box.append(lab)
        widget.set_hexpand(True)
        box.append(widget)
        return box

    def _path_row(self, label, entry, select_folder=False):
        box = self._row(label, entry)
        button = Gtk.Button(label="…")
        button.set_tooltip_text("Auswählen")
        box.append(button)

        def picked(dialog, result):
            try:
                obj = (dialog.select_folder_finish(result) if select_folder else dialog.save_finish(result))
                if obj and obj.get_path():
                    entry.set_text(obj.get_path())
            except GLib.Error:
                pass

        if select_folder:
            button.connect("clicked", lambda *_: Gtk.FileDialog(title=label).select_folder(self, None, picked))
        else:
            def choose(*_):
                d = Gtk.FileDialog(title=label)
                d.set_initial_name(entry.get_text() or "backup")
                d.save(self, None, picked)
            button.connect("clicked", choose)
        return box

    def _build(self):
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.set_child(outer)

        scroller = Gtk.ScrolledWindow()
        scroller.set_vexpand(True)
        outer.append(scroller)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        content.set_margin_top(16); content.set_margin_bottom(16); content.set_margin_start(18); content.set_margin_end(18)
        scroller.set_child(content)

        title = Gtk.Label()
        title.set_markup("<big><b>Neues DAR-Archiv</b></big>")
        title.set_xalign(0)
        content.append(title)
        version = Gtk.Label(label=f"DAR: {dar_version()}")
        version.set_xalign(0); version.add_css_class("dim-label")
        content.append(version)

        self.source = Gtk.Entry()
        content.append(self._path_row("Quellverzeichnis", self.source, True))
        self.archive = Gtk.Entry()
        content.append(self._path_row("Archivbasis / Ziel", self.archive, False))

        frame = Gtk.Frame(label="Backup-Typ")
        b = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        b.set_margin_top(10); b.set_margin_bottom(10); b.set_margin_start(10); b.set_margin_end(10)
        frame.set_child(b); content.append(frame)
        self.full_radio = Gtk.CheckButton(label="Vollbackup")
        self.diff_radio = Gtk.CheckButton(label="Differential / inkrementelles Backup")
        self.diff_radio.set_group(self.full_radio)
        self.full_radio.set_active(True)
        b.append(self.full_radio); b.append(self.diff_radio)
        ref_row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.reference_kind = Gtk.DropDown.new_from_strings(["Bestehendes Archiv", "Isolierter Katalog"])
        ref_row.append(self._row("Referenztyp", self.reference_kind))
        self.reference = Gtk.Entry()
        ref_row.append(self._path_row("Referenzarchiv / isolierter Katalog", self.reference, False))
        b.append(ref_row)
        self.reference.set_sensitive(False)
        self.reference_kind.set_sensitive(False)
        self.diff_radio.connect("toggled", lambda w: (self.reference.set_sensitive(w.get_active()), self.reference_kind.set_sensitive(w.get_active())))

        enc = Gtk.Frame(label="Verschlüsselung")
        eb = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        eb.set_margin_top(10); eb.set_margin_bottom(10); eb.set_margin_start(10); eb.set_margin_end(10)
        enc.set_child(eb); content.append(enc)
        self.encrypt = Gtk.CheckButton(label="Archiv verschlüsseln")
        eb.append(self.encrypt)
        ciphers, _ = supported_algorithms()
        self.cipher = Gtk.DropDown.new_from_strings(ciphers)
        eb.append(self._row("Verfahren", self.cipher))
        note = Gtk.Label(label="Das Passwort wird bewusst nicht in die Konfiguration und nicht in die Kommandozeile geschrieben. DAR fragt es interaktiv im Terminal ab.")
        note.set_wrap(True); note.set_xalign(0); note.add_css_class("dim-label")
        eb.append(note)
        self.cipher.set_sensitive(False)
        self.encrypt.connect("toggled", lambda w: self.cipher.set_sensitive(w.get_active()))

        slice_frame = Gtk.Frame(label="Slicing")
        sb = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        sb.set_margin_top(10); sb.set_margin_bottom(10); sb.set_margin_start(10); sb.set_margin_end(10)
        slice_frame.set_child(sb); content.append(slice_frame)
        self.slice = Gtk.CheckButton(label="Archiv in Slices aufteilen")
        sb.append(self.slice)
        self.slice_size = SizeSelector()
        sb.append(self._row("Slicegröße", self.slice_size))
        self.first_slice = SizeSelector()
        self.first_slice.set_size("")
        sb.append(self._row("1. Slicegröße", self.first_slice))
        self.slice_size.set_sensitive(False); self.first_slice.set_sensitive(False)
        self.slice.connect("toggled", lambda w: (self.slice_size.set_sensitive(w.get_active()), self.first_slice.set_sensitive(w.get_active())))

        comp = Gtk.Frame(label="Kompression")
        cb = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        cb.set_margin_top(10); cb.set_margin_bottom(10); cb.set_margin_start(10); cb.set_margin_end(10)
        comp.set_child(cb); content.append(comp)
        _, compressors = supported_algorithms()
        self.compression = Gtk.DropDown.new_from_strings(["none"] + compressors)
        self.compression.set_selected(1)
        cb.append(self._row("Verfahren", self.compression))
        self.level = Gtk.SpinButton.new(Gtk.Adjustment(value=6, lower=0, upper=9, step_increment=1, page_increment=1), 1, 0)
        cb.append(self._row("Kompressionslevel", self.level))

        adv = Gtk.Expander(label="Erweiterte DAR-Optionen")
        ab = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        ab.set_margin_top(8); ab.set_margin_bottom(8); ab.set_margin_start(8); ab.set_margin_end(8)
        adv.set_child(ab); content.append(adv)
        self.empty_dirs = Gtk.CheckButton(label="Leere Verzeichnisse sichern"); self.empty_dirs.set_active(True)
        ab.append(self.empty_dirs)
        info = Gtk.Label(label="Hardlinks, Sparse-Dateien, ACLs und Extended Attributes werden entsprechend der DAR-Version und der verfügbaren Dateisystem-Unterstützung behandelt. Sie werden hier nicht durch erfundene bzw. versionsabhängige CLI-Schalter erzwungen.")
        info.set_wrap(True); info.set_xalign(0); info.add_css_class("dim-label")
        ab.append(info)
        self.hardlinks = Gtk.CheckButton(); self.hardlinks.set_active(True)
        self.sparse = Gtk.CheckButton(); self.sparse.set_active(True)
        self.xattrs = Gtk.CheckButton(); self.xattrs.set_active(True)
        self.acl = Gtk.CheckButton(); self.acl.set_active(True)
        self.numeric = Gtk.CheckButton(); self.numeric.set_active(False)

        bottom = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        bottom.set_margin_top(10); bottom.set_margin_bottom(10); bottom.set_margin_start(18); bottom.set_margin_end(18)
        outer.append(bottom)
        self.profile = Gtk.Button(label="Einstellungen speichern…")
        self.load_profile_btn = Gtk.Button(label="Einstellungen laden…")
        bottom.append(self.profile); bottom.append(self.load_profile_btn)
        spacer = Gtk.Box(); spacer.set_hexpand(True); bottom.append(spacer)
        cancel = Gtk.Button(label="Abbrechen")
        create = Gtk.Button(label="Archiv im Terminal erstellen")
        create.add_css_class("suggested-action")
        bottom.append(cancel); bottom.append(create)
        cancel.connect("clicked", lambda *_: self.close())
        create.connect("clicked", self._create_clicked)
        self.profile.connect("clicked", self._save_profile)
        self.load_profile_btn.connect("clicked", self._load_profile)

    def options(self) -> ArchiveCreateOptions:
        cipher_idx = self.cipher.get_selected()
        cipher = self.cipher.get_model().get_item(cipher_idx).get_string() if cipher_idx != Gtk.INVALID_LIST_POSITION else "aes256"
        comp_idx = self.compression.get_selected()
        comp = self.compression.get_model().get_item(comp_idx).get_string() if comp_idx != Gtk.INVALID_LIST_POSITION else "gzip"
        return ArchiveCreateOptions(
            source=self.source.get_text().strip(),
            archive=self.archive.get_text().strip(),
            reference=self.reference.get_text().strip() if self.diff_radio.get_active() else None,
            reference_is_catalog=(self.reference_kind.get_selected() == 1) if self.diff_radio.get_active() else False,
            encryption_enabled=self.encrypt.get_active(),
            encryption_algorithm=cipher,
            compression=comp,
            compression_level=int(self.level.get_value()),
            slice_enabled=self.slice.get_active(),
            slice_size=self.slice_size.get_bytes_text(),
            first_slice_size=self.first_slice.get_bytes_text(),
            empty_dirs=self.empty_dirs.get_active(),
            hard_links=self.hardlinks.get_active(),
            sparse_files=self.sparse.get_active(),
            xattrs=self.xattrs.get_active(),
            acl=self.acl.get_active(),
            numeric_owner=self.numeric.get_active(),
        )

    def _create_clicked(self, *_):
        try:
            opts = self.options()
            if not opts.source or not Path(opts.source).is_dir():
                raise ValueError("Das Quellverzeichnis existiert nicht.")
            if not opts.archive:
                raise ValueError("Bitte ein Archivziel angeben.")
            if opts.reference and not Path(opts.reference).exists():
                # DAR can resolve a basename whose first slice exists, but a
                # selected reference should still be a real file/path.
                raise ValueError("Das Referenzarchiv bzw. der isolierte Katalog existiert nicht.")
            argv = build_create_argv(opts)
            self.on_create(opts, argv, shell_command(argv))
            self.close()
        except Exception as exc:
            d = Gtk.AlertDialog(message="Archiv kann nicht erstellt werden", detail=str(exc))
            d.show(self)

    def _save_profile(self, *_):
        d = Gtk.FileDialog(title="DAR-Einstellungen speichern")
        d.set_initial_name("backup.dargui.json")
        def done(dialog, result):
            try:
                f = dialog.save_finish(result)
                if f and f.get_path():
                    save_profile(f.get_path(), self.options())
            except GLib.Error:
                pass
            except Exception as exc:
                Gtk.AlertDialog(message="Speichern fehlgeschlagen", detail=str(exc)).show(self)
        d.save(self, None, done)

    def _load_profile(self, *_):
        d = Gtk.FileDialog(title="DAR-Einstellungen laden")
        def done(dialog, result):
            try:
                f = dialog.open_finish(result)
                if f and f.get_path():
                    self._apply(load_profile(f.get_path()))
            except GLib.Error:
                pass
            except Exception as exc:
                Gtk.AlertDialog(message="Laden fehlgeschlagen", detail=str(exc)).show(self)
        d.open(self, None, done)

    def _apply(self, o: ArchiveCreateOptions):
        self.source.set_text(o.source); self.archive.set_text(o.archive)
        self.diff_radio.set_active(bool(o.reference)); self.reference.set_text(o.reference or "")
        self.reference_kind.set_selected(1 if o.reference_is_catalog else 0)
        self.encrypt.set_active(o.encryption_enabled)
        ciphers, comps = supported_algorithms()
        if o.encryption_algorithm in ciphers: self.cipher.set_selected(ciphers.index(o.encryption_algorithm))
        comp_values = ["none"] + comps
        if o.compression in comp_values: self.compression.set_selected(comp_values.index(o.compression))
        self.level.set_value(o.compression_level)
        self.slice.set_active(o.slice_enabled); self.slice_size.set_size(o.slice_size); self.first_slice.set_size(o.first_slice_size)
        self.empty_dirs.set_active(o.empty_dirs); self.hardlinks.set_active(o.hard_links); self.sparse.set_active(o.sparse_files)
        self.xattrs.set_active(o.xattrs); self.acl.set_active(o.acl); self.numeric.set_active(o.numeric_owner)
