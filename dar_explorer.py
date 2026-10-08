#!/usr/bin/env python3
"""
DAR Explorer V2 - GTK4 Frontend für DAR & dar_manager.
Vollständige Integration mit Unterstützung für lokale Slices und Tape-Modus (LTFS / Bandwechsel)
sowie integriertem VTE-Terminal im unteren Bereich.
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
import sqlite3
from pathlib import Path

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Vte", "3.91")
from gi.repository import Gdk, Gio, GLib, GObject, Gtk, Pango, Vte

from dar_parser import ParsedEntry, normalize_archive_argument, run_dar_listing
from dar_backend.operations import (
    command_compare, command_isolate, command_repair, command_test, command_xform,
    shell_command,
)
from dialogs.phase2_tools import Phase2OperationDialog, RepairDialog
from dar_backend.archive import ArchiveCreateOptions
from dialogs.archive_create import ArchiveCreateDialog
from ltfs_backend.operations import (
    build_mount_command, build_unmount_command, filesystem_at, is_ltfs_filesystem,
    validate_mountpoint,
)


def get_dar_manager_executable() -> str | None:
    """Sucht nach dar_manager im System-PATH sowie unter /usr/local/bin/dar_manager."""
    found = shutil.which("dar_manager")
    if found:
        return found
    if os.path.exists("/usr/local/bin/dar_manager") and os.access("/usr/local/bin/dar_manager", os.X_OK):
        return "/usr/local/bin/dar_manager"
    return None

def get_dar_executable() -> str:
    """Sucht nach dem dar-Befehl im System-PATH."""
    found = shutil.which("dar")
    if found:
        return found
    for path in ["/usr/bin/dar", "/usr/local/bin/dar"]:
        if os.path.exists(path) and os.access(path, os.X_OK):
            return path
    return "dar"

class EmbeddedTerminal(Gtk.Box):
    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        self.terminal = Vte.Terminal()

        self.scrolled_window = Gtk.ScrolledWindow()
        self.scrolled_window.set_policy(
            Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC
        )
        self.scrolled_window.set_child(self.terminal)
        self.scrolled_window.set_vexpand(True)
        self.scrolled_window.set_hexpand(True)

        self.append(self.scrolled_window)

        # Standard-Shell starten (das lief vorhin fehlerfrei)
        self.start_shell()

    def start_shell(self):
        shell = os.environ.get('SHELL', '/bin/bash')
        cwd = os.environ.get('HOME', '/')

        try:
            self.terminal.spawn_async(
                Vte.PtyFlags.DEFAULT,
                cwd,
                [shell],
                [],
                GLib.SpawnFlags.DEFAULT,
                None,
                None,
                -1,
                None,
                self.on_spawn_complete,
            )
        except Exception as e:
            print(f'Fehler beim Starten der Shell: {e}')

    def on_spawn_complete(self, terminal, pid, error):
        if error:
            print(f'Terminal-Spawn fehlgeschlagen: {error}')
        else:
            print(f'Terminal erfolgreich gestartet mit PID: {pid}')


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
        size_bytes: int = 0,
        timestamp: int = 0,
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
        self.size_bytes = size_bytes
        self.timestamp = timestamp
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
                    entry.date if is_last else "—",
                    entry.size_bytes if is_last else 0,
                    entry.timestamp if is_last else 0,
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
                node.size_bytes = entry.size_bytes
                node.timestamp = entry.timestamp

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

        self.current_archive = None
        self.archive_password = None
        self.is_current_catalog = False
        self.all_nodes = []
        self.root_store = None
        self.loading = False
        self.extracting = False
        self._gui_file_count = 0
        self._current_nodes = []
        self._right_model_source = None
        self._sort_key = "name"
        self._sort_reverse = False
        self._type_filter = "all"
        self._index_enabled = False
        self._index_thread = None
        self._index_ready = False

        self.is_tape_active = False
        self.current_dmd_db = None

        # Temporärer isolierter Katalog, der beim Beenden optional gesichert
        # und anschließend gelöscht wird. Nur von uns erzeugte Kataloge werden
        # automatisch aufgeräumt; vom Benutzer geöffnete Katalogdateien bleiben erhalten.
        self.temporary_catalog_path = None

        self._ltfs_mount_pending = False
        self._ltfs_unmount_pending = False
        self._ltfs_unmount_requested = False
        self._build_ui()
        GLib.timeout_add_seconds(2, self._refresh_ltfs_mount_state)
        self.connect("close-request", self._on_close_request)

    def check_if_catalog(self, path: str) -> bool:
        file_path = Path(path)
        if not file_path.exists():
            return False

        try:
            file_size_gb = file_path.stat().st_size / (1024 * 1024 * 1024)
            if file_size_gb > 1.0:
                return False
        except Exception:
            pass

        try:
            dar_bin = get_dar_executable()
            result = subprocess.run(
                [dar_bin, "-t", str(file_path), "-Q"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            combined_output = result.stdout + result.stderr
            if "isolated catalogue" in combined_output.lower():
                return True
        except Exception:
            pass

        return False

    def _prompt_user_choice_queue(self, message):
        """Öffnet einen GTK4-Dialog mit Abbrechen und Weiter über das Response-Signal."""
        result_queue = queue.Queue()

        def run_dialog():
            dialog = Gtk.MessageDialog(
                transient_for=getattr(self, 'window', None),
                modal=True,
                message_type=Gtk.MessageType.WARNING,
                text="Archiv-Hinweis / Fehlendes Slice",
                secondary_text=message
            )
            dialog.add_button("Abbrechen", Gtk.ResponseType.CANCEL)
            dialog.add_button("Weiter", Gtk.ResponseType.OK)

            def on_response(dlg, response_id):
                if response_id == Gtk.ResponseType.OK:
                    result_queue.put("weiter")
                else:
                    result_queue.put("abbrechen")
                dlg.destroy()

            dialog.connect("response", on_response)
            dialog.present()

        GLib.idle_add(run_dialog)
        return result_queue.get()

    def _prompt_user_info_queue(self, message):
        """Öffnet einen einfachen GTK4-Fehlerdialog mit nur einem OK-Button."""
        result_queue = queue.Queue()

        def run_dialog():
            dialog = Gtk.MessageDialog(
                transient_for=getattr(self, 'window', None),
                modal=True,
                message_type=Gtk.MessageType.ERROR,
                text="Archiv-Fehler",
                secondary_text=message
            )
            dialog.add_button("OK", Gtk.ResponseType.OK)

            def on_response(dlg, response_id):
                result_queue.put(True)
                dlg.destroy()

            dialog.connect("response", on_response)
            dialog.present()

        GLib.idle_add(run_dialog)
        result_queue.get()

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

    def _prompt_for_extraction_password_and_retry(self, real_archive_arg, catalog_path, dest_dir, paths_to_extract):
        """Öffnet einen GTK-Dialog zur Passworteingabe und wiederholt die Extraktion."""
        dialog = Gtk.Window(title="Passwort erforderlich", transient_for=self, modal=True)
        dialog.set_default_size(400, -1)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_margin_top(15)
        box.set_margin_bottom(15)
        box.set_margin_start(15)
        box.set_margin_end(15)
        dialog.set_child(box)

        label = Gtk.Label(label="Das Archiv oder der Katalog ist verschlüsselt.\nBitte gib das Passwort ein:")
        box.append(label)

        entry = Gtk.Entry()
        entry.set_visibility(False)
        box.append(entry)

        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        btn_box.set_halign(Gtk.Align.END)
        box.append(btn_box)

        btn_cancel = Gtk.Button(label="Abbrechen")
        btn_ok = Gtk.Button(label="Extrahieren")
        btn_ok.add_css_class("suggested-action")

        btn_box.append(btn_cancel)
        btn_box.append(btn_ok)

        def on_ok(btn):
            pwd = entry.get_text()
            self.archive_password = pwd
            dialog.destroy()
            self._execute_extraction_with_catalog_reference(real_archive_arg, catalog_path, dest_dir, paths_to_extract, archive_password=pwd)

        def on_cancel(btn):
            dialog.destroy()
            if hasattr(self, 'progress'):
                self.progress.set_visible(False)
            if hasattr(self, 'status_label'):
                self.status_label.set_text("Extraktion abgebrochen.")

        btn_ok.connect("clicked", on_ok)
        btn_cancel.connect("clicked", on_cancel)
        dialog.present()


    def update_archive_banner(self, is_catalog=False):
        if is_catalog:
            markup = "🔍 <b>Isolierter Katalog</b>"
        else:
            markup = "📦 <b>Vollständiges Archiv</b>"

        GLib.idle_add(self.archive_status_right_label.set_markup, markup)

    def reset_archive_banner(self):
        GLib.idle_add(self.archive_status_right_label.set_markup, "<span color='gray'>Kein Archiv/Katalog geladen</span>")

    def _build_tools_popover(self, menu_button):
        popover = Gtk.Popover()
        menu_button.set_popover(popover)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_margin_top(8); box.set_margin_bottom(8)
        box.set_margin_start(8); box.set_margin_end(8)
        popover.set_child(box)

        actions = [
            ("Integritätsprüfung", "integrity"),
            ("Slice-Übersicht", "slices"),
            ("DAR-Metadaten", "metadata"),
            ("Hintergrundindex aufbauen", "index"),
            ("Archiv testen", "test"),
            ("Archiv / Dateisystem vergleichen", "compare"),
            ("Katalog isolieren", "isolate"),
            ("Unterbrochenes Archiv reparieren", "repair"),
            ("Slices mit dar_xform umformen", "xform"),
        ]
        for label, operation in actions:
            button = Gtk.Button(label=label)
            button.set_halign(Gtk.Align.FILL)
            button.connect("clicked", self._open_phase2_dialog, operation, popover)
            box.append(button)

    def _open_phase2_dialog(self, _button, operation, popover):
        popover.popdown()
        if operation == "integrity":
            self._run_integrity_check(); return
        if operation == "slices":
            self._show_slice_overview(); return
        if operation == "metadata":
            self._show_archive_metadata(); return
        if operation == "index":
            self._start_background_index(); return
        if operation == "repair":
            # Repair ist absichtlich unabhängig vom aktuell geöffneten Archiv.
            dialog = RepairDialog(self, self._run_phase2_operation)
        else:
            dialog = Phase2OperationDialog(self, operation, self.current_archive, self._run_phase2_operation)
        dialog.present()

    def _run_phase2_operation(self, operation, data):
        archive = data.get("archive", "")
        if operation in {"xform", "isolate"} and self.is_current_catalog:
            self._show_error("Nicht auf isoliertem Katalog", "Für diese Operation muss ein echtes DAR-Archiv geöffnet sein.")
            return

        try:
            if operation == "compare":
                cmd = command_compare(archive, data["filesystem_root"])
            elif operation == "test":
                cmd = command_test(archive, data.get("isolated_catalog"))
            elif operation == "isolate":
                cmd = command_isolate(archive, data["destination"], password=data.get("password"), slice_size=data.get("slice_size"), first_slice_size=data.get("first_slice_size"))
            elif operation == "repair":
                cmd = command_repair(archive, data["destination"], password=data.get("password"), slice_size=data.get("slice_size"), first_slice_size=data.get("first_slice_size"))
            elif operation == "xform":
                cmd = command_xform(archive, data["destination"], data["slice_size"], first_slice_size=data.get("first_slice_size"))
            else:
                raise ValueError(f"Unbekannte Phase-2-Operation: {operation}")

            command = shell_command(cmd)
            self._append_log_threadsafe(f"[PHASE 2] {command}")
            self._force_show_log()
            self.status_label.set_text("DAR-Operation läuft im Terminal …")
            self.terminal_widget.terminal.feed_child((command + "\n").encode("utf-8"))
        except Exception as exc:
            self._show_error("DAR Explorer", str(exc))

    def _build_ui(self):
        header = Gtk.HeaderBar()
        self.set_titlebar(header)

        title = Gtk.Label(label="DAR Explorer")
        title.add_css_class("title")
        header.set_title_widget(title)

        open_btn = Gtk.Button(label="Archiv öffnen")
        open_btn.connect("clicked", self.on_open_clicked)
        header.pack_start(open_btn)

        create_btn = Gtk.Button(label="Archiv erstellen")
        create_btn.add_css_class("suggested-action")
        create_btn.set_tooltip_text("Neues DAR-Archiv bzw. Differentialbackup erstellen")
        create_btn.connect("clicked", self.on_create_archive_clicked)
        header.pack_start(create_btn)

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
        self.search.set_width_chars(26)
        self.search.connect("search-changed", self.on_search_changed)

        self.filter_dropdown = Gtk.DropDown.new_from_strings(["Alle", "Dateien", "Verzeichnisse"])
        self.filter_dropdown.set_tooltip_text("Dateityp filtern")
        self.filter_dropdown.connect("notify::selected", lambda *_: self._on_view_filter_changed())

        header.pack_end(self.filter_dropdown)
        header.pack_end(self.search)

        tools_btn = Gtk.MenuButton(label="DAR-Werkzeuge")
        tools_btn.set_tooltip_text("Phase-2-DAR-Operationen")
        self._build_tools_popover(tools_btn)
        header.pack_end(tools_btn)

        self.archive_label = Gtk.Label(label="Kein Archiv geöffnet")
        self.archive_label.set_xalign(0)
        self.archive_label.add_css_class("dim-label")

        self.path_label = Gtk.Label(label="")
        self.path_label.set_xalign(0)
        self.path_label.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        self.path_label.add_css_class("path-label")

        left_info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        left_info.set_hexpand(True)
        left_info.append(self.archive_label)
        left_info.append(self.path_label)

        self.show_log_btn = Gtk.ToggleButton(label="Terminal & Log")
        self.show_log_btn.set_active(True)
        self.show_log_btn.connect("toggled", self.on_show_log_toggled)
        header.pack_end(self.show_log_btn)

        self.archive_status_right_label = Gtk.Label(label="Kein Archiv/Katalog geladen")
        self.archive_status_right_label.set_xalign(1)
        self.archive_status_right_label.add_css_class("dim-label")

        self.total_stats_label = Gtk.Label(label="")
        self.total_stats_label.set_xalign(1)
        self.total_stats_label.add_css_class("dim-label")

        right_info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        right_info.append(self.archive_status_right_label)
        right_info.append(self.total_stats_label)

        info = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        info.set_margin_top(8)
        info.set_margin_bottom(6)
        info.set_margin_start(10)
        info.set_margin_end(10)

        info.append(left_info)
        info.append(right_info)

        self.tree_selection = Gtk.SingleSelection()
        self.tree_selection.set_autoselect(True)
        self.tree_selection.connect("selection-changed", self.on_tree_selection_changed)

        tree_factory = Gtk.SignalListItemFactory()
        tree_factory.connect("setup", self.on_tree_setup)
        tree_factory.connect("bind", self.on_tree_bind)

        tree_view = Gtk.ListView.new(self.tree_selection, tree_factory)
        tree_view.add_css_class("archive-tree")

        click_controller = Gtk.GestureClick()
        click_controller.connect("pressed", self.on_tree_pressed)
        tree_view.add_controller(click_controller)
        tree_right_click = Gtk.GestureClick(button=3)
        tree_right_click.connect("pressed", self._on_tree_context_pressed)
        tree_view.add_controller(tree_right_click)
        tree_view.connect("activate", self.on_tree_activate)

        tree_scroll = Gtk.ScrolledWindow()
        tree_scroll.set_child(tree_view)

        self.right_selection = Gtk.MultiSelection()
        self.right_selection.connect("selection-changed", self.on_right_selection_changed)

        self.column_view = Gtk.ColumnView.new(self.right_selection)
        self.column_view.set_show_row_separators(True)
        self.column_view.set_show_column_separators(True)

        self._add_column("Name", self._name_factory(), 330, "name")
        self._add_column("Typ", self._type_factory(), 125, "type")
        self._add_column("Größe", self._size_factory(), 110, "size")
        self._add_column("Datum", self._date_factory(), 190, "date")
        self._add_column("Pfad", self._path_factory(), 500, "path")
        self.column_view.sort_by_column(self.column_view.get_columns().get_item(0), Gtk.SortType.ASCENDING)
        self.column_view.connect("activate", self.on_right_activate)
        right_click = Gtk.GestureClick(button=3)
        right_click.connect("pressed", self._on_right_context_pressed)
        self.column_view.add_controller(right_click)

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

        # --- UNTERER BEREICH: Aufgeteilt in Terminal (Links 2/3) und DAR-Ausgabe (Rechts 1/3) ---
        self.bottom_paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        self.bottom_paned.set_position(900) # Initiale Trennposition (ca. 2/3 von 1450 Breite)
        self.bottom_paned.set_vexpand(True)
        self.bottom_paned.set_hexpand(True)

        # 1. DAR und LTFS erhalten VERSCHIEDENE VTE-Terminals / Shells / PTYs.
        # Die beiden Tabs wechseln nur die Anzeige und lassen beide Prozesse laufen.
        self.terminal_tabs = Gtk.Notebook()
        self.terminal_tabs.set_hexpand(True)
        self.terminal_tabs.set_vexpand(True)
        self.terminal_tabs.set_scrollable(True)

        self.terminal_widget = EmbeddedTerminal()  # bestehender DAR-Kanal
        self.terminal_tabs.append_page(self.terminal_widget, Gtk.Label(label="DAR-Terminal"))

        self.ltfs_terminal_widget = EmbeddedTerminal()  # vollständig getrennte PTY
        ltfs_panel = self._build_ltfs_panel()
        self.terminal_tabs.append_page(ltfs_panel, Gtk.Label(label="LTFS-Terminal"))

        term_frame = Gtk.Frame(label="Interaktive Terminals (DAR / LTFS)")
        term_frame.set_child(self.terminal_tabs)
        self.term_frame = term_frame
        term_frame.set_vexpand(True)
        term_frame.set_hexpand(True)
        self.bottom_paned.set_start_child(term_frame)

        # 2. Rechte Seite: Bestehende DAR-Ausgabe (Log View)
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
        self.log_frame = log_frame
        self.bottom_paned.set_end_child(log_frame)

        bottom = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        bottom.append(self.progress)
        bottom.append(self.status_label)
        bottom.append(self.bottom_paned)

        main = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        main.append(info)
        main.append(paned)
        main.append(bottom)

        self.set_child(main)
        self._install_css()

    def _build_ltfs_panel(self):
        """Eigene LTFS-Bedienung über ein unabhängiges interaktives Terminal."""
        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        panel.set_margin_top(6)
        panel.set_margin_start(6)
        panel.set_margin_end(6)
        panel.set_margin_bottom(6)

        config = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        panel.append(config)

        device_label = Gtk.Label(label="Bandlaufwerk:")
        config.append(device_label)
        self.ltfs_device_entry = Gtk.Entry()
        self.ltfs_device_entry.set_placeholder_text("z. B. /dev/sg3")
        self.ltfs_device_entry.set_text(os.environ.get("DAR_EXPLORER_LTFS_DEVICE", ""))
        self.ltfs_device_entry.set_width_chars(17)
        self.ltfs_device_entry.set_hexpand(True)
        config.append(self.ltfs_device_entry)

        mount_label = Gtk.Label(label="Mountpoint:")
        config.append(mount_label)
        self.ltfs_mount_entry = Gtk.Entry()
        self.ltfs_mount_entry.set_text(os.environ.get("DAR_EXPLORER_LTFS_MOUNTPOINT", "/mnt/ltfs"))
        self.ltfs_mount_entry.set_width_chars(24)
        self.ltfs_mount_entry.set_hexpand(True)
        self.ltfs_mount_entry.connect("changed", lambda *_: self._refresh_ltfs_mount_state())
        config.append(self.ltfs_mount_entry)

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        panel.append(actions)
        self.ltfs_mount_button = Gtk.Button(label="Band mounten")
        self.ltfs_mount_button.connect("clicked", self._on_ltfs_mount_clicked)
        actions.append(self.ltfs_mount_button)
        self.ltfs_unmount_button = Gtk.Button(label="Band aushängen")
        self.ltfs_unmount_button.connect("clicked", self._on_ltfs_unmount_clicked)
        actions.append(self.ltfs_unmount_button)
        refresh = Gtk.Button(label="Status / Freigabe")
        refresh.connect("clicked", self._on_ltfs_status_clicked)
        actions.append(refresh)

        self.ltfs_status_label = Gtk.Label(label="LTFS-Status wird geprüft …")
        self.ltfs_status_label.set_xalign(0)
        self.ltfs_status_label.set_hexpand(True)
        self.ltfs_status_label.set_ellipsize(Pango.EllipsizeMode.END)
        actions.append(self.ltfs_status_label)

        explanation = Gtk.Label(
            label="Bandwechsel nur, wenn DAR auf das nächste Slice wartet. "
                  "Nach dem Aushängen vollständigen LTFS-Abschluss abwarten."
        )
        explanation.set_wrap(True)
        explanation.set_xalign(0)
        explanation.add_css_class("dim-label")
        panel.append(explanation)

        self.ltfs_terminal_widget.set_vexpand(True)
        panel.append(self.ltfs_terminal_widget)
        self._refresh_ltfs_mount_state()
        return panel

    def _refresh_ltfs_mount_state(self):
        """Mount-Zustand nur über /proc/self/mountinfo, niemals per Tape-Zugriff."""
        if not hasattr(self, "ltfs_mount_entry") or not hasattr(self, "ltfs_status_label"):
            return True
        mountpoint = self.ltfs_mount_entry.get_text().strip()
        if not mountpoint or not os.path.isabs(os.path.expanduser(mountpoint)):
            self.ltfs_status_label.set_text("Absoluten Mountpoint angeben")
            self.ltfs_mount_button.set_sensitive(False)
            self.ltfs_unmount_button.set_sensitive(False)
            return True
        fs_type = filesystem_at(mountpoint)
        if fs_type is None:
            self._ltfs_unmount_pending = False
            if self._ltfs_mount_pending:
                message = "LTFS-Mount angefordert – Terminal prüfen"
            elif self._ltfs_unmount_requested:
                message = "Nicht gemountet – LTFS-Abschluss im Terminal prüfen!"
            else:
                message = "Nicht gemountet"
            self.ltfs_status_label.set_text(message)
            self.ltfs_mount_button.set_sensitive(
                not self._ltfs_mount_pending and not self._ltfs_unmount_requested
            )
            self.ltfs_unmount_button.set_sensitive(False)
        elif is_ltfs_filesystem(fs_type):
            self._ltfs_mount_pending = False
            message = ("LTFS-Unmount angefordert – Terminal prüfen" if self._ltfs_unmount_pending
                       else f"LTFS gemountet ({fs_type})")
            self.ltfs_status_label.set_text(message)
            self.ltfs_mount_button.set_sensitive(False)
            self.ltfs_unmount_button.set_sensitive(not self._ltfs_unmount_pending)
        else:
            self.ltfs_status_label.set_text(f"Fremdes Dateisystem: {fs_type} – nicht aushängen")
            self.ltfs_mount_button.set_sensitive(False)
            self.ltfs_unmount_button.set_sensitive(False)
        return True

    def _on_ltfs_status_clicked(self, _button):
        """Status prüfen; Freigabe nach dem Unmount nur ausdrücklich bestätigen."""
        mountpoint = self.ltfs_mount_entry.get_text().strip()
        fs_type = filesystem_at(mountpoint) if mountpoint else None
        if self._ltfs_unmount_requested and fs_type is None:
            title = "Ist LTFS vollständig beendet?"
            detail = (
                "Die Mount-Tabelle enthält das LTFS-Dateisystem nicht mehr. "
                "Das allein beweist noch nicht, dass LTFS seinen Index "
                "vollständig geschrieben und das Laufwerk freigegeben hat. "
                "Bitte erst im LTFS-Terminal prüfen und danach freigeben."
            )
            reset = "Abschluss bestätigt"
            def release():
                self._ltfs_unmount_requested = False
                self._ltfs_unmount_pending = False
        elif self._ltfs_mount_pending and fs_type is None:
            title = "Fehlgeschlagenen Mount zurücksetzen?"
            detail = (
                "Bitte zuerst im LTFS-Terminal prüfen, ob der Mount "
                "wirklich fehlgeschlagen bzw. beendet ist. Ein noch laufender "
                "Mount darf nicht ein zweites Mal gestartet werden."
            )
            reset = "Fehlversuch zurücksetzen"
            def release():
                self._ltfs_mount_pending = False
        elif self._ltfs_unmount_pending and is_ltfs_filesystem(fs_type):
            title = "Fehlgeschlagenen Unmount zurücksetzen?"
            detail = (
                "Bitte im LTFS-Terminal prüfen, ob der Unmount wirklich "
                "fehlgeschlagen ist. Den Unmount nicht gleichzeitig mehrfach starten."
            )
            reset = "Fehlversuch zurücksetzen"
            def release():
                self._ltfs_unmount_pending = False
                self._ltfs_unmount_requested = False
        else:
            self._refresh_ltfs_mount_state()
            return

        dialog = Gtk.MessageDialog(
            transient_for=self, modal=True,
            message_type=Gtk.MessageType.WARNING,
            buttons=Gtk.ButtonsType.NONE, text=title, secondary_text=detail,
        )
        dialog.add_button("Abbrechen", Gtk.ResponseType.CANCEL)
        dialog.add_button(reset, Gtk.ResponseType.OK)
        def on_response(dlg, response_id):
            dlg.destroy()
            if response_id == Gtk.ResponseType.OK:
                release()
            self._refresh_ltfs_mount_state()
        dialog.connect("response", on_response)
        dialog.present()

    def _ltfs_send_command(self, command: str):
        """Exklusiv in LTFS-PTY schreiben, nicht ins DAR-Terminal."""
        self.ltfs_terminal_widget.terminal.feed_child((command + "\n").encode("utf-8"))
        self.terminal_tabs.set_current_page(1)
        self._force_show_log()

    def _on_ltfs_mount_clicked(self, _button):
        from ltfs_backend.operations import ltfs_executable
        try:
            mountpoint = validate_mountpoint(self.ltfs_mount_entry.get_text())
            if filesystem_at(mountpoint):
                raise ValueError("Der Mountpoint ist bereits belegt.")
            if not os.path.isdir(mountpoint):
                raise ValueError(f"Mountpoint existiert nicht als Verzeichnis: {mountpoint}")
            if self._ltfs_mount_pending:
                raise ValueError("Ein LTFS-Mount wurde bereits gestartet. Bitte das Terminal prüfen.")
            # LTFS wird im Hintergrund der LTFS-Shell gestartet: sie bleibt
            # für den späteren Unmount bedienbar, selbst wenn LTFS nicht daemonisiert.
            command = build_mount_command(
                self.ltfs_device_entry.get_text(), mountpoint, executable=ltfs_executable()
            )
            shell_line = shlex.join(command) + " &"
            self._ltfs_unmount_requested = False
            self._ltfs_mount_pending = True
            self._ltfs_send_command(shell_line)
            self._refresh_ltfs_mount_state()
            self.ltfs_status_label.set_text("LTFS-Mount gestartet – Terminalausgabe beachten")
        except (ValueError, FileNotFoundError) as exc:
            self._show_error("LTFS-Mount nicht gestartet", str(exc))

    def _on_ltfs_unmount_clicked(self, _button):
        try:
            mountpoint = validate_mountpoint(self.ltfs_mount_entry.get_text())
            fs_type = filesystem_at(mountpoint)
            if not is_ltfs_filesystem(fs_type):
                raise ValueError("Der angegebene Pfad ist nicht als LTFS gemountet.")
            command = build_unmount_command(mountpoint)
        except (ValueError, FileNotFoundError) as exc:
            self._show_error("LTFS-Unmount nicht gestartet", str(exc))
            return

        # Kein automatisches Unmount während eines DAR-Schreibvorgangs.
        # Der Zustand des DAR-Medienprompts lässt sich aus dem PTY nicht
        # zuverlässig auslesen; deshalb explizite Bestätigung durch den Nutzer.
        dialog = Gtk.MessageDialog(
            transient_for=self,
            modal=True,
            message_type=Gtk.MessageType.WARNING,
            buttons=Gtk.ButtonsType.NONE,
            text="LTFS-Band wirklich aushängen?",
            secondary_text=(
                "Nur fortfahren, wenn DAR das aktuelle Slice abgeschlossen hat "
                "und auf den Bandwechsel wartet. Ein laufender Schreibvorgang "
                "darf nicht unterbrochen werden. Vor dem Bandwechsel muss "
                "LTFS vollständig beendet sein."
            ),
        )
        dialog.add_button("Abbrechen", Gtk.ResponseType.CANCEL)
        dialog.add_button("Aushängen", Gtk.ResponseType.OK)
        def on_response(dlg, response_id):
            dlg.destroy()
            if response_id == Gtk.ResponseType.OK:
                self._ltfs_unmount_requested = True
                self._ltfs_unmount_pending = True
                self._ltfs_send_command(shlex.join(command))
                self._refresh_ltfs_mount_state()
                self.ltfs_status_label.set_text("Unmount gestartet – Abschluss im Terminal prüfen")
        dialog.connect("response", on_response)
        dialog.present()

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

    def _add_column(self, title, factory, width, sort_key):
        col = Gtk.ColumnViewColumn(title=title, factory=factory)
        col.set_resizable(True)
        col.set_fixed_width(width)
        # GTK ColumnView manages the clickable header and the sort arrow.
        col.set_sorter(Gtk.CustomSorter.new(
            lambda a, b, *_: self._compare_column(a, b, sort_key)
        ))
        self.column_view.append_column(col)

    @staticmethod
    def _compare_column(a, b, key):
        if key == "size":
            av, bv = a.size_bytes, b.size_bytes
        elif key == "date":
            av, bv = a.timestamp, b.timestamp
        elif key == "type":
            av, bv = a.type_text.casefold(), b.type_text.casefold()
        elif key == "path":
            av, bv = a.path.casefold(), b.path.casefold()
        else:
            av, bv = a.name.casefold(), b.name.casefold()
        return -1 if av < bv else (1 if av > bv else 0)

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

        is_catalog = self.check_if_catalog(path)
        self.is_current_catalog = is_catalog

        all_present = True if is_catalog else check_all_slices_present(path)

        def execute_load(target_path, is_cat):
            self.current_archive = target_path
            self.is_current_catalog = is_cat

            # Bei einem isolierten Katalog muss der exakte Katalogpfad erhalten
            # bleiben. Ein isolierter DAR-Katalog ist z.B.
            # /tmp/dar-explorer/catalogs/testarchiv.dar.1.dar.
            # Dieser Pfad darf später nicht aus dem Archivnamen rekonstruiert
            # oder um .dar-Endungen bereinigt werden.
            if is_cat:
                self.current_catalog_path = str(Path(target_path).resolve())
            else:
                self.current_catalog_path = None

            self.is_tape_active = False
            self.loading = True
            self._gui_file_count = 0

            self.update_archive_banner(is_catalog=is_cat)

            self.reload_btn.set_sensitive(False)
            self.extract_btn.set_sensitive(False)
            self.archive_label.set_text(f"Archiv: {self.current_archive}")
            self.path_label.set_text("DAR-Listing wird eingelesen …")
            self.status_label.set_text("DAR wird ausgeführt …")

            self.progress.set_fraction(0.0)
            self.progress.set_text("Analysiere Archivstruktur... (0 Einträge)")
            self.progress.set_visible(True)

            threading.Thread(
                target=self._load_worker, args=(self.current_archive,), daemon=True
            ).start()

        if is_catalog or all_present:
            execute_load(path, is_cat=is_catalog)
        else:
            dialog_window = Gtk.Window(title="Isolierten Katalog anlegen?", transient_for=self, modal=True)
            dialog_window.set_default_size(480, -1)

            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=15)
            box.set_margin_top(15)
            box.set_margin_bottom(15)
            box.set_margin_start(15)
            box.set_margin_end(15)
            dialog_window.set_child(box)

            lbl = Gtk.Label()
            lbl.set_markup(
                "<b>Die Slices dieses Archivs sind unvollständig oder es fehlt das End-Slice.</b>\n\n"
                "Möchtest du einen <b>isolierten Katalog</b> erzeugen?\n"
                "(Dafür wird das letzte Slice/Band benötigt. Danach kann das Band entfernt werden)."
            )
            lbl.set_wrap(True)
            lbl.set_xalign(0)
            box.append(lbl)

            btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            btn_box.set_halign(Gtk.Align.END)
            box.append(btn_box)

            btn_cancel = Gtk.Button(label="Abbrechen")
            btn_create = Gtk.Button(label="Katalog erstellen...")
            btn_create.add_css_class("suggested-action")
            btn_box.append(btn_cancel)
            btn_box.append(btn_create)

            def on_action(btn, create):
                dialog_window.close()
                if not create:
                    return

                cache_dir = Path("/tmp/dar-explorer/catalogs")
                cache_dir.mkdir(parents=True, exist_ok=True)
                catalog_target = cache_dir / f"{Path(path).stem}.dar"

                # Alten temporären Katalog vor Neuerstellung löschen
                if catalog_target.exists():
                    try:
                        catalog_target.unlink()
                    except OSError:
                        pass

                self._create_catalog_interactive(
                    path,
                    str(catalog_target),
                    lambda cat_path: execute_load(cat_path, is_cat=True) if cat_path else None
                )

            btn_cancel.connect("clicked", on_action, False)
            btn_create.connect("clicked", on_action, True)
            dialog_window.present()

    def _create_catalog_interactive(self, archive_path, catalog_target, callback):
        self.status_label.set_text("Erstelle isolierten Katalog...")
        self.progress.set_visible(True)
        self.progress.pulse()

        def worker():
            try:
                p_obj = Path(archive_path).resolve()
                clean_name = re.sub(r'\.\d+\.dar$', '', p_obj.name)
                archive_arg = str(p_obj.parent / clean_name)

                cache_dir = Path("/tmp/dar-explorer/catalogs")
                cache_dir.mkdir(parents=True, exist_ok=True)
                catalog_target_path = cache_dir / f"{clean_name}.dar"

                # Alle alten Slices/Dateien dieses Katalogs im Cache restlos aufräumen
                for old_file in cache_dir.glob(f"{clean_name}.dar*"):
                    try:
                        old_file.unlink()
                    except OSError:
                        pass

                dar_bin = get_dar_executable()

                cmd_args = [dar_bin, "-C", str(catalog_target_path), "-A", archive_arg, "-Q"]
                if self.archive_password:
                    cmd_args.extend(["-J", self.archive_password])

                self._append_log_threadsafe(f"[EXEC]: {' '.join(cmd_args)}")

                env = os.environ.copy()
                env["LC_ALL"] = "C"
                env["LANG"] = "C"

                process = subprocess.run(
                    cmd_args,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    env=env
                )

                full_output = ""
                if process.stdout:
                    for line in process.stdout.splitlines():
                        cleaned = line.strip()
                        if cleaned:
                            self._append_log_threadsafe(f"[dar]: {cleaned}")
                            full_output += cleaned + "\n"

                # DIREKTE PRÜFUNG HIER: Ist das End-Slice/die letzte Datei nicht da?
                if "The last file of the set is not present" in full_output:
                    def show_missing_slice_dialog():
                        self.loading = False
                        if hasattr(self, 'progress') and self.progress:
                            self.progress.set_visible(False)
                        if hasattr(self, 'status_label') and self.status_label:
                            self.status_label.set_text("Unvollständiges Archiv erkannt.")
                        if hasattr(self, 'reload_btn') and self.reload_btn:
                            self.reload_btn.set_sensitive(True)

                        # GTK-Dialog für das fehlende End-Slice
                        dialog_window = Gtk.Window(title="Unvollständiges Archiv", transient_for=self, modal=True)
                        dialog_window.set_default_size(480, -1)

                        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=15)
                        box.set_margin_top(15)
                        box.set_margin_bottom(15)
                        box.set_margin_start(15)
                        box.set_margin_end(15)
                        dialog_window.set_child(box)

                        lbl = Gtk.Label()
                        lbl.set_markup(
                            "<b>Das Archiv ist unvollständig (End-Slice fehlt).</b>\n\n"
                            "Für die Erstellung eines isolierten Katalogs wird das letzte Slice/Band benötigt.\n"
                            "Bitte lege das entsprechende Medium bereit."
                        )
                        lbl.set_wrap(True)
                        lbl.set_xalign(0)
                        box.append(lbl)

                        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
                        btn_box.set_halign(Gtk.Align.END)
                        box.append(btn_box)

                        btn_ok = Gtk.Button(label="Verstanden / Abbrechen")
                        btn_ok.add_css_class("suggested-action")
                        btn_box.append(btn_ok)

                        btn_ok.connect("clicked", lambda *_: (dialog_window.close(), setattr(self, 'loading', False)))
                        dialog_window.present()

                    GLib.idle_add(show_missing_slice_dialog)
                    GLib.idle_add(lambda: callback(None))
                    return

                if process.returncode == 0:
                    # DAR erzeugt bei -C <name>.dar nicht die Datei <name>.dar,
                    # sondern den eigentlichen isolierten Katalog als
                    # <name>.dar.1.dar. Der von uns übergebene Zielname ist
                    # daher NICHT automatisch der tatsächliche Dateipfad.
                    generated_catalogs = sorted(
                        cache_dir.glob(f"{clean_name}.dar.*.dar")
                    )

                    if generated_catalogs:
                        actual_catalog_path = generated_catalogs[-1].resolve()
                    else:
                        # Fallback für eine eventuell abweichende DAR-Ausgabe.
                        actual_catalog_path = catalog_target_path.resolve()

                    if not actual_catalog_path.is_file():
                        self._append_log_threadsafe(
                            f"[Fehler KATALOG]: DAR meldet Erfolg, aber der erzeugte Katalog wurde nicht gefunden: {actual_catalog_path}"
                        )
                        GLib.idle_add(
                            lambda: self._show_error(
                                "Katalog nicht gefunden",
                                "DAR hat den isolierten Katalog nicht unter dem erwarteten Dateinamen erzeugt."
                            )
                        )
                        GLib.idle_add(lambda: callback(None))
                        return

                    self._append_log_threadsafe(
                        f"[System]: Isolierter Katalog erfolgreich erstellt: {actual_catalog_path}"
                    )
                    # Dieser Katalog wurde von DAR Explorer im temporären Cache
                    # erzeugt und darf beim Beenden optional gelöscht werden.
                    self.temporary_catalog_path = actual_catalog_path
                    GLib.idle_add(lambda: callback(str(actual_catalog_path)))
                else:
                    error_output = process.stdout.lower() if process.stdout else ""
                    if any(kw in error_output for kw in ["password", "secu_string", "krypt", "auth", "encryption"]):
                        pwd = self._prompt_user_interactive_queue("Das Passwort ist falsch oder erforderlich:")
                        if pwd is not None:
                            self.archive_password = pwd
                            GLib.idle_add(lambda: self._create_catalog_interactive(archive_path, catalog_target, callback))
                            return

                    GLib.idle_add(lambda: callback(None))

            except Exception as e:
                self._append_log_threadsafe(f"[Fehler Katalog-Erstellung]: {e}")
                GLib.idle_add(lambda: callback(None))

        threading.Thread(target=worker, daemon=True).start()

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

            dar_bin = get_dar_executable()
            check_cmd = [dar_bin, "-n", "-l", archive_arg, "-Q", "-N"]

            if self.archive_password:
                check_cmd.extend(["-K", self.archive_password])

            self._append_log_threadsafe(f"[EXEC]: {' '.join(check_cmd)}")

            check_process = subprocess.Popen(
                check_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.PIPE,
                text=True,
                bufsize=1,
                env={"LC_ALL": "C", "LANG": "C"}
            )

            check_output_lines = []
            if check_process.stdout:
                for line in iter(check_process.stdout.readline, ''):
                    clean_line = line.strip()
                    if clean_line:
                        check_output_lines.append(clean_line)

            check_process.wait()
            returncode = check_process.returncode

            if returncode != 0:
                self._append_log_threadsafe("[System]: Archiv geschützt oder erfordert Passwort / Fehler aufgetreten:")
                for line in check_output_lines:
                    self._append_log_threadsafe(f"[dar]: {line}")

                pwd = self._prompt_user_interactive_queue("Bitte gib das Passwort für das Archiv ein:")
                if pwd is not None:
                    self.archive_password = pwd
                else:
                    raise RuntimeError("Archiv-Einlesen vom Benutzer abgebrochen.")

            entries, warning = run_dar_listing(
                path,
                password_callback=password_callback,
                progress_callback=progress_bridge,
                encryption_password=self.archive_password
            )
            GLib.idle_add(self._load_finished, path, entries, warning, None)

        except Exception as exc:
            GLib.idle_add(self._load_finished, path, None, "", str(exc))

    def _load_finished(self, path, entries, warning, error):
        self.loading = False
        self.reload_btn.set_sensitive(True)

        self.progress.set_visible(False)
        self.progress.set_fraction(0.0)
        self.progress.set_text("")

        if error:
            self.reset_archive_banner()
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
            self._set_right_nodes(list(archive_root_node.children))
        else:
            self._set_right_nodes(list(self.root_store))

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
                self._set_right_nodes(list(node.children))

                total = node.children.get_n_items()
                if total == 0:
                    self.status_label.set_text("Dieser Ordner ist im Archiv leer.")
                else:
                    self.status_label.set_text(f"{total:,} Einträge in diesem Verzeichnis.".replace(",", "."))
            else:
                self._set_right_nodes([])
                self.status_label.set_text("Dieser Ordner ist im Archiv leer.")

        self._update_extract_button()

    def on_right_selection_changed(self, _selection, _position, _n_items):
        self._update_extract_button()

    def on_tree_pressed(self, _gesture, _n_press, _x, _y):
        row = self.tree_selection.get_selected_item()
        if row and row.get_item():
            node = row.get_item()
            if node.path == "":
                self.right_selection.unselect_all()
                self._update_extract_button()

    def _update_extract_button(self):
        if self.extracting or self.loading:
            self.extract_btn.set_sensitive(False)
            return

        if self.current_archive is None:
            self.extract_btn.set_label("Auswahl extrahieren")
            self.extract_btn.set_sensitive(False)
            return

        if getattr(self, 'is_current_catalog', False):
            self.extract_btn.set_label("Aus Katalog extrahieren...")
            self.extract_btn.set_sensitive(True)
            self.extract_btn.set_tooltip_text("Extrahiert Dateien mithilfe des echten Archivs und dieses Katalogs.")
        else:
            self.extract_btn.set_label("Extrahieren")
            self.extract_btn.set_sensitive(True)
            self.extract_btn.set_tooltip_text(None)

        right_selection_count = 0
        if hasattr(self, 'right_selection') and self.right_selection:
            bitset = self.right_selection.get_selection()
            if bitset:
                right_selection_count = bitset.get_size()

        if right_selection_count > 0:
            self.extract_btn.set_label("Auswahl extrahieren")
            self.extract_btn.set_sensitive(True)
            return

        sidebar_model = self.tree_selection.get_model()
        sidebar_pos = self.tree_selection.get_selected()

        is_root_selected = False
        if sidebar_pos != 0xffffffff and sidebar_model:
            row = sidebar_model.get_item(sidebar_pos)
            if row and row.get_item() and row.get_item().path == "":
                is_root_selected = True

        if is_root_selected:
            self.extract_btn.set_label("Archiv extrahieren")
            self.extract_btn.set_sensitive(True)
        else:
            self.extract_btn.set_label("Auswahl extrahieren")
            self.extract_btn.set_sensitive(False)

    def on_search_changed(self, _entry):
        self._apply_search()

    def _set_right_nodes(self, nodes):
        self._current_nodes = list(nodes)
        store = Gio.ListStore.new(DarNode)
        for node in self._current_nodes:
            store.append(node)
        self._right_model_source = store

        def visible(node):
            query = self.search.get_text().strip().casefold() if hasattr(self, "search") else ""
            if query and query not in f"{node.name} {node.path}".casefold():
                return False
            if self._type_filter == "files" and node.is_dir:
                return False
            if self._type_filter == "dirs" and not node.is_dir:
                return False
            return True

        filt = Gtk.CustomFilter.new(lambda item: visible(item))
        filtered = Gtk.FilterListModel.new(store, filt)

        sorted_model = Gtk.SortListModel.new(filtered, self.column_view.get_sorter())
        self.right_selection.set_model(sorted_model)
        self.status_label.set_text(f"{sorted_model.get_n_items():,} Einträge".replace(",", "."))
        self._update_extract_button()

    def _on_view_filter_changed(self):
        selected = self.filter_dropdown.get_selected()
        self._type_filter = ["all", "files", "dirs"][selected if selected < 3 else 0]
        self._set_right_nodes(self._current_nodes)

    def on_search_changed(self, _entry):
        self._set_right_nodes(self._current_nodes)
        self.path_label.set_text(f"Suche: „{self.search.get_text()}“" if self.search.get_text().strip() else self.path_label.get_text())

    def _selected_right_nodes(self):
        result = []
        model = self.right_selection.get_model()
        bitset = self.right_selection.get_selection()
        if not model or not bitset:
            return result
        for i in range(model.get_n_items()):
            if bitset.contains(i):
                item = model.get_item(i)
                if item:
                    result.append(item)
        return result

    def _navigate_to_node(self, node):
        if not node or not node.is_dir:
            return
        self.search.set_text("")
        self._set_right_nodes(list(node.children))
        self.path_label.set_text(node.path or "Archivwurzel")
        self.status_label.set_text(f"{node.children.get_n_items():,} Einträge".replace(",", "."))

    def on_tree_activate(self, _view, position):
        row = self.tree_selection.get_model().get_item(position) if self.tree_selection.get_model() else None
        if row:
            node = row.get_item()
            if node and node.is_dir:
                self._navigate_to_node(node)

    def on_right_activate(self, _view, position):
        model = self.right_selection.get_model()
        if not model:
            return
        node = model.get_item(position)
        if not node:
            return
        self.right_selection.select_item(position, True)
        if node.is_dir:
            self._navigate_to_node(node)
        else:
            self._start_direct_extract([node.path])

    def _start_direct_extract(self, paths):
        if not paths:
            return
        dialog = Gtk.FileDialog(title="Zielverzeichnis für Extraktion wählen")
        dialog.select_folder(self, None, lambda d, r: self._finish_direct_extract(d, r, paths))

    def _finish_direct_extract(self, dialog, result, paths):
        try:
            folder = dialog.select_folder_finish(result)
            if not folder:
                return
            self._ask_for_permissions_mode(folder.get_path(), paths)
        except Exception as exc:
            self._show_error("Extraktion", str(exc))

    def _context_popover(self, node=None, selected=None, anchor=None):
        pop = Gtk.Popover()
        pop.set_parent(anchor or self.column_view)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        box.set_margin_top(6); box.set_margin_bottom(6); box.set_margin_start(6); box.set_margin_end(6)
        pop.set_child(box)
        selected = selected or ([node] if node else [])
        if selected:
            b = Gtk.Button(label="Extrahieren")
            b.connect("clicked", lambda *_: (pop.popdown(), self._start_direct_extract([n.path for n in selected])))
            box.append(b)
            if len(selected) == 1 and selected[0].is_dir:
                b2 = Gtk.Button(label="Verzeichnis öffnen")
                b2.connect("clicked", lambda *_: (pop.popdown(), self._navigate_to_node(selected[0])))
                box.append(b2)
            b3 = Gtk.Button(label="Details…")
            b3.connect("clicked", lambda *_: (pop.popdown(), self._show_node_details(selected[0])))
            box.append(b3)
        b4 = Gtk.Button(label="Alle Einträge dieses Ordners auswählen")
        b4.connect("clicked", lambda *_: (pop.popdown(), self._select_all_visible()))
        box.append(b4)
        pop.popup()

    def _on_right_context_pressed(self, gesture, n_press, x, y):
        if n_press != 1:
            return
        selected = self._selected_right_nodes()
        self._context_popover(selected=selected, anchor=self.column_view)
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)

    def _on_tree_context_pressed(self, gesture, n_press, x, y):
        if n_press != 1:
            return
        row = self.tree_selection.get_selected_item()
        node = row.get_item() if row else None
        self._context_popover(node=node, anchor=self.column_view)
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)

    def _select_all_visible(self):
        model = self.right_selection.get_model()
        if model:
            self.right_selection.select_all()

    def _show_node_details(self, node):
        dialog = Gtk.Window(title="Dateidetails", transient_for=self, modal=True)
        dialog.set_default_size(620, 430)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_top(16); box.set_margin_bottom(16); box.set_margin_start(16); box.set_margin_end(16)
        dialog.set_child(box)
        text = Gtk.TextView(); text.set_editable(False); text.set_monospace(True); text.set_vexpand(True)
        buf = text.get_buffer()
        buf.set_text("\n".join([
            f"Name:        {node.name}", f"Pfad:        {node.path}", f"Typ:         {node.type_text}",
            f"Größe:       {node.size}", f"Datum:       {node.date}", f"Berechtigungen:{node.permissions}",
            f"Benutzer:    {node.user}", f"Gruppe:      {node.group}",
            "", f"Archiv:      {self.current_archive or '—'}",
        ]))
        scroll=Gtk.ScrolledWindow(); scroll.set_child(text); box.append(scroll)
        close=Gtk.Button(label="Schließen"); close.set_halign(Gtk.Align.END); close.connect("clicked", lambda *_: dialog.close()); box.append(close)
        dialog.present()

    def _show_archive_metadata(self, _button=None):
        if not self.current_archive:
            return
        dialog = Gtk.Window(title="DAR-Metadaten", transient_for=self, modal=True)
        dialog.set_default_size(760, 560)
        box=Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8); box.set_margin_top(12); box.set_margin_bottom(12); box.set_margin_start(12); box.set_margin_end(12); dialog.set_child(box)
        text=Gtk.TextView(); text.set_editable(False); text.set_monospace(True); text.set_vexpand(True)
        scroll=Gtk.ScrolledWindow(); scroll.set_child(text); box.append(scroll)
        close=Gtk.Button(label="Schließen"); close.set_halign(Gtk.Align.END); close.connect("clicked", lambda *_: dialog.close()); box.append(close)
        text.get_buffer().set_text("Lese DAR-Metadaten …")
        def worker():
            try:
                cmd=[get_dar_executable(), "-l", normalize_archive_argument(self.current_archive)]
                if self.archive_password:
                    cmd.extend(["-K", self.archive_password])
                proc=subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env={**os.environ,"LC_ALL":"C","LANG":"C"})
                out=proc.stdout or ""
            except Exception as exc:
                out=f"Fehler: {exc}"
            GLib.idle_add(text.get_buffer().set_text, out[-30000:] if len(out)>30000 else out)
        threading.Thread(target=worker, daemon=True).start(); dialog.present()

    def _index_path(self):
        if not self.current_archive:
            return None
        import hashlib
        p=Path(normalize_archive_argument(self.current_archive))
        try:
            st=p.stat()
            key=f"{p.resolve()}|{st.st_mtime_ns}|{st.st_size}".encode()
        except OSError:
            key=str(p.resolve()).encode()
        digest=hashlib.sha256(key).hexdigest()
        root=Path.home()/".cache"/"dar-explorer"/"index"
        root.mkdir(parents=True, exist_ok=True)
        return root/f"{digest}.sqlite"

    def _start_background_index(self, _button=None):
        if not self.all_nodes or not self.current_archive or self._index_thread and self._index_thread.is_alive():
            return
        self._index_enabled=True
        index_path=self._index_path()
        if not index_path:
            return
        nodes=list(self.all_nodes)
        self.progress.set_visible(True); self.progress.set_fraction(0.0); self.progress.set_text("Baue Hintergrundindex …")
        self.status_label.set_text("Hintergrundindex wird erstellt …")
        def worker():
            try:
                con=sqlite3.connect(index_path)
                con.execute("CREATE TABLE IF NOT EXISTS entries (path TEXT PRIMARY KEY, name TEXT, is_dir INTEGER, size_bytes INTEGER, timestamp INTEGER, permissions TEXT, user TEXT, group_name TEXT)")
                con.execute("DELETE FROM entries")
                total=max(1,len(nodes))
                for i,n in enumerate(nodes,1):
                    con.execute("INSERT OR REPLACE INTO entries VALUES (?,?,?,?,?,?,?,?)",(n.path,n.name,int(n.is_dir),int(n.size_bytes),int(n.timestamp),n.permissions,n.user,n.group))
                    if i%250==0 or i==total:
                        GLib.idle_add(self.progress.set_fraction,i/total)
                        GLib.idle_add(self.progress.set_text,f"Index: {i}/{total} ({int(i/total*100)}%)")
                con.commit(); con.close()
                self._index_ready=True
                GLib.idle_add(self.status_label.set_text,f"Hintergrundindex bereit: {index_path}")
            except Exception as exc:
                GLib.idle_add(self.status_label.set_text,f"Hintergrundindex fehlgeschlagen: {exc}")
            finally:
                GLib.idle_add(self.progress.set_visible,False)
        self._index_thread=threading.Thread(target=worker,daemon=True); self._index_thread.start()

    def _show_integrity_result(self, ok, output):
        title="Integritätsprüfung erfolgreich" if ok else "Integritätsprüfung fehlgeschlagen"
        dialog=Gtk.Window(title=title, transient_for=self, modal=True); dialog.set_default_size(760,520)
        box=Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8); box.set_margin_top(12); box.set_margin_bottom(12); box.set_margin_start(12); box.set_margin_end(12); dialog.set_child(box)
        label=Gtk.Label(label="Das Archiv wurde ohne DAR-Fehler geprüft." if ok else "DAR meldet einen Fehler beim Prüfen des Archivs."); label.set_xalign(0); label.set_wrap(True); box.append(label)
        text=Gtk.TextView(); text.set_editable(False); text.set_monospace(True); text.set_vexpand(True); text.get_buffer().set_text(output[-30000:]); scroll=Gtk.ScrolledWindow(); scroll.set_child(text); box.append(scroll)
        close=Gtk.Button(label="Schließen"); close.set_halign(Gtk.Align.END); close.connect("clicked", lambda *_: dialog.close()); box.append(close); dialog.present()

    def _run_integrity_check(self, _button=None):
        if not self.current_archive or self.loading or self.extracting: return
        self._force_show_log(); self.progress.set_visible(True); self.progress.pulse(); self.progress.set_text("Prüfe Archivintegrität …")
        archive=self.current_archive
        def worker():
            try:
                cmd=[get_dar_executable(), "-t", normalize_archive_argument(archive)]
                proc=subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env={**os.environ,"LC_ALL":"C","LANG":"C"})
                out=proc.stdout or ""; ok=proc.returncode==0
            except Exception as exc:
                out=str(exc); ok=False
            GLib.idle_add(self.progress.set_visible, False)
            GLib.idle_add(self.status_label.set_text, "Integritätsprüfung erfolgreich." if ok else "Integritätsprüfung fehlgeschlagen.")
            GLib.idle_add(self._show_integrity_result, ok, out)
        threading.Thread(target=worker, daemon=True).start()

    def _show_slice_overview(self, _button=None):
        if not self.current_archive: return
        base=normalize_archive_argument(self.current_archive)
        p=Path(base); directory=p.parent; stem=p.name
        rows=[]
        for f in sorted(directory.glob(stem+".*.dar")):
            m=re.search(r"\.(\d+)\.dar$", f.name)
            if m:
                rows.append((int(m.group(1)), f, f.stat().st_size))
        rows.sort()
        max_no=rows[-1][0] if rows else 0
        present={n for n,_,_ in rows}
        missing=[n for n in range(1,max_no+1) if n not in present]
        dialog=Gtk.Window(title="Slice-Übersicht", transient_for=self, modal=True); dialog.set_default_size(720,520)
        box=Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8); box.set_margin_top(12); box.set_margin_bottom(12); box.set_margin_start(12); box.set_margin_end(12); dialog.set_child(box)
        summary=f"Archiv: {base}\nVorhanden: {len(rows)} Slices" + (f"\nFehlend: {', '.join(map(str,missing))}" if missing else "\nKeine Lücken erkannt.")
        lab=Gtk.Label(label=summary); lab.set_xalign(0); lab.set_selectable(True); box.append(lab)
        view=Gtk.TextView(); view.set_editable(False); view.set_monospace(True); view.set_vexpand(True)
        lines=[f"{'Slice':>8}  {'Größe':>16}  Datei"]
        for n,f,size in rows: lines.append(f"{n:>8}  {size:>16,}  {f.name}".replace(",","."))
        view.get_buffer().set_text("\n".join(lines)); scroll=Gtk.ScrolledWindow(); scroll.set_child(view); box.append(scroll)
        close=Gtk.Button(label="Schließen"); close.set_halign(Gtk.Align.END); close.connect("clicked", lambda *_: dialog.close()); box.append(close); dialog.present()

    def on_open_clicked(self, _button):
        dialog = Gtk.FileDialog()
        dialog.set_title("DAR-Archiv auswählen")
        dialog.open(self, None, self._open_done)

    def on_create_archive_clicked(self, _button):
        """Öffnet den modularen DAR-Archiv-Erstellungsdialog."""
        dialog = ArchiveCreateDialog(self, self._create_archive_in_terminal)
        dialog.present()

    def _create_archive_in_terminal(self, options: ArchiveCreateOptions, argv: list[str], command: str):
        """Startet DAR ausschließlich in der bereits vorhandenen VTE-PTY.

        Bewusst kein subprocess/Pipe: DAR kann Passwort-, Medien- und
        Slice-Fragen direkt interaktiv im echten Terminal stellen.
        """
        if not hasattr(self, "terminal_widget") or not self.terminal_widget:
            self._show_error("Terminal nicht verfügbar", "Das integrierte Terminal ist nicht verfügbar.")
            return

        self._append_log_threadsafe("[DAR CREATE]: Neues Archiv wird im interaktiven Terminal gestartet.")
        self._append_log_threadsafe(f"[DAR CREATE]: {command}")
        self._append_log_threadsafe(
            "[DAR CREATE]: Passwörter werden nicht in die Kommandozeile geschrieben; "
            "DAR fragt sie direkt im Terminal ab."
        )

        # Ein explizites cd verhindert, dass relative DAR-Referenzen vom
        # aktuellen Shell-Arbeitsverzeichnis abhängen. Alle übergebenen Pfade
        # sind bereits absolut und sicher shell-quotiert.
        cwd = str(Path(options.source).expanduser().resolve())
        shell_line = f"cd -- {shlex.quote(cwd)} && {command}"
        self.terminal_widget.terminal.feed_child((shell_line + "\n").encode("utf-8"))
        self.terminal_tabs.set_current_page(0)
        self.status_label.set_text("DAR läuft interaktiv im Terminal …")
        self._force_show_log()

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

    def _execute_extraction_with_catalog_reference(
        self,
        real_archive_arg,
        catalog_path,
        dest_dir,
        paths_to_extract,
        archive_password=None,
        restore_ownership=False,
    ):
        """Extrahiert aus einem echten DAR-Archiv mit einem isolierten Katalog.

        Wichtig:
        - real_archive_arg ist die DAR-Archivbasis, z.B. /.../testarchiv
        - catalog_path ist der vollständige isolierte Katalog, z.B.
          /tmp/dar-explorer/catalogs/testarchiv.dar.1.dar
        - Der Katalogpfad wird niemals aus dem Archivpfad rekonstruiert und
          seine .dar/.1.dar-Endungen werden niemals entfernt.
        """
        self.status_label.set_text("Starte Extraktion direkt im Terminal...")
        self.progress.set_visible(False)

        try:
            from urllib.parse import urlparse
            from urllib.request import url2pathname

            # URI -> lokaler Pfad
            if real_archive_arg and str(real_archive_arg).startswith("file://"):
                real_archive_arg = url2pathname(urlparse(str(real_archive_arg)).path)

            if catalog_path and str(catalog_path).startswith("file://"):
                catalog_path = url2pathname(urlparse(str(catalog_path)).path)

            # Bekannte /temp-Fehlinterpretation korrigieren.
            if catalog_path and str(catalog_path).startswith("/temp/"):
                catalog_path = str(catalog_path).replace("/temp/", "/tmp/", 1)

            real_archive_arg = str(Path(real_archive_arg).resolve())
            catalog_arg = str(Path(catalog_path).resolve())
            dest_dir = str(Path(dest_dir).resolve())

            # Der Katalog MUSS eine konkrete Datei sein.
            # Ein Verzeichnis darf hier niemals stillschweigend an -A
            # übergeben werden.
            catalog_file = Path(catalog_arg)
            if not catalog_file.is_file():
                self._append_log_threadsafe(
                    f"[Fehler KATALOG]: Katalogdatei nicht gefunden: {catalog_arg}"
                )
                self._show_error(
                    "Katalog nicht gefunden",
                    f"Die isolierte Katalogdatei wurde nicht gefunden:\n{catalog_arg}"
                )
                return

            # Das reale Archiv wird als DAR-Basis übergeben. Bei einer
            # Auswahl testarchiv.1.dar wird daraus /.../testarchiv.
            self._append_log_threadsafe(
                f"[DEBUG ARCHIV]: -x {real_archive_arg}"
            )
            self._append_log_threadsafe(
                f"[DEBUG KATALOG]: -A {catalog_arg}"
            )

            dar_bin = get_dar_executable()

            # Passwort explizit bevorzugen; ansonsten das aktuell gespeicherte.
            password = archive_password or getattr(self, "archive_password", None)

            if not hasattr(self, "terminal_widget") or not self.terminal_widget:
                self._show_error(
                    "Fehler",
                    "Terminal-Widget ist in der UI nicht verfügbar."
                )
                return

            # WICHTIG: Alle -g-Selektionspfade werden in EINEN DAR-Aufruf
            # aufgenommen. Mehrere aufeinanderfolgende Shell-Kommandos wären
            # bei interaktiven DAR-Prompts gefährlich: Eingaben wie Passwort
            # oder "Enter für nächstes Slice" könnten sonst vom nächsten
            # bereits gepufferten Shell-Kommando verschluckt werden.
            cmd_args = [dar_bin]

            if password:
                # -K ist der Schlüssel für das eigentliche Archiv, -J wäre
                # dagegen ausschließlich der Schlüssel des Referenzarchivs.
                # Bei einem isolierten Katalog ist -A typischerweise ein
                # unverschlüsselter Katalog; das echte Archiv kann trotzdem
                # verschlüsselt sein.
                cmd_args.extend(["-K", password])

            cmd_args.extend([
                "-x", real_archive_arg,
                "-A", catalog_arg,
                "-R", dest_dir,
            ])

            if not restore_ownership:
                cmd_args.append("-O")

            for path in paths_to_extract:
                if path:
                    cmd_args.extend(["-g", path])

            full_command_str = " ".join(
                shlex.quote(str(arg)) for arg in cmd_args
            )

            self._append_log_threadsafe(
                f"[DEBUG EXAKTER BEFEHL]: {full_command_str}"
            )
            self._append_log_threadsafe(
                "[EXEC Extraktion aus isoliertem Katalog direkt im Terminal]: "
                + full_command_str
            )
            self._append_log_threadsafe(
                "[System]: DAR kann nun fehlende Archiv-Slices/Bänder selbst "
                "interaktiv im Terminal anfordern."
            )

            self.terminal_widget.terminal.feed_child(
                (full_command_str + "\n").encode("utf-8")
            )

        except Exception as e:
            self._append_log_threadsafe(f"[Fehler Extraktion]: {e}")
            self._show_error("Fehler", f"Ausnahmefehler: {e}")

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

        dialog = Gtk.FileDialog(title="Zielverzeichnis für Extraktion wählen")

        def on_folder_picked(dialog_obj, result):
            try:
                folder = dialog_obj.select_folder_finish(result)
                if folder:
                    destination = folder.get_path()

                    is_isolated_catalog = getattr(self, 'is_current_catalog', False)
                    if is_isolated_catalog:
                        file_dialog = Gtk.FileDialog(title="Echtes Archiv (Slice) auswählen")

                        def on_file_picked(file_dialog_obj, file_result):
                            try:
                                file_obj = file_dialog_obj.open_finish(file_result)
                                if file_obj:
                                    file_path = Path(file_obj.get_path())

                                    filename = file_path.name
                                    clean_base_name = re.sub(r'\.\d+\.dar$', '', filename)
                                    clean_base_name = re.sub(r'\.dar$', '', clean_base_name)
                                    real_archive_arg = str(file_path.parent / clean_base_name)

                                    # Der beim Laden gespeicherte Pfad ist bereits der
                                    # vollständige isolierte Katalog, z.B.
                                    # /tmp/dar-explorer/catalogs/testarchiv.dar.1.dar.
                                    # NICHT aus dem Archivnamen rekonstruieren und NICHT
                                    # .dar/.1.dar entfernen.
                                    catalog_arg = getattr(self, 'current_catalog_path', None)

                                    if not catalog_arg:
                                        self._append_log_threadsafe(
                                            "[Fehler]: Kein Pfad zum aktuell geladenen isolierten Katalog vorhanden."
                                        )
                                        self._show_error(
                                            "Katalogpfad fehlt",
                                            "Der Pfad des geladenen isolierten Katalogs konnte nicht ermittelt werden."
                                        )
                                        return

                                    catalog_arg = str(Path(catalog_arg).resolve())

                                    self._append_log_threadsafe(
                                        f"[DEBUG KATALOG]: Exakter -A-Pfad: {catalog_arg}"
                                    )
                                    self._ask_for_permissions_mode(
                                        destination,
                                        paths_to_extract,
                                        real_archive_arg,
                                        catalog_arg
                                    )

                            except Exception as e:
                                self._append_log_threadsafe(f"Fehler bei der Dateiauswahl: {e}")

                        file_dialog.open(self, None, on_file_picked)
                    else:
                        self._ask_for_permissions_mode(destination, paths_to_extract)

            except Exception as e:
                self._append_log_threadsafe(f"Fehler bei der Ordnerauswahl: {e}")

        dialog.select_folder(self, None, on_folder_picked)

    def _ask_for_permissions_mode(self, destination, paths_to_extract, real_archive_arg=None, catalog_path=None):
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

            if real_archive_arg and catalog_path:
                self._execute_extraction_with_catalog_reference(
                    real_archive_arg, catalog_path, destination, paths_to_extract, restore_ownership=restore_ownership
                )
            else:
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
            selected = {p.rstrip("/") for p in paths_to_extract if p}
            total_expected_files = sum(1 for n in self.all_nodes if any(n.path == p or n.path.startswith(p + "/") for p in selected))
            total_expected_files = max(total_expected_files, len(paths_to_extract))

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
            dar_bin = get_dar_executable()
            clean_archive = normalize_archive_argument(self.current_archive)

            cmd = [dar_bin, "-x", clean_archive, "-R", destination, "-w"]

            if not restore_ownership:
                cmd.append("-O")

            if self.archive_password:
                cmd.extend(["-K", self.archive_password])

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
                GLib.idle_add(self._extract_finished, f"Extraktion fehlgeschlagen (Exit-Code {returncode}).")

        except Exception as e:
            self._append_log_threadsafe(f"[Fehler bei Extraktion]: {e}")
            GLib.idle_add(self._extract_finished, f"Fehler: {e}")

    def _prompt_user_interactive_queue(self, prompt_text: str) -> str | None:
        res_queue = queue.Queue()
        GLib.idle_add(self._show_prompt_dialog, prompt_text, res_queue)

        while True:
            try:
                val = res_queue.get(timeout=0.2)
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
        if hasattr(self, 'bottom_paned'):
            self.bottom_paned.set_visible(button.get_active())

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


    def _on_close_request(self, _window):
        """Behandelt das Schließen des Fensters und räumt temporäre Kataloge auf."""
        temp_path = getattr(self, "temporary_catalog_path", None)

        if not temp_path or not Path(temp_path).is_file():
            return False  # normales GTK-Schließen

        dialog = Gtk.Window(
            title="Temporären Katalog sichern?",
            transient_for=self,
            modal=True,
        )
        dialog.set_default_size(520, -1)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=15)
        box.set_margin_top(18)
        box.set_margin_bottom(18)
        box.set_margin_start(18)
        box.set_margin_end(18)
        dialog.set_child(box)

        label = Gtk.Label()
        label.set_markup(
            "<b>Es existiert noch ein temporärer isolierter Katalog.</b>\n\n"
            "Möchtest du ihn vor dem Beenden sichern?\n\n"
            f"<tt>{GLib.markup_escape_text(str(temp_path))}</tt>"
        )
        label.set_wrap(True)
        label.set_xalign(0)
        box.append(label)

        button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        button_box.set_halign(Gtk.Align.END)
        box.append(button_box)

        btn_cancel = Gtk.Button(label="Abbrechen")
        btn_discard = Gtk.Button(label="Verwerfen und schließen")
        btn_save = Gtk.Button(label="Sichern...")
        btn_save.add_css_class("suggested-action")

        button_box.append(btn_cancel)
        button_box.append(btn_discard)
        button_box.append(btn_save)

        def finish_close():
            dialog.close()
            self._delete_temporary_catalog()
            self.close()

        def on_cancel(_button):
            dialog.close()

        def on_discard(_button):
            finish_close()

        def on_save(_button):
            save_dialog = Gtk.FileDialog(title="Isolierten Katalog sichern")
            save_dialog.set_initial_name(Path(temp_path).name)

            def on_save_finished(dialog_obj, result):
                try:
                    destination = dialog_obj.save_finish(result)
                except GLib.Error as e:
                    # Abbruch des Dateidialogs ist kein Fehler und lässt das
                    # Hauptfenster geöffnet.
                    if e.code != 0:
                        return
                    return
                except Exception:
                    return

                if destination is None:
                    return

                destination_path = destination.get_path()
                if not destination_path:
                    self._show_error(
                        "Speichern fehlgeschlagen",
                        "Der ausgewählte Speicherort ist kein lokaler Dateipfad."
                    )
                    return

                try:
                    src = Path(temp_path)
                    dst = Path(destination_path)
                    if src.resolve() == dst.resolve():
                        # Gleiche Datei: nichts kopieren, anschließend trotzdem
                        # als gesichert behandeln und den temporären Katalog
                        # entfernen.
                        pass
                    else:
                        dst.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(src, dst)

                    self._append_log_threadsafe(
                        f"[System]: Isolierter Katalog gesichert: {dst}"
                    )
                    finish_close()
                except Exception as e:
                    self._show_error(
                        "Speichern fehlgeschlagen",
                        f"Der isolierte Katalog konnte nicht gesichert werden:\n{e}"
                    )

            save_dialog.save(self, None, on_save_finished)

        btn_cancel.connect("clicked", on_cancel)
        btn_discard.connect("clicked", on_discard)
        btn_save.connect("clicked", on_save)
        dialog.present()

        # Das Fenster bleibt geöffnet, bis der Benutzer eine der Aktionen
        # ausdrücklich auswählt.
        return True

    def _delete_temporary_catalog(self):
        """Löscht ausschließlich den aktuell von DAR Explorer erzeugten Katalog."""
        temp_path = getattr(self, "temporary_catalog_path", None)
        if not temp_path:
            return

        try:
            path = Path(temp_path)
            if path.is_file():
                path.unlink()
                self._append_log_threadsafe(
                    f"[System]: Temporärer isolierter Katalog gelöscht: {path}"
                )
        except OSError as e:
            self._append_log_threadsafe(
                f"[Warnung]: Temporärer isolierter Katalog konnte nicht gelöscht werden: {e}"
            )
        finally:
            self.temporary_catalog_path = None

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
