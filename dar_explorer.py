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
    for root_node in roots:
        populate_gtk_children(root_node)
        root_store.append(root_node)

    return root_store, list(nodes.values())




class DarExplorerWindow(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app)
        self.set_default_size(1450, 850)
        self.set_title("DAR Explorer V2")

        self.current_archive = None
        self.all_nodes = []
        self.root_store = None
        self.loading = False
        self.extracting = False

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

        self.path_label = Gtk.Label(label="")
        self.path_label.set_xalign(0)
        self.path_label.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        self.path_label.add_css_class("path-label")

        info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        info.set_margin_top(8)
        info.set_margin_bottom(6)
        info.set_margin_start(10)
        info.set_margin_end(10)
        info.append(self.archive_label)
        info.append(self.path_label)

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

        self.reload_btn.set_sensitive(False)
        self.extract_btn.set_sensitive(False)
        self.archive_label.set_text(f"Archiv: {path}")
        self.path_label.set_text("DAR-Listing wird eingelesen …")
        self.status_label.set_text("DAR wird ausgeführt …")

        threading.Thread(
            target=self._load_worker, args=(path,), daemon=True
        ).start()

    def _load_worker(self, path):
        try:
            entries, warning = run_dar_listing(path)
            GLib.idle_add(self._load_finished, path, entries, warning, None)
        except Exception as exc:
            GLib.idle_add(self._load_finished, path, None, "", str(exc))

    def _load_finished(self, path, entries, warning, error):
        self.loading = False
        self.reload_btn.set_sensitive(True)

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

        self.status_label.set_text(msg)
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

        self.path_label.set_text(node.path)

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
        selected = self._selected_nodes()
        if not selected:
            return

        dialog = Gtk.FileDialog()
        dialog.set_title("Zielverzeichnis für die Extraktion auswählen")
        dialog.select_folder(self, None, self._extract_folder_done, selected)

    def _extract_folder_done(self, dialog, result, selected):
        try:
            folder = dialog.select_folder_finish(result)
        except GLib.Error:
            return

        if folder is None:
            return

        destination = folder.get_path()
        if destination:
            self._start_extraction(selected, destination)

    def _selected_nodes(self):
        model = self.right_selection.get_model()
        selection = self.right_selection.get_selection()
        result = []

        if model is None:
            return result

        for i in range(model.get_n_items()):
            if selection.contains(i):
                result.append(model.get_item(i))

        return result

    def _start_extraction(self, nodes, destination):
        if not self.current_archive or self.extracting:
            return

        self.extracting = True
        self.extract_btn.set_sensitive(False)
        self.reload_btn.set_sensitive(False)

        self.progress.set_visible(True)
        self.progress.set_fraction(0.0)
        self.progress.set_text("Extraktion wird vorbereitet …")

        self._clear_log()
        self.status_label.set_text(
            f"{len(nodes)} Einträge werden nach {destination} extrahiert …"
        )

        archive_arg = normalize_archive_argument(self.current_archive)
        paths = [node.path for node in nodes]

        threading.Thread(
            target=self._extract_worker,
            args=(archive_arg, paths, destination),
            daemon=True,
        ).start()

    def _extract_worker(self, archive_arg, paths, destination):
        # KORREKTUR: Wir nutzen hier ebenfalls normalize_archive_argument,
        # damit DAR den sauberen Basisnamen ohne ".1.dar" übergeben bekommt!
        clean_archive = self.normalize_archive_argument(archive_arg) if hasattr(self, 'normalize_archive_argument') else archive_arg

        # Falls die Methode in dar_parser.py liegt und importiert wurde, nutzen wir sie direkt:
        # clean_archive = normalize_archive_argument(archive_arg)

        cmd = ["dar", "-x", clean_archive, "-R", destination, "-O", "-w"]

        for path in paths:
            cmd.extend(["-g", path])


        try:
            self._append_log_threadsafe("$ " + " ".join(self._shell_quote(x) for x in cmd))

            # KORREKTUR: text=True wird ENTFERNT, bufsize=0 zwingt zu absolut ungepuffertem Binär-Streaming
            process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=0,
                env=env,
            )

            buffer = ""
            while True:
                # KORREKTUR: Wir lesen 1 rohes Byte aus dem Stream
                char_bytes = process.stdout.read(1)
                if not char_bytes:
                    break

                # Byte direkt in Text übersetzen
                char = char_bytes.decode("utf-8", errors="replace")
                buffer += char

                # Normale Zeilen loggen
                if char == "\n":
                    line_to_log = buffer.rstrip()
                    GLib.idle_add(self._append_log, line_to_log)
                    GLib.idle_add(self.progress.pulse)
                    buffer = ""

                # ERKENNUNG VON INTERAKTION (Wenn der Puffer auf ein Prompt-Zeichen endet)
                elif buffer.endswith("]") or buffer.endswith(":") or buffer.endswith("?"):
                    lower_buf = buffer.lower()

                    # Nur reagieren, wenn es sich um bekannte DAR-Abfragen handelt
                    if any(x in lower_buf for x in ["path", "password", "continue", "lack of privilege", "[return", "enter"]):
                        GLib.idle_add(self._append_log, f"[GUI-Prompt]: {buffer}")

                        # Unser schönes neues Dialog-Fenster aufrufen
                        user_response = self._prompt_user_interactive_sync(buffer)

                        if user_response is not None:
                            # KORREKTUR: Antwort als Binärdaten (.encode()) mit Zeilenumbruch an DAR senden
                            response_bytes = (user_response + "\n").encode("utf-8")
                            process.stdin.write(response_bytes)
                            process.stdin.flush()
                        else:
                            process.terminate()
                            break
                        buffer = ""

            if buffer:
                GLib.idle_add(self._append_log, buffer.rstrip())

            returncode = process.wait()
            GLib.idle_add(self._extract_finished, returncode, destination)

        except Exception as exc:
            GLib.idle_add(self._extract_finished, -1, destination, str(exc))


    def _prompt_user_interactive_sync(self, prompt_text: str) -> str | None:
        """
        Öffnet einen flexiblen, synchronen Dialog im GTK4-Hauptthread über ein Gtk.Window.
        Blockiert den Worker-Thread sicher, bis der Benutzer antwortet.
        """
        condition = threading.Condition()
        user_input_container = [None]

        def ask_user():
            with condition:
                # KORREKTUR: Reines Gtk.Window statt veraltetem Gtk.Dialog nutzen
                window = Gtk.Window(title="DAR Interaktion benötigt", transient_for=self, modal=True)
                window.set_default_size(400, -1)

                # Haupt-Layout
                main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=15)
                main_box.set_margin_top(15)
                main_box.set_margin_bottom(15)
                main_box.set_margin_start(15)
                main_box.set_margin_end(15)
                window.set_child(main_box)

                # Textanzeige der Frage
                label = Gtk.Label(label=prompt_text.strip())
                label.set_xalign(0)
                label.set_wrap(True)
                main_box.append(label)

                # Eingabefeld (nur für Passwörter oder Slices)
                entry = Gtk.Entry()
                entry.set_hexpand(True)

                is_yes_no = "[return = yes" in prompt_text.lower() or "esc = no" in prompt_text.lower()

                if "password" in prompt_text.lower():
                    entry.set_visibility(False)
                if "slice" in prompt_text.lower() and self.current_archive:
                    entry.set_text(self.current_archive)

                if not is_yes_no:
                    main_box.append(entry)

                # Button-Leiste unten
                button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
                button_box.set_halign(Gtk.Align.END)
                main_box.append(button_box)

                # Buttons erstellen
                btn_cancel = Gtk.Button(label="Abbrechen")
                button_box.append(btn_cancel)

                if is_yes_no:
                    btn_no = Gtk.Button(label="Nein (ESC)")
                    btn_yes = Gtk.Button(label="Ja (Enter)")
                    button_box.append(btn_no)
                    button_box.append(btn_yes)
                else:
                    btn_ok = Gtk.Button(label="Bestätigen")
                    button_box.append(btn_ok)

                # Signal-Handler für die Buttons (Direkte Freigabe des Threads)
                def finalize(result_value):
                    user_input_container[0] = result_value
                    window.close()  # Schließt das Fenster sauber in GTK4

                btn_cancel.connect("clicked", lambda _: finalize(None))
                if is_yes_no:
                    btn_no.connect("clicked", lambda _: finalize("no"))
                    btn_yes.connect("clicked", lambda _: finalize(""))
                else:
                    btn_ok.connect("clicked", lambda _: finalize(entry.get_text()))

                # Fallback, wenn der Benutzer das Fenster über das 'X' schließt
                def on_close_request(_):
                    with condition:
                        condition.notify()
                    return False

                window.connect("close-request", on_close_request)

                # Zerstörungs-Signal nutzen, um den wartenden Thread aufzuwecken
                def on_destroy(_):
                    with condition:
                        condition.notify()

                window.connect("destroy", on_destroy)
                window.present()

        with condition:
            GLib.idle_add(ask_user)
            condition.wait()

        # Gibt den exakten Wert an den Worker zurück
        return user_input_container[0]




    def _prompt_for_slice_path_sync(self) -> str | None:
        """
        Öffnet einen synchronen Eingabedialog im GTK-Hauptthread
        und blockiert den Worker-Thread, bis die Antwort vorliegt.
        """
        condition = threading.Condition()
        result_path = [None]

        def ask_user():
            nonlocal result_path
            with condition:
                dialog = Gtk.Dialog(title="Echtes Datenarchiv benötigt", transient_for=self, modal=True)
                dialog.add_button("Abbrechen", Gtk.ResponseType.CANCEL)
                dialog.add_button("Bestätigen", Gtk.ResponseType.OK)

                box = dialog.get_content_area()
                box.set_margin_all(12)
                box.set_spacing(10)

                label = Gtk.Label(
                    label="DAR liest einen isolierten Katalog und benötigt nun die echten Daten.\n"
                          "Bitte geben Sie den Basispfad (ohne .1.dar) zu den Archiv-Slices an:"
                )
                label.set_xalign(0)
                box.append(label)

                entry = Gtk.Entry()
                entry.set_hexpand(True)
                if self.current_archive:
                    entry.set_text(self.current_archive)
                box.append(entry)

                def on_response(d, response_id):
                    nonlocal result_path
                    if response_id == Gtk.ResponseType.OK:
                        result_path = entry.get_text()
                    else:
                        result_path = None
                    d.destroy()
                    with condition:
                        condition.notify()

                dialog.connect("response", on_response)
                dialog.show()

        with condition:
            GLib.idle_add(ask_user)
            condition.wait()

        return result_path

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
