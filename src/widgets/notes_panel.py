"""Notes panel widget for sidebar - user notes, docs, snippets, and code TODOs."""

import subprocess
import shutil
import threading
from pathlib import Path

from gi.repository import Gtk, Gdk, GLib, GObject, Adw

from ..services import SnippetsService, ToastService, GitService, FileStatus, FileMonitorService
from ..services.icon_cache import IconCache


# CSS classes for git status colors (same as file_tree.py)
STATUS_CSS_CLASSES = {
    FileStatus.MODIFIED: "git-modified",
    FileStatus.ADDED: "git-added",
    FileStatus.DELETED: "git-deleted",
    FileStatus.RENAMED: "git-renamed",
    FileStatus.UNTRACKED: "git-added",
    FileStatus.TYPECHANGE: "git-modified",
}


class NotesPanel(Gtk.Box):
    """Panel displaying user notes, docs, snippets, and code TODOs.

    Notes, docs, and snippets share unified interface:
    - Header with title, count, and add button
    - File rows with name and delete button
    """

    __gsignals__ = {
        "open-file": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        "open-file-at-line": (GObject.SignalFlags.RUN_FIRST, None, (str, int, str)),
    }

    # TODO patterns to search for
    TODO_PATTERNS = ["TODO:", "FIXME:", "HACK:", "XXX:", "NOTE:"]

    def __init__(self, project_path: str, file_monitor_service: FileMonitorService):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)

        self.project_path = Path(project_path)
        self._file_monitor_service = file_monitor_service

        # Icon cache for file icons
        self._icon_cache = IconCache()

        # Git service for file status
        self._git_service = GitService(self.project_path)
        self._is_git_repo = self._git_service.is_git_repo()
        if self._is_git_repo:
            self._git_service.open()
        self._git_status: dict[str, FileStatus] = {}

        self._build_ui()
        self._setup_css()
        self.refresh()
        self._connect_monitor_signals()

    def _connect_monitor_signals(self):
        """Connect to FileMonitorService signals."""
        self._file_monitor_service.connect("notes-changed", self._on_notes_changed)

    def _on_notes_changed(self, service):
        """Handle notes/docs changes from monitor service."""
        self.refresh()

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
        /* Git status colors */
        .git-modified { color: #f1c40f; }
        .git-added { color: #2ecc71; }
        .git-deleted { color: #e74c3c; }
        .git-renamed { color: #3498db; }
        /* Normal font weight for file labels */
        .notes-file-label { font-weight: normal; }
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
        # Header with refresh button
        header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        header_box.set_margin_start(12)
        header_box.set_margin_end(12)
        header_box.set_margin_top(12)
        header_box.set_margin_bottom(6)

        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        header_box.append(spacer)

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

        # My Notes section (notes/*.md)
        self.notes_expander, self.notes_header, self.notes_list = self._create_file_section(
            "My Notes", self._on_add_note_clicked
        )
        self.content_box.append(self.notes_expander)

        # Docs section (docs/*.md + CLAUDE.md)
        self.docs_expander, self.docs_header, self.docs_list = self._create_file_section(
            "Docs", self._on_add_doc_clicked
        )
        self.docs_expander.set_margin_top(8)
        self.content_box.append(self.docs_expander)

        # Snippets section (~/.config/claude-companion/snippets/*.md)
        self.snippets_expander, self.snippets_header, self.snippets_list = self._create_file_section(
            "Snippets", self._on_add_snippet_clicked
        )
        self.snippets_expander.set_margin_top(8)
        self.content_box.append(self.snippets_expander)

        # TODOs section (code search)
        self.todos_expander = Gtk.Expander()
        self.todos_expander.set_expanded(True)
        self.todos_expander.set_margin_top(8)
        self.todos_header = Gtk.Label(label="TODOs")
        self.todos_header.add_css_class("heading")
        self.todos_expander.set_label_widget(self.todos_header)
        self.todos_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.todos_expander.set_child(self.todos_list)
        self.content_box.append(self.todos_expander)

        # Get snippets service
        self.snippets_service = SnippetsService.get_instance()
        self.snippets_service.connect("changed", lambda s: self._refresh_snippets())

        scrolled.set_child(self.content_box)
        self.append(scrolled)

    def _create_file_section(self, title: str, add_callback) -> tuple[Gtk.Expander, Gtk.Label, Gtk.Box]:
        """Create a file section with header (title + add button) and list."""
        expander = Gtk.Expander()
        expander.set_expanded(True)

        # Header with title and add button
        header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

        title_label = Gtk.Label(label=title)
        title_label.add_css_class("heading")
        header_box.append(title_label)

        add_btn = Gtk.Button()
        add_btn.set_icon_name("list-add-symbolic")
        add_btn.add_css_class("flat")
        add_btn.add_css_class("circular")
        add_btn.set_valign(Gtk.Align.CENTER)
        add_btn.set_tooltip_text(f"Add {title.lower().rstrip('s')}")
        add_btn.connect("clicked", add_callback)
        header_box.append(add_btn)

        expander.set_label_widget(header_box)

        file_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        expander.set_child(file_list)

        return expander, title_label, file_list

    def refresh(self):
        """Refresh all sections."""
        # Update git status
        if self._is_git_repo:
            self._git_status = self._git_service.get_file_status_map()
        else:
            self._git_status = {}

        self._refresh_notes()
        self._refresh_docs()
        self._refresh_todos()
        self._refresh_snippets()

    def _refresh_notes(self):
        """Refresh My Notes section."""
        self._clear_box(self.notes_list)

        notes_dir = self.project_path / "notes"
        if not notes_dir.exists():
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
                self._add_file_row(self.notes_list, file_path, "notes")

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
                self._add_file_row(self.docs_list, file_path, "docs")

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
            # File header with icon
            file_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            file_box.set_margin_top(4)

            header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            header_box.set_margin_start(4)

            # File icon
            full_path = self.project_path / file_path
            gicon = self._icon_cache.get_file_gicon(full_path)
            if gicon:
                icon = Gtk.Image.new_from_gicon(gicon)
                icon.set_pixel_size(16)
            else:
                icon = Gtk.Image.new_from_icon_name("text-x-generic-symbolic")
                icon.add_css_class("dim-label")
            header_box.append(icon)

            # File name
            file_label = Gtk.Label(label=f"{file_path} ({len(items)})")
            file_label.set_xalign(0)
            file_label.add_css_class("notes-file-label")

            # Apply git status color
            git_status = self._git_status.get(file_path)
            if git_status:
                css_class = STATUS_CSS_CLASSES.get(git_status)
                if css_class:
                    file_label.add_css_class(css_class)
            else:
                file_label.add_css_class("dim-label")

            header_box.append(file_label)
            file_box.append(header_box)

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

    def _add_file_row(self, container: Gtk.Box, file_path: Path, section: str = "notes"):
        """Add a file row with rename/delete buttons to container.

        Args:
            container: The box to add the row to
            file_path: Path to the file
            section: One of "notes", "docs", "snippets" - determines behavior
        """
        # Check if this is the protected CLAUDE.md file
        is_protected = file_path.name == "CLAUDE.md"

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        row.add_css_class("notes-file-btn")

        # File button (clickable to open)
        btn = Gtk.Button()
        btn.add_css_class("flat")
        btn.set_hexpand(True)
        btn.file_path = str(file_path)

        btn_box = Gtk.Box(spacing=6)

        # Icon - lock for protected files, Material Design icon for others
        if is_protected:
            icon = Gtk.Image.new_from_icon_name("changes-prevent-symbolic")
            icon.set_tooltip_text("Protected file")
            icon.add_css_class("dim-label")
        else:
            gicon = self._icon_cache.get_file_gicon(file_path)
            if gicon:
                icon = Gtk.Image.new_from_gicon(gicon)
                icon.set_pixel_size(16)
            else:
                icon = Gtk.Image.new_from_icon_name("text-x-generic-symbolic")
                icon.add_css_class("dim-label")
        btn_box.append(icon)

        # Show relative path for docs, just filename for notes/snippets
        try:
            rel = file_path.relative_to(self.project_path)
            display_name = str(rel) if "docs" in str(rel) or file_path.name == "CLAUDE.md" else file_path.name
            relative_path = str(rel)
        except ValueError:
            display_name = file_path.name
            relative_path = file_path.name

        label = Gtk.Label(label=display_name)
        label.set_xalign(0)
        label.set_ellipsize(2)
        label.set_hexpand(True)
        label.add_css_class("notes-file-label")

        # Apply git status color
        git_status = self._git_status.get(relative_path)
        if git_status:
            css_class = STATUS_CSS_CLASSES.get(git_status)
            if css_class:
                label.add_css_class(css_class)

        btn_box.append(label)

        btn.set_child(btn_box)
        btn.connect("clicked", self._on_file_clicked)
        row.append(btn)

        # Only show rename/delete buttons for non-protected files
        if not is_protected:
            # Rename button
            rename_btn = Gtk.Button()
            rename_btn.set_icon_name("document-edit-symbolic")
            rename_btn.add_css_class("flat")
            rename_btn.add_css_class("circular")
            rename_btn.add_css_class("dim-label")
            rename_btn.set_valign(Gtk.Align.CENTER)
            rename_btn.set_tooltip_text("Rename")
            rename_btn.connect("clicked", self._on_rename_file_clicked, file_path, section)
            row.append(rename_btn)

            # Delete button
            del_btn = Gtk.Button()
            del_btn.set_icon_name("edit-delete-symbolic")
            del_btn.add_css_class("flat")
            del_btn.add_css_class("circular")
            del_btn.add_css_class("dim-label")
            del_btn.set_valign(Gtk.Align.CENTER)
            del_btn.set_tooltip_text("Delete")
            del_btn.connect("clicked", self._on_delete_file_clicked, file_path, section)
            row.append(del_btn)

        container.append(row)

    def _on_rename_file_clicked(self, button, file_path: Path, section: str):
        """Handle rename button click - show rename dialog."""
        dialog = Adw.AlertDialog()
        dialog.set_heading("Rename")

        # Get current name without extension
        current_name = file_path.stem

        entry = Gtk.Entry()
        entry.set_text(current_name)
        entry.set_margin_top(12)
        dialog.set_extra_child(entry)

        dialog.add_response("cancel", "Cancel")
        dialog.add_response("rename", "Rename")
        dialog.set_response_appearance("rename", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("rename")
        dialog.set_close_response("cancel")

        dialog.connect("response", self._on_rename_file_response, entry, file_path, section)
        dialog.present(self.get_root())

    def _on_rename_file_response(self, dialog, response: str, entry, file_path: Path, section: str):
        """Handle rename dialog response."""
        if response != "rename":
            return

        new_name = entry.get_text().strip()
        if not new_name:
            return

        # Ensure .md extension
        if not new_name.endswith(".md"):
            new_name += ".md"

        # Check if name actually changed
        if new_name == file_path.name:
            return

        try:
            if section == "snippets":
                # Use snippets service to rename
                old_label = file_path.stem
                new_label = new_name[:-3]  # Remove .md
                if self.snippets_service.rename(old_label, new_label):
                    ToastService.show(f"Renamed to: {new_label}")
                else:
                    ToastService.show_error("Failed to rename snippet")
            else:
                # Rename regular file
                new_path = file_path.parent / new_name
                if new_path.exists():
                    ToastService.show_error(f"File already exists: {new_name}")
                    return
                file_path.rename(new_path)
                ToastService.show(f"Renamed to: {new_name}")
                self.refresh()
        except OSError as e:
            ToastService.show_error(f"Failed to rename: {e}")

    def _on_delete_file_clicked(self, button, file_path: Path, section: str):
        """Handle delete button click - show confirmation dialog."""
        dialog = Adw.AlertDialog()
        dialog.set_heading("Delete File?")
        dialog.set_body(f"Delete \"{file_path.name}\"?")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("delete", "Delete")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", self._on_delete_file_response, file_path, section)
        dialog.present(self.get_root())

    def _on_delete_file_response(self, dialog, response: str, file_path: Path, section: str):
        """Handle delete confirmation response."""
        if response != "delete":
            return

        try:
            if section == "snippets":
                # Use snippets service to delete
                label = file_path.stem
                if self.snippets_service.delete(label):
                    ToastService.show(f"Deleted: {file_path.name}")
                else:
                    ToastService.show_error(f"Failed to delete: {file_path.name}")
            else:
                # Delete regular file
                file_path.unlink()
                ToastService.show(f"Deleted: {file_path.name}")
                self.refresh()
        except OSError as e:
            ToastService.show_error(f"Failed to delete: {e}")

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
                self._add_file_row(self.snippets_list, Path(snippet["path"]), "snippets")

    # === Add callbacks ===

    def _on_add_note_clicked(self, button):
        """Handle add note button click."""
        self._show_new_file_dialog("Note", self.project_path / "notes")

    def _on_add_doc_clicked(self, button):
        """Handle add doc button click."""
        self._show_new_file_dialog("Doc", self.project_path / "docs")

    def _on_add_snippet_clicked(self, button):
        """Handle add snippet button click."""
        dialog = Adw.AlertDialog()
        dialog.set_heading("New Snippet")
        dialog.set_body("Enter name for the new snippet:")

        entry = Gtk.Entry()
        entry.set_placeholder_text("my-snippet")
        entry.set_margin_top(12)
        dialog.set_extra_child(entry)

        dialog.add_response("cancel", "Cancel")
        dialog.add_response("create", "Create")
        dialog.set_response_appearance("create", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("create")

        dialog.connect("response", self._on_new_snippet_response, entry)
        dialog.present(self.get_root())

    def _show_new_file_dialog(self, file_type: str, folder: Path):
        """Show dialog for creating a new file."""
        dialog = Adw.AlertDialog()
        dialog.set_heading(f"New {file_type}")
        dialog.set_body(f"Enter filename for the new {file_type.lower()}:")

        entry = Gtk.Entry()
        entry.set_placeholder_text(f"my-{file_type.lower()}")
        entry.set_margin_top(12)
        dialog.set_extra_child(entry)

        dialog.add_response("cancel", "Cancel")
        dialog.add_response("create", "Create")
        dialog.set_response_appearance("create", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("create")

        dialog.connect("response", self._on_new_file_response, entry, folder)
        dialog.present(self.get_root())

    def _on_new_file_response(self, dialog, response, entry, folder: Path):
        """Handle new file dialog response."""
        if response != "create":
            return

        filename = entry.get_text().strip()
        if not filename:
            return

        # Ensure .md extension
        if not filename.endswith(".md"):
            filename += ".md"

        # Create folder if needed
        folder.mkdir(parents=True, exist_ok=True)

        file_path = folder / filename
        if not file_path.exists():
            file_path.write_text(f"# {filename[:-3]}\n\n")

        self.refresh()
        self.emit("open-file", str(file_path))

    def _on_new_snippet_response(self, dialog, response, entry):
        """Handle new snippet dialog response."""
        if response != "create":
            return

        name = entry.get_text().strip()
        if not name:
            return

        file_path = self.snippets_service.add(name, f"# {name}\n\n")
        self.emit("open-file", file_path)
