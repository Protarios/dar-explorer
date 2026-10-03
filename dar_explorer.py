#!/usr/bin/env python3
"""
DAR Explorer V2 - GTK4 Frontend für DAR & dar_manager.
Vollständige Integration mit Unterstützung für lokale Slices und Tape-Modus (LTFS / Bandwechsel).
"""

from __future__ import annotations
import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
import queue
from pathlib import Path

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, GObject, Gtk, Pango

from dar_parser import ParsedEntry, normalize_archive_argument, run_dar_listing


def get_dar_manager_executable() -> str | None:
    """Sucht nach dar_manager im System-PATH sowie unter /usr/local/bin/dar_manager."""
    found = shutil.which("dar_manager")
    if found:
        return found
    if os.path.exists("/usr/local/bin/dar_manager") and os.access("/usr/local/bin/dar_manager", os.X_OK):
        return "/usr/local/bin/dar_manager"
    return None


class DarNode(GObject.Object):
    def __init__(
        self,
        name: str,
        path: str,
        is_dir: bool,
        permissions: str = "",
        user: str = "",
        group: str = "",
        size: str = "",
        date: str = "",
    ):
        super().__init__()
        self.name = name
        self.path = path
        self.is_dir = is_dir
        self.permissions = permissions
        self.user = user
        self.group = group
        self.size = size
        self.date = date
        self.children = Gio.ListStore.new(DarNode)

    @property
    def type_text(self):
        return "Verzeichnis" if self.is_dir else "Datei"


def build_tree(entries: list[ParsedEntry]):
    tree_structure = {}
    nodes = {}
    roots = []

    for entry in entries:
        clean = entry.path.strip("/")
        if not clean:
            continue

        parts = clean.split("/")
        current_path = ""
        parent_path = ""

        for index, part in enumerate(parts):
            current_path = part if not current_path else f"{current_path}/{part}"
            is_last = (index == len(parts) - 1)

            if current_path not in nodes:
                node = DarNode(
                    part, current_path,
                    entry.is_dir if is_last else True,
                    entry.permissions if is_last else "drwxr-xr-x",
                    entry.user if is_last else "unknown",
                    entry.group if is_last else "unknown",
                    entry.size if is_last else "—",
                    entry.date if is_last else "—"
                )
                nodes[current_path] = node

                if index == 0:
                    roots.append(node)
                else:
                    if parent_path not in tree_structure:
                        tree_structure[parent_path] = []
                    tree_structure[parent_path].append(node)
            elif is_last:
                node = nodes[current_path]
                node.is_dir = entry.is_dir
                node.permissions = entry.permissions
                node.user = entry.user
                node.group = entry.group
                node.size = entry.size
                node.date = entry.date

            parent_path = current_path

    def populate_gtk_children(node):
        children_nodes = tree_structure.get(node.path, [])
        if children_nodes:
            children_nodes.sort(key=lambda n: (not n.is_dir, n.name.casefold()))
            for child in children_nodes:
                populate_gtk_children(child)
                node.children.append(child)

    roots.sort(key=lambda n: (not n.is_dir, n.name.casefold()))
    root_store = Gio.ListStore.new(DarNode)

    archive_root = DarNode(
        "📦 [Gesamtes Archiv entpacken]",
        "",
        True, "drwxr-xr-x", "root", "root", "—", "—"
    )

    for root_node in roots:
        populate_gtk_children(root_node)
        archive_root.children.append(root_node)

    root_store.append(archive_root)
    return root_store, list(nodes.values())


def check_all_slices_present(path: str) -> bool:
    abs_path = os.path.abspath(os.path.expanduser(path))
    directory = os.path.dirname(abs_path)

    archive_base = normalize_archive_argument(abs_path)
    base_name = os.path.basename(archive_base)

    pattern = re.compile(rf"^{re.escape(base_name)}\.(\d+)\.dar$")
    slice_numbers = []

    try:
        for file in os.listdir(directory):
            match = pattern.match(file)
            if match:
                slice_numbers.append(int(match.group(1)))
    except Exception:
        return False

    if not slice_numbers:
        return False

    slice_numbers.sort()
    max_slice = slice_numbers[-1]
    expected_set = set(range(1, max_slice + 1))
    actual_set = set(slice_numbers)

    return expected_set == actual_set


class DarExplorerWindow(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app)
        self.set_default_size(1450, 850)
        self.set_title("DAR Explorer V2")

        try:
            from config_manager import load_config
            self.config = load_config()
        except ImportError:
            self.config = {"slice_lager_path": os.path.expanduser("~/mein_band_archiv")}

        self.current_archive = None
        self.archive_password = None
        self.all_nodes = []
        self.root_store = None
        self.loading = False
        self.extracting = False
        self._gui_file_count = 0

        self.is_tape_active = False
        self.current_dmd_db = None

        self._build_ui()

    def _ask_tape_mode(self, callback):
        window = Gtk.Window(title="Archiv-Modus wählen", transient_for=self, modal=True)
        window.set_default_size(450, -1)

        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=15)
        main_box.set_margin_top(15)
        main_box.set_margin_bottom(15)
        main_box.set_margin_start(15)
        main_box.set_margin_end(15)
        window.set_child(main_box)

        label = Gtk.Label()
        label.set_markup(
            "<b>Die Teilsicherungen (Slices) dieses Archivs sind unvollständig.</b>\n\n"
            "Handelt es sich um ein <b>Bandlaufwerk (LTFS)</b>, bei dem die verbleibenden "
            "Slices durch Bandwechsel nachgeladen werden müssen?\n\n"
            "• <b>Ja (Tape-Modus):</b> Das Tool nutzt dar_manager, damit fehlende Slices "
            "beim Entpacken interaktiv vom Band angefordert werden können.\n"
            "• <b>Nein (Lokal):</b> Das Archiv wird direkt vor Ort geöffnet."
        )
        label.set_xalign(0)
        label.set_wrap(True)
        main_box.append(label)

        button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        button_box.set_halign(Gtk.Align.END)
        main_box.append(button_box)

        btn_no = Gtk.Button(label="Nein, lokal öffnen")
        btn_yes = Gtk.Button(label="Ja, Tape-Modus aktivieren")
        btn_yes.add_css_class("suggested-action")

        button_box.append(btn_no)
        button_box.append(btn_yes)

        def on_choice(button, is_tape):
            window.close()
            callback(is_tape)

        btn_no.connect("clicked", on_choice, False)
        btn_yes.connect("clicked", on_choice, True)
        window.present()

    def _build_config_popover(self, menu_button):
        popover = Gtk.Popover()
        menu_button.set_popover(popover)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_margin_top(10)
        box.set_margin_bottom(10)
        box.set_margin_start(12)
        box.set_margin_end(12)
        popover.set_child(box)

        lbl = Gtk.Label()
        lbl.set_markup("<b>Teilarchiv-Lager (Bandspeicher)</b>")
        lbl.set_xalign(0)
        box.append(lbl)

        current_path = self.config.get("slice_lager_path", "")
        self.config_path_label = Gtk.Label(label=current_path)
        self.config_path_label.set_xalign(0)
        self.config_path_label.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        self.config_path_label.set_width_chars(35)
        self.config_path_label.add_css_class("dim-label")
        box.append(self.config_path_label)

        change_path_btn = Gtk.Button(label="Ordner ändern...")
        change_path_btn.connect("clicked", self.on_change_config_path_clicked, popover)
        box.append(change_path_btn)

    def on_change_config_path_clicked(self, _btn, popover):
        dialog = Gtk.FileDialog(title="Ordner für Teilarchive wählen")

        def on_folder_selected(dialog_obj, result):
            try:
                folder = dialog_obj.select_folder_finish(result)
                if folder:
                    new_path = folder.get_path()
                    if new_path:
                        self.config["slice_lager_path"] = new_path
                        self.config_path_label.set_text(new_path)

                        from config_manager import save_config
                        save_config(self.config)
                        self._append_log_threadsafe(f"[Config]: Speicherort für Slices geändert auf: {new_path}")
            except Exception as e:
                self._append_log_threadsafe(f"[Config]: Fehler bei der Pfadauswahl: {e}")
            finally:
                popover.popdown()

        dialog.select_folder(self, None, on_folder_selected)

    def _build_ui(self):
        header = Gtk.HeaderBar()
        self.set_titlebar(header)

        title = Gtk.Label(label="DAR Explorer")
        title.add_css_class("title")
        header.set_title_widget(title)

        open_btn = Gtk.Button(label="Archiv öffnen")
        open_btn.connect("clicked", self.on_open_clicked)
        header.pack_start(open_btn)

        self.reload_btn = Gtk.Button(label="Neu laden")
        self.reload_btn.connect("clicked", self.on_reload_clicked)
        self.reload_btn.set_sensitive(False)
        header.pack_start(self.reload_btn)

        self.extract_btn = Gtk.Button(label="Auswahl extrahieren")
        self.extract_btn.add_css_class("suggested-action")
        self.extract_btn.set_sensitive(False)
        self.extract_btn.connect("clicked", self.on_extract_clicked)
        header.pack_start(self.extract_btn)

        self.search = Gtk.SearchEntry()
        self.search.set_placeholder_text("Archiv durchsuchen …")
        self.search.set_width_chars(30)
        self.search.connect("search-changed", self.on_search_changed)
        header.pack_end(self.search)

        config_btn = Gtk.MenuButton()
        config_btn.set_icon_name("emblem-system")
        config_btn.set_tooltip_text("Einstellungen")
        self._build_config_popover(config_btn)
        header.pack_end(config_btn)

        self.archive_label = Gtk.Label(label="Kein Archiv geöffnet")
        self.archive_label.set_xalign(0)
        self.archive_label.add_css_class("dim-label")

        self.total_stats_label = Gtk.Label(label="")
        self.total_stats_label.set_xalign(1)
        self.total_stats_label.add_css_class("dim-label")

        self.path_label = Gtk.Label(label="")
        self.path_label.set_xalign(0)
        self.path_label.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        self.path_label.add_css_class("path-label")

        left_info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        left_info.set_hexpand(True)
        left_info.append(self.archive_label)
        left_info.append(self.path_label)

        info = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        info.set_margin_top(8)
        info.set_margin_bottom(6)
        info.set_margin_start(10)
        info.set_margin_end(10)

        info.append(left_info)
        info.append(self.total_stats_label)

        self.tree_selection = Gtk.SingleSelection()
        self.tree_selection.set_autoselect(True)
        self.tree_selection.connect("selection-changed", self.on_tree_selection_changed)

        tree_factory = Gtk.SignalListItemFactory()
        tree_factory.connect("setup", self.on_tree_setup)
        tree_factory.connect("bind", self.on_tree_bind)

        tree_view = Gtk.ListView.new(self.tree_selection, tree_factory)
        tree_view.add_css_class("archive-tree")

        tree_scroll = Gtk.ScrolledWindow()
        tree_scroll.set_child(tree_view)

        self.right_selection = Gtk.MultiSelection()
        self.right_selection.connect("selection-changed", self.on_right_selection_changed)

        self.column_view = Gtk.ColumnView.new(self.right_selection)
        self.column_view.set_show_row_separators(True)
        self.column_view.set_show_column_separators(True)

        self._add_column("Name", self._name_factory(), 330)
        self._add_column("Typ", self._type_factory(), 125)
        self._add_column("Größe", self._size_factory(), 110)
        self._add_column("Datum", self._date_factory(), 190)
        self._add_column("Pfad", self._path_factory(), 500)

        right_scroll = Gtk.ScrolledWindow()
        right_scroll.set_min_content_height(400)
        right_scroll.set_vexpand(True)
        right_scroll.set_child(self.column_view)

        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        paned.set_position(330)
        paned.set_start_child(tree_scroll)
        paned.set_end_child(right_scroll)

        self.status_label = Gtk.Label(label="Bereit")
        self.status_label.set_xalign(0)
        self.status_label.set_margin_start(10)
        self.status_label.set_margin_end(10)

        self.progress = Gtk.ProgressBar()
        self.progress.set_show_text(True)
        self.progress.set_text("")
        self.progress.set_visible(False)
        self.progress.set_margin_start(10)
        self.progress.set_margin_end(10)

        log_frame = Gtk.Frame(label="DAR-Ausgabe")
        self.log_view = Gtk.TextView()
        self.log_view.set_editable(False)
        self.log_view.set_monospace(True)
        self.log_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.log_buffer = self.log_view.get_buffer()

        log_scroll = Gtk.ScrolledWindow()
        log_scroll.set_min_content_height(130)
        log_scroll.set_child(self.log_view)
        log_frame.set_child(log_scroll)
        log_frame.set_visible(False)
        self.log_frame = log_frame

        self.show_log_btn = Gtk.ToggleButton(label="DAR-Ausgabe anzeigen")
        self.show_log_btn.set_margin_start(10)
        self.show_log_btn.set_margin_end(10)
        self.show_log_btn.connect("toggled", self.on_show_log_toggled)

        bottom = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        bottom.append(self.progress)
        bottom.append(self.status_label)

        log_controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        log_controls.append(self.show_log_btn)
        bottom.append(log_controls)
        bottom.append(log_frame)

        main = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        main.append(info)
        main.append(paned)
        main.append(bottom)

        self.set_child(main)
        self._install_css()

    def _install_css(self):
        css = Gtk.CssProvider()
        css.load_from_data(
            b"""
            .archive-tree { padding: 6px; }
            .path-label { font-family: monospace; }
            .dim-label { opacity: 0.75; }
            columnview row { padding-top: 2px; padding-bottom: 2px; }
            """
        )
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            css,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

    def _add_column(self, title, factory, width):
        col = Gtk.ColumnViewColumn(title=title, factory=factory)
        col.set_resizable(True)
        col.set_fixed_width(width)
        self.column_view.append_column(col)

    @staticmethod
    def _label_factory():
        factory = Gtk.SignalListItemFactory()
        def setup(_factory, item):
            label = Gtk.Label()
            label.set_xalign(0)
            label.set_ellipsize(Pango.EllipsizeMode.END)
            label.set_hexpand(True)
            item.set_child(label)
        factory.connect("setup", setup)
        return factory

    def _name_factory(self):
        f = self._label_factory()
        f.connect("bind", lambda _, item: item.get_child().set_text(
            ("📁  " if item.get_item().is_dir else "📄  ") + item.get_item().name
        ))
        return f

    def _type_factory(self):
        f = self._label_factory()
        f.connect("bind", lambda _, item: item.get_child().set_text(item.get_item().type_text))
        return f

    def _size_factory(self):
        f = self._label_factory()
        def bind(_, item):
            label = item.get_child()
            label.set_xalign(0)
            label.set_text(item.get_item().size or "—")
        f.connect("bind", bind)
        return f

    def _date_factory(self):
        f = self._label_factory()
        f.connect("bind", lambda _, item: item.get_child().set_text(item.get_item().date or "—"))
        return f

    def _path_factory(self):
        f = self._label_factory()
        f.connect("bind", lambda _, item: item.get_child().set_text(item.get_item().path))
        return f

    def on_tree_setup(self, _, list_item):
        expander = Gtk.TreeExpander()
        label = Gtk.Label()
        label.set_xalign(0)
        label.set_ellipsize(Pango.EllipsizeMode.END)
        expander.set_child(label)
        list_item.set_child(expander)

    def on_tree_bind(self, _, list_item):
        row = list_item.get_item()
        expander = list_item.get_child()
        expander.set_list_row(row)
        node = row.get_item()
        expander.get_child().set_text(("📁  " if node.is_dir else "📄  ") + node.name)

    def load_archive(self, path):
        if self.loading or self.extracting:
            return

        path = str(Path(path).expanduser())
        all_present = check_all_slices_present(path)

        def proceed_with_loading(is_tape_mode):
            self.is_tape_active = is_tape_mode
            self.current_archive = path
            self.loading = True
            self._gui_file_count = 0

            self.reload_btn.set_sensitive(False)
            self.extract_btn.set_sensitive(False)
            self.archive_label.set_text(f"Archiv: {path}")
            self.path_label.set_text("DAR-Listing wird eingelesen …")
            self.status_label.set_text("DAR wird ausgeführt …")

            self.progress.set_fraction(0.0)
            self.progress.set_text("Analysiere Archivstruktur... (0 Einträge)")
            self.progress.set_visible(True)

            threading.Thread(
                target=self._load_worker, args=(path,), daemon=True
            ).start()

        if all_present:
            proceed_with_loading(is_tape_mode=False)
        else:
            self._ask_tape_mode(proceed_with_loading)

    def _pulse_progress(self, count=None):
        if not self.loading:
            return False

        self.progress.pulse()

        if count is not None:
            self._gui_file_count = count

        formatted_count = f"{self._gui_file_count:,}".replace(",", ".")
        self.progress.set_text(f"Analysiere Archivstruktur... ({formatted_count} Einträge eingelesen)")
        return False

    def _load_worker(self, path):
        try:
            def password_callback(prompt_text):
                return self._prompt_user_interactive_queue(prompt_text)

            def progress_bridge(*args):
                count = args[0] if args else None
                GLib.idle_add(self._pulse_progress, count)

            archive_arg = normalize_archive_argument(path)
            tmp_dir = "/tmp/dar-explorer"
            os.makedirs(tmp_dir, exist_ok=True, mode=0o700)
            self.current_dmd_db = os.path.join(tmp_dir, "tape_archive.dmd")

            dar_mgr_bin = get_dar_manager_executable()
            if getattr(self, 'is_tape_active', False):
                if not dar_mgr_bin:
                    raise RuntimeError(
                        "Das Systemwerkzeug 'dar_manager' ist nicht installiert.\n"
                        "Bitte installieren Sie das Paket 'dar'."
                    )

                if os.path.exists(self.current_dmd_db):
                    try: os.remove(self.current_dmd_db)
                    except Exception: pass

            check_cmd = f"dar -n -l {shlex.quote(archive_arg)} -Q"
            check_process = subprocess.Popen(
                check_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True,
                env={"LC_ALL": "C", "LANG": "C"}
            )
            _, check_stderr = check_process.communicate()
            returncode = check_process.wait()

            if returncode != 0:
                GLib.idle_add(self._force_show_log)
                GLib.idle_add(self._append_log, "[System]: Archiv geschützt oder erfordert Passwort...")
                pwd = self._prompt_user_interactive_queue("Bitte gib das Passwort für das Archiv ein:")
                if pwd is not None:
                    self.archive_password = pwd
                else:
                    raise RuntimeError("Archiv-Einlesen vom Benutzer abgebrochen.")

            # Nutzung des DarTapeManagers im Tape-Modus
            if getattr(self, 'is_tape_active', False):
                from dar_tape_manager import DarTapeManager
                manager = DarTapeManager(self.current_dmd_db, dar_mgr_bin)

                if not manager.create_database():
                    raise RuntimeError("Fehler beim Erstellen der DMD-Datenbank.")

                if not manager.add_archive(archive_arg, self.archive_password):
                    raise RuntimeError("Katalog konnte nicht in dar_manager geladen werden.")

                self._append_log_threadsafe("[System]: dar_manager-Datenbank erfolgreich erstellt.")

            # Lokales Einlesen über den Standard-Parser (unberührt für normale Archive)
            entries, warning = run_dar_listing(
                path,
                password_callback=password_callback,
                progress_callback=progress_bridge,
                encryption_password=self.archive_password
            )
            GLib.idle_add(self._load_finished, path, entries, warning, None)

        except Exception as exc:
            GLib.idle_add(self._load_finished, path, None, "", str(exc))

    def _extract_worker(self, destination, paths_to_extract, restore_ownership, total_expected_files):
        try:
            # 1. TAPE-MODUS (über DarTapeManager)
            if getattr(self, 'is_tape_active', False) and self.current_dmd_db:
                dar_mgr_bin = get_dar_manager_executable() or "dar_manager"
                from dar_tape_manager import DarTapeManager
                manager = DarTapeManager(self.current_dmd_db, dar_mgr_bin)

                success = manager.restore_files(
                    target_dir=destination,
                    password=self.archive_password,
                    file_patterns=None if paths_to_extract == [""] else paths_to_extract,
                    restore_ownership=restore_ownership
                )

                if success:
                    GLib.idle_add(self._extract_finished, "Extraktion erfolgreich abgeschlossen.")
                else:
                    GLib.idle_add(self._force_show_log)
                    GLib.idle_add(self._extract_finished, "Extraktion via dar_manager fehlgeschlagen.")
                return

            # 2. STANDARDMODUS (Direktes DAR - vollkommen unberührt)
            else:
                clean_archive = normalize_archive_argument(self.current_archive)
                cmd = ["dar", "-x", clean_archive, "-R", destination, "-w"]

                if not restore_ownership:
                    cmd.append("-O")

                if self.archive_password:
                    cmd.extend(["-K", f"gzip:{self.archive_password}"])

                if paths_to_extract != [""]:
                    for p in paths_to_extract:
                        if p:
                            cmd.extend(["-g", p])

            sudo_pwd = None
            if restore_ownership:
                sudo_pwd = self._prompt_user_interactive_queue(
                    "Für die Wiederherstellung der Dateirechte wird Ihr sudo-Passwort benötigt:"
                )
                if not sudo_pwd:
                    self._append_log_threadsafe("[System]: Extraktion abgebrochen (kein Sudo-Passwort).")
                    GLib.idle_add(self._extract_finished, "Extraktion abgebrochen")
                    return

                cmd = ["sudo", "-S"] + cmd

            self._append_log_threadsafe(f"[Executing Command]: {' '.join(cmd)}")

            process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                env={"LC_ALL": "C", "LANG": "C"}
            )

            if restore_ownership and sudo_pwd:
                process.stdin.write(f"{sudo_pwd}\n")
                process.stdin.flush()

            file_counter = 0

            while True:
                line = process.stdout.readline()
                if not line and process.poll() is not None:
                    break

                if line:
                    line_str = line.strip()
                    self._append_log_threadsafe(line_str)
                    if any(kw in line_str for kw in ["Extracting", "Restoring", "Entpacke", "Wiederherstellen", "[OK]"]):
                        file_counter += 1
                        GLib.idle_add(self._update_progress_ui, file_counter, total_expected_files)

            returncode = process.wait()
            if returncode == 0:
                GLib.idle_add(self._extract_finished, "Extraktion erfolgreich abgeschlossen.")
            else:
                GLib.idle_add(self._force_show_log)
                GLib.idle_add(self._extract_finished, f"Extraktion fehlgeschlagen (Exit-Code {returncode}).")

        except Exception as e:
            GLib.idle_add(self._force_show_log)
            self._append_log_threadsafe(f"[Fehler bei Extraktion]: {e}")
            GLib.idle_add(self._extract_finished, f"Fehler: {e}")

    def _load_finished(self, path, entries, warning, error):
        self.loading = False
        self.reload_btn.set_sensitive(True)

        self.progress.set_visible(False)
        self.progress.set_fraction(0.0)
        self.progress.set_text("")

        if error:
            self.status_label.set_text("Fehler beim Einlesen")
            self._show_error("DAR Explorer", error)
            return False

        self.root_store, self.all_nodes = build_tree(entries)
        model = Gtk.TreeListModel.new(
            self.root_store, False, False, self._tree_children
        )
        self.tree_selection.set_model(model)

        self.search.set_text("")

        if self.root_store.get_n_items() > 0:
            archive_root_node = self.root_store.get_item(0)
            self.right_selection.set_model(archive_root_node.children)
        else:
            self.right_selection.set_model(self.root_store)

        self.extract_btn.set_sensitive(False)

        dirs = sum(n.is_dir for n in self.all_nodes)
        files = len(self.all_nodes) - dirs
        total = len(self.all_nodes)

        msg = (
            f"{total:,} Einträge  •  {dirs:,} Verzeichnisse  •  "
            f"{files:,} Dateien"
        ).replace(",", ".")

        if warning:
            msg += f"  •  {warning.splitlines()}"

        self.status_label.set_text(msg)
        self.total_stats_label.set_text(msg)
        self.path_label.set_text("Archivwurzel")

        if self.root_store.get_n_items():
            self.tree_selection.set_selected(0)
            GLib.idle_add(self._update_extract_button)

        return False

    @staticmethod
    def _tree_children(*args):
        if not args:
            return None
        item = args[-1]

        if hasattr(item, "get_item"):
            item = item.get_item()

        if item and hasattr(item, "is_dir") and item.is_dir:
            return item.children
        return None

    def on_tree_selection_changed(self, selection, _position, _n_items):
        row = selection.get_selected_item()
        if row is None:
            return

        node = row.get_item()
        if node is None:
            return

        self.path_label.set_text(node.path if node.path else "Gesamtes Archiv")

        if self.search.get_text().strip():
            self._apply_search()
        else:
            if hasattr(node, 'children') and node.children is not None:
                self.right_selection.set_model(node.children)

                total = node.children.get_n_items()
                if total == 0:
                    self.status_label.set_text("Dieser Ordner ist im Archiv leer.")
                else:
                    self.status_label.set_text(f"{total:,} Einträge in diesem Verzeichnis.".replace(",", "."))
            else:
                self.right_selection.set_model(Gio.ListStore.new(DarNode))
                self.status_label.set_text("Dieser Ordner ist im Archiv leer.")

        self._update_extract_button()

    def on_right_selection_changed(self, _selection, _position, _n_items):
        self._update_extract_button()

    def _update_extract_button(self):
        if self.extracting or self.loading:
            self.extract_btn.set_sensitive(False)
            return

        n = self.right_selection.get_selection().get_size()

        sidebar_model = self.tree_selection.get_model()
        sidebar_pos = self.tree_selection.get_selected()
        if sidebar_pos != 0xffffffff and sidebar_model:
            row = sidebar_model.get_item(sidebar_pos)
            if row and row.get_item() and row.get_item().path == "":
                self.extract_btn.set_sensitive(self.current_archive is not None)
                return

        self.extract_btn.set_sensitive(n > 0 and self.current_archive is not None)

    def on_search_changed(self, _entry):
        self._apply_search()

    def _apply_search(self):
        query = self.search.get_text().strip().casefold()

        if not query:
            row = self.tree_selection.get_selected_item()
            if row:
                node = row.get_item()
                self.right_selection.set_model(
                    node.children if node.is_dir else Gio.ListStore.new(DarNode)
                )
                self.path_label.set_text(node.path)
            self._update_extract_button()
            return

        store = Gio.ListStore.new(DarNode)
        for node in self.all_nodes:
            if query in f"{node.name} {node.path}".casefold():
                store.append(node)

        self.right_selection.set_model(store)
        self.path_label.set_text(f"Suche: „{self.search.get_text()}“")
        self.status_label.set_text(f"{store.get_n_items():,} Treffer".replace(",", "."))
        self._update_extract_button()

    def on_open_clicked(self, _button):
        dialog = Gtk.FileDialog()
        dialog.set_title("DAR-Archiv auswählen")
        dialog.open(self, None, self._open_done)

    def _open_done(self, dialog, result):
        try:
            file = dialog.open_finish(result)
        except GLib.Error:
            return
        if file:
            path = file.get_path()
            if path:
                self.load_archive(path)

    def on_reload_clicked(self, _button):
        if self.current_archive:
            self.load_archive(self.current_archive)

    def on_extract_clicked(self, _button):
        paths_to_extract = []

        selection_bitset = self.right_selection.get_selection()
        model = self.right_selection.get_model()

        if selection_bitset and model and not selection_bitset.is_empty():
            for i in range(model.get_n_items()):
                if selection_bitset.contains(i):
                    item = model.get_item(i)
                    if item:
                        paths_to_extract.append(item.path)

        if not paths_to_extract:
            sidebar_selection = getattr(self, 'tree_selection', None)
            if sidebar_selection and hasattr(sidebar_selection, 'get_selected_item'):
                row = sidebar_selection.get_selected_item()
                if row and row.get_item():
                    node = row.get_item()
                    if node.path == "":
                        paths_to_extract = [""]
                    else:
                        paths_to_extract.append(node.path)

        if not paths_to_extract:
            self._append_log_threadsafe("Keine Dateien zur Extraktion ausgewählt.")
            return

        dialog = Gtk.FileDialog(title="Extraktions-Ziel wählen")

        def on_folder_picked(dialog_obj, result):
            try:
                folder = dialog_obj.select_folder_finish(result)
                if folder:
                    destination = folder.get_path()
                    self._ask_for_permissions_mode(destination, paths_to_extract)
            except Exception as e:
                self._append_log_threadsafe(f"Fehler bei der Ordnerauswahl: {e}")

        dialog.select_folder(self, None, on_folder_picked)

    def _ask_for_permissions_mode(self, destination, paths_to_extract):
        window = Gtk.Window(title="Berechtigungen wiederherstellen?", transient_for=self, modal=True)
        window.set_default_size(450, -1)

        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=15)
        main_box.set_margin_top(15)
        main_box.set_margin_bottom(15)
        main_box.set_margin_start(15)
        main_box.set_margin_end(15)
        window.set_child(main_box)

        label = Gtk.Label()
        label.set_markup(
            "<b>Wie sollen Dateiberechtigungen (Besitzer/Gruppen) wiederhergestellt werden?</b>\n\n"
            "• <i>Originale Rechte behalten:</i> DAR stellt UID/GID exakt wieder her (erfordert sudo).\n"
            "• <i>Rechte ignorieren:</i> Alle Dateien gehören danach Ihrem aktuellen Benutzer."
        )
        label.set_xalign(0)
        label.set_wrap(True)
        main_box.append(label)

        button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        button_box.set_halign(Gtk.Align.END)
        main_box.append(button_box)

        btn_cancel = Gtk.Button(label="Abbrechen")
        btn_ignore = Gtk.Button(label="Rechte ignorieren")
        btn_keep = Gtk.Button(label="Originale Rechte behalten")

        button_box.append(btn_cancel)
        button_box.append(btn_ignore)
        button_box.append(btn_keep)

        def on_choice_made(button, action_type):
            window.close()
            if action_type == "cancel":
                return

            restore_ownership = (action_type == "keep")
            self._start_extraction_thread(destination, paths_to_extract, restore_ownership)

        btn_cancel.connect("clicked", on_choice_made, "cancel")
        btn_ignore.connect("clicked", on_choice_made, "ignore")
        btn_keep.connect("clicked", on_choice_made, "keep")

        window.present()

    def _start_extraction_thread(self, destination, paths_to_extract, restore_ownership):
        self.extracting = True
        self.extract_btn.set_sensitive(False)
        self.reload_btn.set_sensitive(False)

        if paths_to_extract == [""]:
            total_expected_files = len(self.all_nodes)
        else:
            total_expected_files = len(paths_to_extract)

        self.progress.set_fraction(0.0)
        self.progress.set_text(f"Starte Extraktion ({total_expected_files} Elemente)...")
        self.progress.set_visible(True)

        threading.Thread(
            target=self._extract_worker,
            args=(destination, paths_to_extract, restore_ownership, total_expected_files),
            daemon=True
        ).start()

    def _extract_worker(self, destination, paths_to_extract, restore_ownership, total_expected_files):
        try:
            cmd = []

            # ==========================================
            # 1. TAPE-MODUS (LTFS / dar_manager)
            # ==========================================
            if getattr(self, 'is_tape_active', False) and self.current_dmd_db:
                dar_mgr_bin = get_dar_manager_executable() or "dar_manager"

                cmd = [dar_mgr_bin, "-N", "-B", self.current_dmd_db]

                # Zielverzeichnis via -o an dar übergeben
                cmd.extend(["-o", f"-R {destination}"])

                if not restore_ownership:
                    cmd.extend(["-o", "-O"])

                if self.archive_password:
                    cmd.extend(["-o", f"-K pass:{self.archive_password}"])

                # Extraktions-Befehl (-r)
                cmd.append("-r")

                # Dateiauswahl an -r anhängen (muss relativ sein, ohne führenden Slash)
                if paths_to_extract != [""]:
                    for p in paths_to_extract:
                        clean_path = p.lstrip("/")
                        if clean_path:
                            cmd.append(clean_path)

            # ==========================================
            # 2. STANDARDMODUS (Direktes DAR)
            # ==========================================
            else:
                clean_archive = normalize_archive_argument(self.current_archive)
                cmd = ["dar", "-x", clean_archive, "-R", destination, "-w"]

                if not restore_ownership:
                    cmd.append("-O")

                if self.archive_password:
                    cmd.extend(["-K", f"pass:{self.archive_password}"])

                if paths_to_extract != [""]:
                    for p in paths_to_extract:
                        if p:
                            cmd.extend(["-g", p])

            # Rechte-Wiederherstellung mit sudo
            sudo_pwd = None
            if restore_ownership:
                sudo_pwd = self._prompt_user_interactive_queue(
                    "Für die Wiederherstellung der Dateirechte wird Ihr sudo-Passwort benötigt:"
                )
                if not sudo_pwd:
                    self._append_log_threadsafe("[System]: Extraktion abgebrochen (kein Sudo-Passwort).")
                    GLib.idle_add(self._extract_finished, "Extraktion abgebrochen")
                    return

                cmd = ["sudo", "-S"] + cmd

            self._append_log_threadsafe(f"[Executing Command]: {' '.join(cmd)}")

            process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                env={"LC_ALL": "C", "LANG": "C"}
            )

            if restore_ownership and sudo_pwd:
                process.stdin.write(f"{sudo_pwd}\n")
                process.stdin.flush()

            file_counter = 0

            while True:
                line = process.stdout.readline()
                if not line and process.poll() is not None:
                    break

                if line:
                    line_str = line.strip()
                    print(f"[DAR OUT]: {line_str}", flush=True)
                    self._append_log_threadsafe(line_str)

                    # Interaktive Prompts abfangen (Passwort, Slice-Wechsel etc.)
                    if any(prompt in line_str for prompt in [
                        "Passphrase", "Password", "Enter key", "stelle die Slice", "Please enter slice",
                        "Continue?", "overwrite", "Press enter", "Entscheidung:"
                    ]):
                        user_input = self._prompt_user_interactive_queue(line_str)
                        if user_input is not None:
                            process.stdin.write(f"{user_input}\n")
                            process.stdin.flush()

                    if any(kw in line_str for kw in ["Extracting", "Restoring", "Entpacke", "Wiederherstellen", "[OK]"]):
                        file_counter += 1
                        GLib.idle_add(self._update_progress_ui, file_counter, total_expected_files)

            returncode = process.wait()
            if returncode == 0:
                GLib.idle_add(self._extract_finished, "Extraktion erfolgreich abgeschlossen.")
            else:
                GLib.idle_add(self._force_show_log)
                GLib.idle_add(self._extract_finished, f"Extraktion fehlgeschlagen (Exit-Code {returncode}).")

        except Exception as e:
            GLib.idle_add(self._force_show_log)
            self._append_log_threadsafe(f"[Fehler bei Extraktion]: {e}")
            GLib.idle_add(self._extract_finished, f"Fehler: {e}")

    def _prompt_user_interactive_queue(self, prompt_text: str) -> str | None:
        res_queue = queue.Queue()
        print(f"[DEBUG] Warten auf Benutzereingabe: {prompt_text!r}", flush=True)
        GLib.idle_add(self._show_prompt_dialog, prompt_text, res_queue)

        while True:
            try:
                val = res_queue.get(timeout=0.2)
                print(f"[DEBUG] Eingabe erhalten: {val!r}", flush=True)
                return val
            except queue.Empty:
                pass

    def _show_prompt_dialog(self, prompt_text: str, res_queue: queue.Queue):
        dialog = Gtk.Window(title="Eingabe erforderlich", transient_for=self, modal=True)
        dialog.set_default_size(420, -1)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_margin_top(15)
        box.set_margin_bottom(15)
        box.set_margin_start(15)
        box.set_margin_end(15)
        dialog.set_child(box)

        lbl = Gtk.Label(label=prompt_text)
        lbl.set_wrap(True)
        lbl.set_xalign(0)
        box.append(lbl)

        prompt_lower = prompt_text.lower()
        is_password_prompt = any(kw in prompt_lower for kw in ["passwort", "password", "passphrase", "key", "schlüssel"])
        is_slice_prompt = any(kw in prompt_lower for kw in ["slice", "band", "press enter", "drücke enter"])

        entry = Gtk.Entry()
        if is_password_prompt:
            entry.set_visibility(False)
            box.append(entry)
        elif not is_slice_prompt:
            box.append(entry)

        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        btn_box.set_halign(Gtk.Align.END)
        box.append(btn_box)

        btn_cancel = Gtk.Button(label="Abbrechen")
        btn_ok = Gtk.Button(label="OK")
        btn_ok.add_css_class("suggested-action")

        btn_box.append(btn_cancel)
        btn_box.append(btn_ok)

        def on_response(button, ok_clicked):
            if ok_clicked:
                text = entry.get_text() if (is_password_prompt or not is_slice_prompt) else ""
                res_queue.put(text)
            else:
                res_queue.put(None)
            dialog.close()

        btn_cancel.connect("clicked", on_response, False)
        btn_ok.connect("clicked", on_response, True)

        if is_password_prompt or not is_slice_prompt:
            entry.connect("activate", lambda _: on_response(btn_ok, True))

        dialog.present()

    def _append_log_threadsafe(self, text: str):
        GLib.idle_add(self._append_log, text)

    def _append_log(self, text: str):
        end_iter = self.log_buffer.get_end_iter()
        self.log_buffer.insert(end_iter, text + "\n")

    def _force_show_log(self):
        self.show_log_btn.set_active(True)
        self.log_frame.set_visible(True)

    def on_show_log_toggled(self, button):
        self.log_frame.set_visible(button.get_active())

    def _update_progress_ui(self, current: int, total: int):
        self.progress.set_visible(True)
        if total > 0:
            frac = min(1.0, current / total)
            self.progress.set_fraction(frac)
            self.progress.set_text(f"Extrahiere... {current}/{total} Elemente ({int(frac*100)}%)")
        else:
            self.progress.pulse()
            self.progress.set_text(f"Extrahiere... {current} Elemente entpackt")

    def _extract_finished(self, status_msg: str):
        self.extracting = False
        self.progress.set_visible(False)
        self.reload_btn.set_sensitive(True)
        self._update_extract_button()
        self.status_label.set_text(status_msg)

    def _show_error(self, title: str, message: str):
        dialog = Gtk.AlertDialog(message=title, detail=message)
        dialog.show(self)


class DarExplorerApp(Gtk.Application):
    def __init__(self):
        super().__init__(
            application_id="org.dar.explorer",
            flags=Gio.ApplicationFlags.FLAGS_NONE,
        )

    def do_activate(self):
        win = self.props.active_window
        if not win:
            win = DarExplorerWindow(self)
        win.present()


def main():
    app = DarExplorerApp()
    return app.run(sys.argv)


if __name__ == "__main__":
    sys.argv[0] = os.path.abspath(sys.argv[0])
    sys.exit(main())
