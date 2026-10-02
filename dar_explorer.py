#!/usr/bin/env python3
"""
DAR Explorer V2 - GTK4 frontend for DAR.
Teil 1: Importe und Tree-Builder
"""

from __future__ import annotations
import os
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
    # tree_structure speichert: { "Absoluter_Elternpfad": [Liste von direkten DarNode-Kindern] }
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
            # Absoluter Vollpfad sichert die Eindeutigkeit bei gleichen Ordnernamen
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
                # Nur Metadaten aktualisieren, falls der Knoten zuvor virtuell erstellt wurde
                node = nodes[current_path]
                node.is_dir = entry.is_dir
                node.permissions = entry.permissions
                node.user = entry.user
                node.group = entry.group
                node.size = entry.size
                node.date = entry.date

            parent_path = current_path

    # Wenn die reine Python-Struktur steht, befüllen wir die GTK-Stores in linearer Zeit
    def populate_gtk_children(node):
        children_nodes = tree_structure.get(node.path, [])
        if children_nodes:
            children_nodes.sort(key=lambda n: (not n.is_dir, n.name.casefold()))
            for child in children_nodes:
                populate_gtk_children(child)
                node.children.append(child)

    roots.sort(key=lambda n: (not n.is_dir, n.name.casefold()))
    root_store = Gio.ListStore.new(DarNode)

    # KORREKTUR: Virtuellen Master-Knoten für das gesamte Archiv erstellen
    archive_root = DarNode(
        "📦 [Gesamtes Archiv entpacken]",
        "",  # Ein absolut leerer Pfad symbolisiert die Wurzel
        True, "drwxr-xr-x", "root", "root", "—", "—"
    )

    for root_node in roots:
        populate_gtk_children(root_node)
        archive_root.children.append(root_node)

    root_store.append(archive_root)
    return root_store, list(nodes.values())





class DarExplorerWindow(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app)
        self.set_default_size(1450, 850)
        self.set_title("DAR Explorer V2")

        self.current_archive = None
        self.archive_password = None  # NEU: Speichert das eingegebene Passwort im Hauptfenster
        self.all_nodes = []
        self.root_store = None
        self.loading = False
        self.extracting = False
        self._gui_file_count = 0  # Falls noch nicht vorhanden, für den Tacho

        self._build_ui()


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

        self.archive_label = Gtk.Label(label="Kein Archiv geöffnet")
        self.archive_label.set_xalign(0)
        self.archive_label.add_css_class("dim-label")

        # NEU: Das dauerhafte Label für die Gesamtstatistik oben rechts erstellen
        self.total_stats_label = Gtk.Label(label="")
        self.total_stats_label.set_xalign(1)  # Rechtsbündig ausrichten
        self.total_stats_label.add_css_class("dim-label")

        self.path_label = Gtk.Label(label="")
        self.path_label.set_xalign(0)
        self.path_label.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        self.path_label.add_css_class("path-label")

        # Die Pfad-Infos kommen in eine interne vertikale Box auf die linke Seite
        left_info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        left_info.set_hexpand(True)  # Dehnt sich aus, um das Statistik-Label nach rechts zu schieben
        left_info.append(self.archive_label)
        left_info.append(self.path_label)

        # Das Haupt-Infoband unter der Headerbar wird HORIZONTAL
        info = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        info.set_margin_top(8)
        info.set_margin_bottom(6)
        info.set_margin_start(10)
        info.set_margin_end(10)

        info.append(left_info)
        info.append(self.total_stats_label)  # Das Statistik-Label sitzt jetzt permanent ganz rechts


        self.tree_selection = Gtk.SingleSelection()
        self.tree_selection.set_autoselect(True)
        self.tree_selection.connect(
            "selection-changed", self.on_tree_selection_changed
        )

        tree_factory = Gtk.SignalListItemFactory()
        tree_factory.connect("setup", self.on_tree_setup)
        tree_factory.connect("bind", self.on_tree_bind)

        tree_view = Gtk.ListView.new(self.tree_selection, tree_factory)
        tree_view.add_css_class("archive-tree")

        tree_scroll = Gtk.ScrolledWindow()
        tree_scroll.set_child(tree_view)

        self.right_selection = Gtk.MultiSelection()
        self.right_selection.connect(
            "selection-changed", self.on_right_selection_changed
        )

        # KORREKTUR: Zuerst definieren, dann Eigenschaften setzen
        self.column_view = Gtk.ColumnView.new(self.right_selection)
        self.column_view.set_show_row_separators(True)
        self.column_view.set_show_column_separators(True)

        self._add_column("Name", self._name_factory(), 330)
        self._add_column("Typ", self._type_factory(), 125)
        self._add_column("Größe", self._size_factory(), 110)
        self._add_column("Datum", self._date_factory(), 190)
        self._add_column("Pfad", self._path_factory(), 500)

        right_scroll = Gtk.ScrolledWindow()
        # KORREKTUR: Dem rechten Fenster eine Mindesthöhe geben und es vertikal expandieren lassen
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
        # KORREKTUR: Properties müssen als Key-Word-Arguments übergeben werden
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
            # KORREKTUR: Von 1 (rechts) auf 0 (links) setzen für Linksbündigkeit
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
        self.current_archive = path
        self.loading = True

        # Internen Zähler für die GUI zurücksetzen
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

    # KORREKTUR: 'count' ist jetzt optional (=None). Das verhindert den "missing argument"-Fehler komplett,
    # egal wie oder von wo GTK diese Methode im Hauptthread ansteuert!
    def _pulse_progress(self, count=None):
        if not self.loading:
            return False

        self.progress.pulse()

        # Wenn eine neue Zahl reinkommt, updaten wir unseren internen Speicher
        if count is not None:
            self._gui_file_count = count

        formatted_count = f"{self._gui_file_count:,}".replace(",", ".")
        self.progress.set_text(f"Analysiere Archivstruktur... ({formatted_count} Einträge eingelesen)")

        return False  # WICHTIG: Sagt GTK, dass die Funktion NICHT automatisch wiederholt werden soll


    def _load_worker(self, path):
        try:
            def password_callback(prompt_text):
                return self._prompt_user_interactive_queue(prompt_text)

            def progress_bridge(*args):
                count = args[0] if args else None
                GLib.idle_add(self._pulse_progress, count)

            archive_arg = normalize_archive_argument(path)

            # KORREKTUR: shlex.quote schützt Pfade mit Leerzeichen vor der Shell.
            # shell=True stellt sicher, dass 'dar' im System-PATH gefunden wird!
            import shlex
            check_cmd = f"dar -l {shlex.quote(archive_arg)} -Q"

            check_process = subprocess.Popen(
                check_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=True,
                env={"LC_ALL": "C", "LANG": "C"}
            )
            _, check_stderr = check_process.communicate()
            returncode = check_process.wait()

            # Wenn der Vorab-Check fehlschlägt, ist das Archiv verschlüsselt
            if returncode != 0:
                GLib.idle_add(self._force_show_log)
                GLib.idle_add(self._append_log, "[System]: Archiv geschützt. Passwort wird benötigt...")

                pwd = self._prompt_user_interactive_queue("Bitte gib das Passwort für das Archiv ein:")
                if pwd is not None:
                    self.archive_password = pwd
                    # DEBUG: Prüfen, ob das Passwort nach dem Popup im Thread existiert
                    print(f"[DEBUG GUI] Passwort empfangen: {'*' * len(pwd)} (Länge: {len(pwd)})")
                else:
                    raise RuntimeError("Archiv-Einlesen vom Benutzer abgebrochen.")

            # DEBUG: Vor dem Aufruf der Pipeline prüfen, was übergeben wird
            print(f"[DEBUG GUI] Starte run_dar_listing mit Passwort-Zustand: {self.archive_password is not None}")

            # 2. Die originale, unberührte Pipeline ausführen
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

        # NEU: Fortschrittsbalken nach dem Laden wieder ausblenden
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

        # Unten in der Statuszeile wird es wie gewohnt eingetragen
        self.status_label.set_text(msg)

        # NEU: Genau dieselbe wertvolle Info schreiben wir jetzt fest oben rechts rein!
        self.total_stats_label.set_text(msg)

        self.path_label.set_text("Archivwurzel")


        if self.root_store.get_n_items():
            self.tree_selection.set_selected(0)

        return False

    @staticmethod
    def _tree_children(*args):
        if not args:
            return None
        # Das letzte Argument ist bei PyGObject immer das Ziel-Item
        item = args[-1]

        # Gtk.TreeListModel liefert hier oft ein Gtk.TreeListRow-Objekt.
        # Wir müssen das echte DarNode-Objekt herausholen!
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

        # KORREKTUR: Text für das virtuelle Element lesbar machen
        self.path_label.set_text(node.path if node.path else "Gesamtes Archiv")


        if self.search.get_text().strip():
            self._apply_search()
        else:
            # KORREKTUR: Wir reichen den node.children Store IMMER direkt weiter.
            # Ein leerer ListStore ist für GTK4 ein vollkommen gültiges Modell,
            # solange wir kein neues, instanziiertes Alibi-Objekt erzwingen!
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

        # KORREKTUR: Wenn der virtuelle Masterknoten links ausgewählt ist,
        # schalten wir den Button direkt frei, auch wenn rechts nichts markiert wurde
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
        self.status_label.set_text(
            f"{store.get_n_items():,} Treffer".replace(",", ".")
        )
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

        # Zugriff auf die Gtk.MultiSelection für die rechte Tabelle
        selection_bitset = self.right_selection.get_selection()
        model = self.right_selection.get_model()

        if selection_bitset and model and not selection_bitset.is_empty():
            # Wir gehen alle Indizes durch, die im Bitset als ausgewählt markiert sind
            for i in range(model.get_n_items()):
                if selection_bitset.contains(i):
                    item = model.get_item(i)
                    if item:
                        paths_to_extract.append(item.path)

        # Falls in der rechten Tabelle nichts ausgewählt ist, prüfen wir den linken Baum
        if not paths_to_extract:
            sidebar_selection = getattr(self, 'tree_selection', None)
            if sidebar_selection:
                if hasattr(sidebar_selection, 'get_selected_item'):
                    row = sidebar_selection.get_selected_item()
                    if row and row.get_item():
                        node = row.get_item()
                        # KORREKTUR: Wenn leeres Array/String vorliegt -> Gesamtes Archiv markiert
                        if node.path == "":
                            paths_to_extract = [""]
                        else:
                            paths_to_extract.append(node.path)
                elif hasattr(sidebar_selection, 'get_selected'):
                    pos = sidebar_selection.get_selected()
                    if pos != 0xffffffff and (pos >> 32 == 0):
                        sidebar_model = sidebar_selection.get_model()
                        if sidebar_model:
                            row = sidebar_model.get_item(pos)
                            if row:
                                node = row.get_item()
                                if node:
                                    paths_to_extract.append(node.path)

        if not paths_to_extract:
            self._append_log_threadsafe("Keine Dateien zur Extraktion ausgewählt.")
            return

        # 2. Zielverzeichnis abfragen
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
        """Öffnet ein sauberes GTK4-Fenster zur Auswahl des Berechtigungs-Modus."""
        # KORREKTUR: Reines Gtk.Window statt veraltetem Gtk.Dialog nutzen, um Warnung zu eliminieren
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
            "• <i>Originale Rechte behalten:</i> DAR stellt UID/GID exakt wieder her. Das Programm "
            "fordert hierzu gleich Ihr sudo-Passwort über ein Popup an.\n"
            "• <i>Rechte ignorieren:</i> Alle Dateien gehören danach Ihrem aktuellen Benutzer (sicher für normale Daten)."
        )
        label.set_xalign(0)
        label.set_wrap(True)
        main_box.append(label)

        # Button-Leiste unten anordnen
        button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        button_box.set_halign(Gtk.Align.END)
        main_box.append(button_box)

        # Einzelne Buttons erstellen
        btn_cancel = Gtk.Button(label="Abbrechen")
        btn_ignore = Gtk.Button(label="Rechte ignorieren")
        btn_keep = Gtk.Button(label="Originale Rechte behalten")

        button_box.append(btn_cancel)
        button_box.append(btn_ignore)
        button_box.append(btn_keep)

        # Zentrale Antwort-Aktion
        def on_choice_made(button, action_type):
            window.close()
            if action_type == "cancel":
                return

            restore_ownership = (action_type == "keep")
            # Extraktions-Thread mit der getroffenen Entscheidung starten
            self._start_extraction_thread(destination, paths_to_extract, restore_ownership)

        # Event-Handler absolut verwechslungssicher verbinden
        btn_cancel.connect("clicked", on_choice_made, "cancel")
        btn_ignore.connect("clicked", on_choice_made, "ignore")
        btn_keep.connect("clicked", on_choice_made, "keep")

        window.present()


    def _start_extraction_thread(self, destination, paths_to_extract, restore_ownership):
        """UI sperren und den Hintergrund-Worker starten."""
        self.extracting = True
        self.extract_btn.set_sensitive(False)
        self.reload_btn.set_sensitive(False)

        # NEU: Gesamtzahl der zu entpackenden Dateien im RAM berechnen
        if paths_to_extract == [""]:
            total_expected_files = len(self.all_nodes)
        else:
            total_expected_files = sum(
                1 for node in self.all_nodes
                if any(node.path.startswith(p) for p in paths_to_extract if p)
            )
        total_expected_files = max(1, total_expected_files)

        self.progress.set_fraction(0.0)
        self.progress.set_text("Extraktion wird gestartet... 0%")
        self.progress.set_visible(True)

        # Thread starten und total_expected_files als neuen Parameter übergeben
        t = threading.Thread(
            target=self._extract_worker,
            args=(self.current_archive, paths_to_extract, destination, restore_ownership, total_expected_files),
            daemon=True
        )
        t.start()



    def _extract_worker(self, archive_arg, paths, destination, restore_ownership, total_expected_files):
        import re
        import os
        import shlex
        from dar_runner import DarProcessRunner

        clean_archive = archive_arg
        if archive_arg.endswith(".dar"):
            clean_archive = re.sub(r"\.\d+\.dar$", "", archive_arg)

        # Befehl zusammenbauen (Wichtig: mit -w, da pexpect auf die Prompts wartet!)
        cmd = ["dar", "-x", clean_archive, "-R", destination, "-w", "--sequential-read"]
        if restore_ownership:
            cmd = ["sudo", "-S"] + cmd
        else:
            cmd.append("-O")

        if hasattr(self, 'archive_password') and self.archive_password:
            cmd.extend(["-K", self.archive_password])

        for path in paths:
            if path:
                cmd.extend(["-g", path])

        self._append_log_threadsafe("$ " + " ".join(shlex.quote(x) for x in cmd))

        extracted_count = 0

        # Callback für die Textzeilen (Log & Tacho)
        def handle_line(line_to_log):
            nonlocal extracted_count
            if line_to_log:
                extracted_count += 1
                fraction = min(1.0, extracted_count / total_expected_files)
                percentage = int(fraction * 100)
                text = f"Entpacke... {percentage}% ({extracted_count:,} von {total_expected_files:,})".replace(",", ".")

                GLib.idle_add(lambda: self.progress.set_fraction(fraction) or self.progress.set_text(text) or self._append_log(line_to_log))

        # Callback für das GUI-Popup
        def handle_interaction(prompt_text):
            GLib.idle_add(self._force_show_log)
            # Nutzt dein stabiles GTK-Popup. Liefert bei "Ja" -> "", bei "Abbrechen" -> None
            return self._prompt_user_interactive_queue(prompt_text)

        try:
            # Den neuen pexpect-basierten Runner starten
            runner = DarProcessRunner(cmd)
            returncode = runner.run(line_callback=handle_line, interaction_callback=handle_interaction)

            GLib.idle_add(self._extract_finished, returncode, destination)

        except Exception as exc:
            GLib.idle_add(self._extract_finished, -1, destination, str(exc))



    def _prompt_user_interactive_queue(self, prompt_text: str) -> str | None:
        """
        Öffnet einen universellen, interaktiven Dialog im GTK4-Hauptthread.
        Nutzt feste Widget-Namen zur Rückgabe. 'Nein' bricht den Vorgang jetzt sauber ab.
        """
        response_queue = queue.Queue()

        def ask_user_gui():
            window = Gtk.Window(title="DAR Interaktion benötigt", transient_for=self, modal=True)
            window.set_default_size(450, -1)

            main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=15)
            main_box.set_margin_top(15)
            main_box.set_margin_bottom(15)
            main_box.set_margin_start(15)
            main_box.set_margin_end(15)
            window.set_child(main_box)

            label = Gtk.Label(label=prompt_text.strip())
            label.set_xalign(0)
            label.set_wrap(True)
            main_box.append(label)

            entry = Gtk.Entry()
            entry.set_hexpand(True)

            is_yes_no = "[return = yes" in prompt_text.lower() or "esc = no" in prompt_text.lower()

            prompt_lower = prompt_text.lower()
            if "password" in prompt_lower or "passwort" in prompt_lower:
                entry.set_visibility(False)

            if "slice" in prompt_text.lower() and self.current_archive:
                entry.set_text(self.current_archive)

            if not is_yes_no:
                main_box.append(entry)

            button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            button_box.set_halign(Gtk.Align.END)
            main_box.append(button_box)

            # Die zentrale Klick-Funktion
            def on_button_clicked(button):
                btn_id = button.get_name()

                if btn_id == "cancel" or btn_id == "no":
                    # 'no' liefert jetzt ebenfalls None zurück, damit der Worker process.terminate() ruft
                    response_queue.put(None)
                elif btn_id == "yes":
                    response_queue.put("") # Leerer String wird zu reiner Eingabetaste (\n)
                elif btn_id == "ok":
                    response_queue.put(entry.get_text())

                window.close()

            # Nur wenn es KEINE reine Ja/Nein-Frage ist, zeigen wir den klassischen Abbrechen-Button
            if not is_yes_no:
                btn_cancel = Gtk.Button(label="Abbrechen")
                btn_cancel.set_name("cancel")
                btn_cancel.connect("clicked", on_button_clicked)
                button_box.append(btn_cancel)

            if is_yes_no:
                # 'Nein' agiert jetzt als sauberer Abbruch-Knopf
                btn_no = Gtk.Button(label="Nein / Abbrechen (ESC)")
                btn_no.set_name("no")
                btn_no.connect("clicked", on_button_clicked)

                btn_yes = Gtk.Button(label="Ja / Weiter (Enter)")
                btn_yes.set_name("yes")
                btn_yes.connect("clicked", on_button_clicked)

                button_box.append(btn_no)
                button_box.append(btn_yes)
            else:
                btn_ok = Gtk.Button(label="Bestätigen")
                btn_ok.set_name("ok")
                btn_ok.connect("clicked", on_button_clicked)
                button_box.append(btn_ok)

            def on_close_request(_):
                response_queue.put(None)
                return False
            window.connect("close-request", on_close_request)

            window.present()

        GLib.idle_add(ask_user_gui)
        return response_queue.get()




    def _prompt_for_slice_path_sync(self, prompt_text: str) -> str | None:
        """
        Nutzt den stabilen Queue-Kanal für Slice-Wechsel und isolierte Kataloge.
        """
        return self._prompt_user_interactive_queue(prompt_text)


    @staticmethod
    def _shell_quote(value):
        import shlex
        return shlex.quote(value)

    def _append_log_threadsafe(self, text):
        GLib.idle_add(self._append_log, text)

    def _append_log(self, text):
        if text:
            end = self.log_buffer.get_end_iter()
            self.log_buffer.insert(end, text + "\n")
            mark = self.log_buffer.create_mark(None, self.log_buffer.get_end_iter(), False)
            self.log_view.scroll_to_mark(mark, 0.0, False, 0.0, 1.0)
        return False

    def _clear_log(self):
        self.log_buffer.set_text("")

    def _extract_finished(self, returncode, destination, error=None):
        self.extracting = False
        self.reload_btn.set_sensitive(True)
        self._update_extract_button()

        if error:
            self.progress.set_fraction(0)
            self.progress.set_text("Fehler")
            self.status_label.set_text("Extraktion konnte nicht gestartet werden")
            self._show_error("Extraktion", error)
            return False

        if returncode == 0:
            self.progress.set_fraction(1.0)
            self.progress.set_text("Fertig")
            self.status_label.set_text(
                f"Extraktion abgeschlossen: {destination}"
            )
        else:
            self.progress.set_text("DAR meldete einen Fehler")
            self.status_label.set_text(
                f"Extraktion beendet mit Exit-Code {returncode}"
            )
            self._show_error(
                "Extraktion",
                f"DAR wurde mit Exit-Code {returncode} beendet.\n"
                "Die vollständige Ausgabe findest du im DAR-Ausgabeprotokoll.",
            )

        return False

    def on_show_log_toggled(self, button):
        self.log_frame.set_visible(button.get_active())

    def _force_show_log(self):
        """Klappt die DAR-Ausgabe im GUI automatisch auf, falls sie geschlossen war."""
        if not self.show_log_btn.get_active():
            self.show_log_btn.set_active(True)
        return False


    def _show_error(self, title, message):
        dialog = Gtk.AlertDialog()
        dialog.set_message(title)
        dialog.set_detail(message)
        dialog.show(self)


class DarExplorerApp(Gtk.Application):
    def __init__(self, initial_archive=None):
        super().__init__(
            application_id="de.thws.dar-explorer",
            flags=Gio.ApplicationFlags.DEFAULT_FLAGS,
        )
        self.initial_archive = initial_archive

    def do_activate(self):
        window = self.props.active_window
        if window is None:
            window = DarExplorerWindow(self)

        window.present()

        if self.initial_archive and window.current_archive is None:
            archive = self.initial_archive
            self.initial_archive = None
            GLib.idle_add(window.load_archive, archive)


def main():
    initial = sys.argv[1] if len(sys.argv) > 1 else None

    if initial and not os.path.exists(os.path.expanduser(initial)):
        print(f"Fehler: Datei nicht gefunden: {initial}", file=sys.stderr)
        return 2

    return DarExplorerApp(initial).run(sys.argv)


if __name__ == "__main__":
    raise SystemExit(main())
