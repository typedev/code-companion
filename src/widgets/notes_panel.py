"""Notes panel widget for sidebar - user notes, docs, and code TODOs."""

import subprocess
import shutil
import threading
from pathlib import Path

from gi.repository import Gtk, Gio, GLib, GObject, Adw

from ..services import SnippetsService


class NotesPanel(Gtk.Box):
    """Panel displaying user notes, docs, and code TODOs."""

    __gsignals__ = {
        "open-file": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        "open-file-at-line": (GObject.SignalFlags.RUN_FIRST, None, (str, int, str)),
    }

    # TODO patterns to search for
    TODO_PATTERNS = ["TODO:", "FIXME:", "HACK:", "XXX:", "NOTE:"]

    def __init__(self, project_path: str):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)

        self.project_path = Path(project_path)
        self._file_monitors: list[Gio.FileMonitor] = []
        self._refresh_pending = False

        self._build_ui()
        self._setup_css()
        self.refresh()
        self._setup_file_monitors()

        self.connect("destroy", self._on_destroy)

    def _setup_css(self):
        """Set up CSS styles."""
        css = b"""
        .notes-file-btn {
            padding: 4px 8px;
        }
        .notes-file-btn:hover {
            background: alpha(@accent_color, 0.1);
        }
        .todo-line-num {
            font-family: monospace;
            font-size: 0.85em;
            min-width: 3em;
        }
        .todo-tag {
            font-family: monospace;
            font-size: 0.8em;
            font-weight: bold;
            padding: 1px 4px;
            border-radius: 3px;
        }
        .todo-tag-todo { background: alpha(@warning_color, 0.3); }
        .todo-tag-fixme { background: alpha(@error_color, 0.3); }
        .todo-tag-hack { background: alpha(@warning_color, 0.2); }
        .todo-tag-note { background: alpha(@accent_color, 0.2); }
        """
        provider = Gtk.CssProvider()
        provider.load_from_data(css)
        Gtk.StyleContext.add_provider_for_display(
            self.get_display(),
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

    def _build_ui(self):
        """Build the panel UI."""
        # Header
        header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        header_box.set_margin_start(12)
        header_box.set_margin_end(12)
        header_box.set_margin_top(12)
        header_box.set_margin_bottom(6)

        # New note button
        new_note_btn = Gtk.Button()
        new_note_btn.set_icon_name("document-new-symbolic")
        new_note_btn.add_css_class("flat")
        new_note_btn.set_tooltip_text("New note")
        new_note_btn.connect("clicked", self._on_new_note_clicked)
        header_box.append(new_note_btn)

        # Spacer
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        header_box.append(spacer)

        # Refresh button
        refresh_btn = Gtk.Button()
        refresh_btn.set_icon_name("view-refresh-symbolic")
        refresh_btn.add_css_class("flat")
        refresh_btn.set_tooltip_text("Refresh")
        refresh_btn.connect("clicked", lambda b: self.refresh())
        header_box.append(refresh_btn)

        self.append(header_box)

        # Scrolled content
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        self.content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.content_box.set_margin_start(12)
        self.content_box.set_margin_end(12)
        self.content_box.set_margin_bottom(12)

        # My Notes section
        self.notes_expander = Gtk.Expander()
        self.notes_expander.set_expanded(True)
        self.notes_header = Gtk.Label(label="My Notes")
        self.notes_header.add_css_class("heading")
        self.notes_expander.set_label_widget(self.notes_header)
        self.notes_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.notes_expander.set_child(self.notes_list)
        self.content_box.append(self.notes_expander)

        # Docs section
        self.docs_expander = Gtk.Expander()
        self.docs_expander.set_expanded(True)
        self.docs_expander.set_margin_top(8)
        self.docs_header = Gtk.Label(label="Docs")
        self.docs_header.add_css_class("heading")
        self.docs_expander.set_label_widget(self.docs_header)
        self.docs_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.docs_expander.set_child(self.docs_list)
        self.content_box.append(self.docs_expander)

        # TODOs section
        self.todos_expander = Gtk.Expander()
        self.todos_expander.set_expanded(True)
        self.todos_expander.set_margin_top(8)
        self.todos_header = Gtk.Label(label="TODOs")
        self.todos_header.add_css_class("heading")
        self.todos_expander.set_label_widget(self.todos_header)
        self.todos_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.todos_expander.set_child(self.todos_list)
        self.content_box.append(self.todos_expander)

        # Snippets section
        self.snippets_expander = Gtk.Expander()
        self.snippets_expander.set_expanded(True)
        self.snippets_expander.set_margin_top(8)

        # Snippets header with add button
        snippets_header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.snippets_header = Gtk.Label(label="Snippets")
        self.snippets_header.add_css_class("heading")
        snippets_header_box.append(self.snippets_header)

        add_snippet_btn = Gtk.Button()
        add_snippet_btn.set_icon_name("list-add-symbolic")
        add_snippet_btn.add_css_class("flat")
        add_snippet_btn.add_css_class("circular")
        add_snippet_btn.set_valign(Gtk.Align.CENTER)
        add_snippet_btn.set_tooltip_text("Add snippet")
        add_snippet_btn.connect("clicked", self._on_add_snippet_clicked)
        snippets_header_box.append(add_snippet_btn)

        self.snippets_expander.set_label_widget(snippets_header_box)
        self.snippets_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.snippets_expander.set_child(self.snippets_list)
        self.content_box.append(self.snippets_expander)

        # Get snippets service
        self.snippets_service = SnippetsService.get_instance()
        self.snippets_service.connect("changed", lambda s: self._refresh_snippets())

        scrolled.set_child(self.content_box)
        self.append(scrolled)

    def _setup_file_monitors(self):
        """Set up file monitors for notes and docs folders."""
        folders_to_watch = [
            self.project_path / "notes",
            self.project_path / "docs",
        ]

        for folder in folders_to_watch:
            if folder.exists():
                try:
                    gfile = Gio.File.new_for_path(str(folder))
                    monitor = gfile.monitor_directory(Gio.FileMonitorFlags.NONE, None)
                    monitor.connect("changed", self._on_folder_changed)
                    self._file_monitors.append(monitor)
                except GLib.Error:
                    pass

    def _on_folder_changed(self, monitor, file, other_file, event_type):
        """Handle folder changes - debounced refresh."""
        if event_type in (
            Gio.FileMonitorEvent.CREATED,
            Gio.FileMonitorEvent.DELETED,
            Gio.FileMonitorEvent.CHANGED,
        ):
            if not self._refresh_pending:
                self._refresh_pending = True
                GLib.timeout_add(300, self._delayed_refresh)

    def _delayed_refresh(self):
        """Perform delayed refresh."""
        self._refresh_pending = False
        self.refresh()
        return False

    def _on_destroy(self, widget):
        """Clean up on destroy."""
        for monitor in self._file_monitors:
            monitor.cancel()
        self._file_monitors.clear()

    def refresh(self):
        """Refresh all sections."""
        self._refresh_notes()
        self._refresh_docs()
        self._refresh_todos()
        self._refresh_snippets()

    def _refresh_notes(self):
        """Refresh My Notes section."""
        self._clear_box(self.notes_list)

        notes_dir = self.project_path / "notes"
        if not notes_dir.exists():
            # Create notes folder
            try:
                notes_dir.mkdir(parents=True, exist_ok=True)
            except OSError:
                pass

        md_files = sorted(notes_dir.glob("*.md")) if notes_dir.exists() else []

        if not md_files:
            label = Gtk.Label(label="No notes yet")
            label.add_css_class("dim-label")
            label.set_margin_start(8)
            label.set_margin_top(4)
            self.notes_list.append(label)
            self.notes_header.set_label("My Notes")
        else:
            self.notes_header.set_label(f"My Notes ({len(md_files)})")
            for file_path in md_files:
                self._add_file_row(self.notes_list, file_path)

    def _refresh_docs(self):
        """Refresh Docs section."""
        self._clear_box(self.docs_list)

        doc_files = []

        # CLAUDE.md in root
        claude_md = self.project_path / "CLAUDE.md"
        if claude_md.exists():
            doc_files.append(claude_md)

        # docs/*.md
        docs_dir = self.project_path / "docs"
        if docs_dir.exists():
            doc_files.extend(sorted(docs_dir.glob("*.md")))

        if not doc_files:
            label = Gtk.Label(label="No docs")
            label.add_css_class("dim-label")
            label.set_margin_start(8)
            label.set_margin_top(4)
            self.docs_list.append(label)
            self.docs_header.set_label("Docs")
        else:
            self.docs_header.set_label(f"Docs ({len(doc_files)})")
            for file_path in doc_files:
                self._add_file_row(self.docs_list, file_path)

    def _refresh_todos(self):
        """Refresh TODOs section - search in background."""
        self._clear_box(self.todos_list)

        loading = Gtk.Label(label="Scanning...")
        loading.add_css_class("dim-label")
        loading.set_margin_start(8)
        self.todos_list.append(loading)

        def search_todos():
            results = self._search_todos()
            GLib.idle_add(lambda: self._display_todos(results))

        thread = threading.Thread(target=search_todos, daemon=True)
        thread.start()

    def _search_todos(self) -> dict[str, list[tuple[int, str, str]]]:
        """Search for TODOs in code and markdown files."""
        pattern = "|".join(self.TODO_PATTERNS)

        rg_path = shutil.which("rg")
        if rg_path:
            cmd = [
                "rg", "--line-number", "--no-heading",
                "-e", pattern,
                "--type", "py",
                "--type", "js",
                "--type", "ts",
                "--type", "md",
                "--glob", "!node_modules",
                "--glob", "!.venv",
                "--glob", "!__pycache__",
                "--glob", "!.git",
                "--glob", "!*.min.js",
                "."
            ]
        else:
            cmd = [
                "grep", "-rn", "-E", pattern,
                "--include=*.py", "--include=*.js", "--include=*.ts",
                "--include=*.md", "--include=*.tsx", "--include=*.jsx",
                "--exclude-dir=node_modules", "--exclude-dir=.venv",
                "--exclude-dir=__pycache__", "--exclude-dir=.git",
                "."
            ]

        try:
            result = subprocess.run(
                cmd, cwd=self.project_path,
                capture_output=True, text=True, timeout=15
            )
            return self._parse_todo_results(result.stdout)
        except Exception:
            return {}

    def _parse_todo_results(self, output: str) -> dict[str, list[tuple[int, str, str]]]:
        """Parse grep/rg output into grouped results."""
        results: dict[str, list[tuple[int, str, str]]] = {}

        for line in output.strip().split("\n"):
            if not line:
                continue

            parts = line.split(":", 2)
            if len(parts) < 3:
                continue

            file_path = parts[0].lstrip("./")
            try:
                line_num = int(parts[1])
            except ValueError:
                continue
            content = parts[2].strip()

            # Extract tag
            tag = ""
            for p in self.TODO_PATTERNS:
                if p in content:
                    tag = p.rstrip(":")
                    break

            if file_path not in results:
                results[file_path] = []
            results[file_path].append((line_num, tag, content))

        return results

    def _display_todos(self, results: dict[str, list[tuple[int, str, str]]]):
        """Display TODO results."""
        self._clear_box(self.todos_list)

        if not results:
            label = Gtk.Label(label="No TODOs found")
            label.add_css_class("dim-label")
            label.set_margin_start(8)
            label.set_margin_top(4)
            self.todos_list.append(label)
            self.todos_header.set_label("TODOs")
            return

        total = sum(len(items) for items in results.values())
        self.todos_header.set_label(f"TODOs ({total})")

        for file_path, items in sorted(results.items()):
            # File header
            file_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            file_box.set_margin_top(4)

            file_label = Gtk.Label(label=f"{file_path} ({len(items)})")
            file_label.set_xalign(0)
            file_label.add_css_class("dim-label")
            file_label.set_margin_start(4)
            file_box.append(file_label)

            # TODO items (limit to 10 per file)
            for line_num, tag, content in items[:10]:
                self._add_todo_row(file_box, file_path, line_num, tag, content)

            if len(items) > 10:
                more = Gtk.Label(label=f"  +{len(items) - 10} more")
                more.add_css_class("dim-label")
                more.set_xalign(0)
                more.set_margin_start(12)
                file_box.append(more)

            self.todos_list.append(file_box)

    def _add_file_row(self, container: Gtk.Box, file_path: Path):
        """Add a file row to container."""
        btn = Gtk.Button()
        btn.add_css_class("flat")
        btn.add_css_class("notes-file-btn")
        btn.file_path = str(file_path)

        box = Gtk.Box(spacing=6)

        icon = Gtk.Image.new_from_icon_name("text-x-generic-symbolic")
        icon.add_css_class("dim-label")
        box.append(icon)

        # Show relative path for docs, just filename for notes
        try:
            rel = file_path.relative_to(self.project_path)
            display_name = str(rel) if "docs" in str(rel) or file_path.name == "CLAUDE.md" else file_path.name
        except ValueError:
            display_name = file_path.name

        label = Gtk.Label(label=display_name)
        label.set_xalign(0)
        label.set_ellipsize(2)
        box.append(label)

        btn.set_child(box)
        btn.connect("clicked", self._on_file_clicked)
        container.append(btn)

    def _add_todo_row(self, container: Gtk.Box, file_path: str, line_num: int, tag: str, content: str):
        """Add a TODO row."""
        btn = Gtk.Button()
        btn.add_css_class("flat")
        btn.add_css_class("notes-file-btn")
        btn.file_path = str(self.project_path / file_path)
        btn.line_num = line_num
        btn.tag = tag

        box = Gtk.Box(spacing=6)
        box.set_margin_start(8)

        # Line number
        line_label = Gtk.Label(label=f"{line_num}:")
        line_label.add_css_class("todo-line-num")
        line_label.add_css_class("dim-label")
        line_label.set_xalign(1)
        box.append(line_label)

        # Tag badge
        if tag:
            tag_label = Gtk.Label(label=tag)
            tag_label.add_css_class("todo-tag")
            tag_class = f"todo-tag-{tag.lower()}"
            tag_label.add_css_class(tag_class)
            box.append(tag_label)

        # Content preview (strip the tag from content)
        preview = content
        for p in self.TODO_PATTERNS:
            if p in preview:
                idx = preview.find(p)
                preview = preview[idx + len(p):].strip()
                break

        preview = preview[:40] + "..." if len(preview) > 40 else preview
        content_label = Gtk.Label(label=preview)
        content_label.set_xalign(0)
        content_label.set_ellipsize(2)
        box.append(content_label)

        btn.set_child(box)
        btn.connect("clicked", self._on_todo_clicked)
        container.append(btn)

    def _on_file_clicked(self, button):
        """Handle file click."""
        self.emit("open-file", button.file_path)

    def _on_todo_clicked(self, button):
        """Handle TODO click."""
        self.emit("open-file-at-line", button.file_path, button.line_num, button.tag)

    def _on_new_note_clicked(self, button):
        """Handle new note button click."""
        dialog = Adw.AlertDialog()
        dialog.set_heading("New Note")
        dialog.set_body("Enter filename for the new note:")

        # Entry for filename
        entry = Gtk.Entry()
        entry.set_placeholder_text("my-note")
        entry.set_margin_top(12)
        dialog.set_extra_child(entry)

        dialog.add_response("cancel", "Cancel")
        dialog.add_response("create", "Create")
        dialog.set_response_appearance("create", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("create")

        dialog.connect("response", self._on_new_note_response, entry)
        dialog.present(self.get_root())

    def _on_new_note_response(self, dialog, response, entry):
        """Handle new note dialog response."""
        if response != "create":
            return

        filename = entry.get_text().strip()
        if not filename:
            return

        # Ensure .md extension
        if not filename.endswith(".md"):
            filename += ".md"

        # Create file
        notes_dir = self.project_path / "notes"
        notes_dir.mkdir(parents=True, exist_ok=True)

        file_path = notes_dir / filename
        if not file_path.exists():
            file_path.write_text(f"# {filename[:-3]}\n\n")

        # Refresh and open
        self.refresh()
        self.emit("open-file", str(file_path))

    def _clear_box(self, box: Gtk.Box):
        """Clear all children from a box."""
        while True:
            child = box.get_first_child()
            if not child:
                break
            box.remove(child)

    def _refresh_snippets(self):
        """Refresh Snippets section."""
        self._clear_box(self.snippets_list)

        snippets = self.snippets_service.get_all()

        if not snippets:
            label = Gtk.Label(label="No snippets")
            label.add_css_class("dim-label")
            label.set_margin_start(8)
            label.set_margin_top(4)
            self.snippets_list.append(label)
            self.snippets_header.set_label("Snippets")
        else:
            self.snippets_header.set_label(f"Snippets ({len(snippets)})")
            for snippet in snippets:
                self._add_snippet_row(snippet)

    def _add_snippet_row(self, snippet: dict):
        """Add a snippet row as a clickable file button."""
        btn = Gtk.Button()
        btn.add_css_class("flat")
        btn.add_css_class("notes-file-btn")
        btn.file_path = snippet["path"]

        box = Gtk.Box(spacing=6)

        icon = Gtk.Image.new_from_icon_name("text-x-generic-symbolic")
        icon.add_css_class("dim-label")
        box.append(icon)

        label = Gtk.Label(label=snippet["label"])
        label.set_xalign(0)
        label.set_ellipsize(2)
        label.set_tooltip_text(snippet["text"][:100] + "..." if len(snippet["text"]) > 100 else snippet["text"])
        box.append(label)

        btn.set_child(box)
        btn.connect("clicked", self._on_file_clicked)
        self.snippets_list.append(btn)

    def _on_add_snippet_clicked(self, button):
        """Handle add snippet button - create new file and open it."""
        dialog = Adw.AlertDialog()
        dialog.set_heading("New Snippet")
        dialog.set_body("Enter name for the new snippet:")

        entry = Gtk.Entry()
        entry.set_placeholder_text("My Snippet")
        entry.set_margin_top(12)
        dialog.set_extra_child(entry)

        dialog.add_response("cancel", "Cancel")
        dialog.add_response("create", "Create")
        dialog.set_response_appearance("create", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("create")

        dialog.connect("response", self._on_new_snippet_response, entry)
        dialog.present(self.get_root())

    def _on_new_snippet_response(self, dialog, response, entry):
        """Handle new snippet dialog response."""
        if response != "create":
            return

        name = entry.get_text().strip()
        if not name:
            return

        # Create snippet file with placeholder text
        file_path = self.snippets_service.add(name, "Enter snippet text here...")

        # Open in editor
        self.emit("open-file", file_path)
