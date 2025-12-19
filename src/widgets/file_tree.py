"""File tree widget for browsing project files."""

from pathlib import Path

import pathspec
from gi.repository import Adw, Gtk, Gio, GLib, GObject, Pango, Gdk

from ..services import GitService, FileStatus, IconCache, ToastService, SettingsService, FileMonitorService


# CSS classes for git status colors
STATUS_CSS_CLASSES = {
    FileStatus.MODIFIED: "git-modified",
    FileStatus.ADDED: "git-added",
    FileStatus.DELETED: "git-deleted",
    FileStatus.RENAMED: "git-renamed",
    FileStatus.UNTRACKED: "git-added",
    FileStatus.TYPECHANGE: "git-modified",
}


class FileTree(Gtk.Box):
    """A widget for browsing project files as a tree."""

    __gsignals__ = {
        "file-activated": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        "selection-changed": (GObject.SignalFlags.RUN_FIRST, None, (bool,)),  # has_selection
        "rename-requested": (GObject.SignalFlags.RUN_FIRST, None, (str, str)),  # old_path, new_name
        "delete-requested": (GObject.SignalFlags.RUN_FIRST, None, (object,)),  # list of paths
    }

    # Folders to always hide (not toggleable)
    ALWAYS_HIDDEN = {".git"}

    # Files that should always be visible (even if in .gitignore)
    ALWAYS_VISIBLE = {".gitignore"}

    def __init__(self, root_path: str, file_monitor_service: FileMonitorService):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)

        self.root_path = Path(root_path)
        self._file_monitor_service = file_monitor_service
        self._expanded_paths: set[str] = set()
        self._git_status: dict[str, FileStatus] = {}
        self.context_menu = None

        # File filtering (only .gitignore patterns)
        self._show_ignored = False
        self._ignore_spec: pathspec.PathSpec | None = None
        self._load_ignore_patterns()

        # Initialize icon cache (singleton, loads icons once)
        self._icon_cache = IconCache()

        # Initialize git service
        self._git_service = GitService(self.root_path)
        self._is_git_repo = self._git_service.is_git_repo()
        if self._is_git_repo:
            self._git_service.open()

        self._build_ui()
        self._setup_css()
        self._populate_tree()

        # Connect to monitor service signals
        self._connect_monitor_signals()

        # Setup initial working tree monitors via service
        self._setup_initial_monitors()

    def _load_ignore_patterns(self):
        """Load ignore patterns from .gitignore only."""
        patterns = []

        # Load .gitignore if exists
        gitignore_path = self.root_path / ".gitignore"
        if gitignore_path.exists():
            try:
                with open(gitignore_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        # Skip comments and empty lines
                        if line and not line.startswith("#"):
                            patterns.append(line)
            except OSError:
                pass

        # Create pathspec matcher (None if no patterns)
        if patterns:
            self._ignore_spec = pathspec.PathSpec.from_lines(
                pathspec.patterns.GitWildMatchPattern,
                patterns
            )
        else:
            self._ignore_spec = None

    def _is_ignored(self, path: Path) -> bool:
        """Check if a path should be ignored."""
        if self._ignore_spec is None:
            return False

        try:
            relative = path.relative_to(self.root_path)
            # Add trailing slash for directories to match directory patterns
            match_path = str(relative) + "/" if path.is_dir() else str(relative)
            return self._ignore_spec.match_file(match_path)
        except ValueError:
            return False

    @property
    def show_ignored(self) -> bool:
        """Get whether ignored files (from .gitignore) are shown."""
        return self._show_ignored

    @show_ignored.setter
    def show_ignored(self, value: bool):
        """Set whether ignored files are shown."""
        if self._show_ignored != value:
            self._show_ignored = value
            self.refresh()

    def _build_ui(self):
        """Build the file tree UI."""
        # Scrolled window
        self.scrolled = Gtk.ScrolledWindow()
        self.scrolled.set_vexpand(True)
        self.scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)

        # List box for tree items
        self.list_box = Gtk.ListBox()
        self.list_box.set_selection_mode(Gtk.SelectionMode.NONE)  # We handle selection manually
        self.list_box.add_css_class("navigation-sidebar")
        self.list_box.connect("row-activated", self._on_row_activated)

        # Track selection manually for multi-select support
        self._selected_rows: set[Gtk.ListBoxRow] = set()
        self._last_clicked_row: Gtk.ListBoxRow | None = None

        # Left-click for selection (with Ctrl/Shift support)
        left_click = Gtk.GestureClick()
        left_click.set_button(1)  # Left click
        left_click.connect("pressed", self._on_left_click)
        self.list_box.add_controller(left_click)

        # Right-click context menu
        right_click = Gtk.GestureClick()
        right_click.set_button(3)  # Right click
        right_click.connect("pressed", self._on_right_click)
        self.list_box.add_controller(right_click)

        # Keyboard shortcuts
        key_controller = Gtk.EventControllerKey()
        key_controller.connect("key-pressed", self._on_key_pressed)
        self.list_box.add_controller(key_controller)

        self.scrolled.set_child(self.list_box)
        self.append(self.scrolled)

        # Create context menu
        self._create_context_menu()

    def _setup_css(self):
        """Set up CSS for git status colors and selection."""
        css = b"""
        .git-modified { color: #f1c40f; }
        .git-added { color: #2ecc71; }
        .git-deleted { color: #e74c3c; }
        .git-renamed { color: #3498db; }
        .git-indicator {
            font-size: 8px;
            margin-left: 4px;
        }
        .file-selected {
            background-color: alpha(@accent_color, 0.3);
        }
        .file-selected:hover {
            background-color: alpha(@accent_color, 0.4);
        }
        """
        provider = Gtk.CssProvider()
        provider.load_from_data(css)
        Gtk.StyleContext.add_provider_for_display(
            self.get_display(),
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

    def _create_context_menu(self):
        """Create the right-click context menu."""
        menu = Gio.Menu()

        # Copy section
        copy_section = Gio.Menu()
        copy_section.append("Copy Path", "filetree.copy-path")
        copy_section.append("Copy Relative Path", "filetree.copy-relative-path")
        menu.append_section(None, copy_section)

        # Edit section
        edit_section = Gio.Menu()
        edit_section.append("Rename…", "filetree.rename")
        edit_section.append("Delete", "filetree.delete")
        menu.append_section(None, edit_section)

        self.context_menu = Gtk.PopoverMenu.new_from_model(menu)
        self.context_menu.set_parent(self.list_box)
        self.context_menu.set_has_arrow(False)

        # Action group
        action_group = Gio.SimpleActionGroup()

        copy_path_action = Gio.SimpleAction.new("copy-path", None)
        copy_path_action.connect("activate", self._on_copy_path)
        action_group.add_action(copy_path_action)

        copy_rel_path_action = Gio.SimpleAction.new("copy-relative-path", None)
        copy_rel_path_action.connect("activate", self._on_copy_relative_path)
        action_group.add_action(copy_rel_path_action)

        rename_action = Gio.SimpleAction.new("rename", None)
        rename_action.connect("activate", self._on_rename_action)
        action_group.add_action(rename_action)
        self._rename_action = rename_action

        delete_action = Gio.SimpleAction.new("delete", None)
        delete_action.connect("activate", self._on_delete_action)
        action_group.add_action(delete_action)
        self._delete_action = delete_action

        self.list_box.insert_action_group("filetree", action_group)

    def _on_left_click(self, gesture, n_press, x, y):
        """Handle left-click for selection with Ctrl/Shift support."""
        row = self.list_box.get_row_at_y(int(y))
        if not row or not hasattr(row, "path"):
            return

        # Get modifier state
        state = gesture.get_current_event_state()
        ctrl_pressed = state & Gdk.ModifierType.CONTROL_MASK
        shift_pressed = state & Gdk.ModifierType.SHIFT_MASK

        if ctrl_pressed:
            # Ctrl+Click: toggle selection
            if row in self._selected_rows:
                self._deselect_row(row)
            else:
                self._select_row(row)
            self._last_clicked_row = row
        elif shift_pressed and self._last_clicked_row:
            # Shift+Click: select range
            self._select_range(self._last_clicked_row, row)
        else:
            # Normal click: select only this row
            self._clear_selection()
            self._select_row(row)
            self._last_clicked_row = row

        # Emit selection changed signal
        self.emit("selection-changed", len(self._selected_rows) > 0)

    def _on_right_click(self, gesture, n_press, x, y):
        """Handle right-click to show context menu."""
        row = self.list_box.get_row_at_y(int(y))
        if row and hasattr(row, "path"):
            # Add to selection if not already selected
            if row not in self._selected_rows:
                self._select_row(row)
                self.emit("selection-changed", True)

        # Update menu items based on selection
        self._update_context_menu_sensitivity()

        # Position and show menu
        rect = Gdk.Rectangle()
        rect.x = int(x)
        rect.y = int(y)
        rect.width = 1
        rect.height = 1
        self.context_menu.set_pointing_to(rect)
        self.context_menu.popup()

    def _select_row(self, row: Gtk.ListBoxRow):
        """Add a row to selection."""
        if row not in self._selected_rows:
            self._selected_rows.add(row)
            row.add_css_class("file-selected")

    def _deselect_row(self, row: Gtk.ListBoxRow):
        """Remove a row from selection."""
        if row in self._selected_rows:
            self._selected_rows.discard(row)
            row.remove_css_class("file-selected")

    def _clear_selection(self):
        """Clear all selection."""
        for row in list(self._selected_rows):
            row.remove_css_class("file-selected")
        self._selected_rows.clear()

    def _select_range(self, start_row: Gtk.ListBoxRow, end_row: Gtk.ListBoxRow):
        """Select all rows between start and end (inclusive)."""
        # Get indices
        start_idx = start_row.get_index()
        end_idx = end_row.get_index()

        if start_idx > end_idx:
            start_idx, end_idx = end_idx, start_idx

        # Clear existing selection and select range
        self._clear_selection()
        for i in range(start_idx, end_idx + 1):
            row = self.list_box.get_row_at_index(i)
            if row:
                self._select_row(row)

    def _select_all(self):
        """Select all visible rows."""
        i = 0
        while True:
            row = self.list_box.get_row_at_index(i)
            if row is None:
                break
            self._select_row(row)
            i += 1
        self.emit("selection-changed", len(self._selected_rows) > 0)

    def _on_key_pressed(self, controller, keyval, keycode, state):
        """Handle keyboard shortcuts."""
        ctrl_pressed = state & Gdk.ModifierType.CONTROL_MASK

        if ctrl_pressed and keyval == Gdk.KEY_c:
            # Ctrl+C - copy relative path
            self._copy_selected_paths(relative=True)
            return True

        if ctrl_pressed and keyval == Gdk.KEY_a:
            # Ctrl+A - select all visible rows
            self._select_all()
            return True

        if keyval == Gdk.KEY_F2:
            # F2 - rename selected (single selection only)
            paths = self.get_selected_paths()
            if len(paths) == 1:
                self._show_rename_dialog(paths[0])
            return True

        if keyval == Gdk.KEY_Delete:
            # Delete - delete selected
            paths = self.get_selected_paths()
            if paths:
                self.emit("delete-requested", [str(p) for p in paths])
            return True

        return False

    def get_selected_paths(self) -> list[Path]:
        """Get list of selected paths."""
        paths = []
        for row in self._selected_rows:
            if hasattr(row, "path"):
                paths.append(row.path)
        return paths

    def _on_copy_path(self, action, param):
        """Copy full path(s) to clipboard."""
        self._copy_selected_paths(relative=False)

    def _on_copy_relative_path(self, action, param):
        """Copy relative path(s) to clipboard."""
        self._copy_selected_paths(relative=True)

    def _copy_selected_paths(self, relative: bool):
        """Copy selected paths to clipboard."""
        paths = self.get_selected_paths()
        if not paths:
            return

        if relative:
            text = "\n".join(self._get_relative_path(p) for p in paths)
        else:
            text = "\n".join(str(p) for p in paths)

        clipboard = Gdk.Display.get_default().get_clipboard()
        clipboard.set(text)

    def _update_context_menu_sensitivity(self):
        """Update context menu items based on current selection."""
        paths = self.get_selected_paths()
        has_selection = len(paths) > 0
        single_selection = len(paths) == 1

        # Rename only works for single selection
        if hasattr(self, "_rename_action"):
            self._rename_action.set_enabled(single_selection)

        # Delete works for any selection
        if hasattr(self, "_delete_action"):
            self._delete_action.set_enabled(has_selection)

    def _on_rename_action(self, action, param):
        """Handle rename menu action."""
        paths = self.get_selected_paths()
        if len(paths) != 1:
            return
        self._show_rename_dialog(paths[0])

    def _on_delete_action(self, action, param):
        """Handle delete menu action."""
        paths = self.get_selected_paths()
        if paths:
            self.emit("delete-requested", [str(p) for p in paths])

    def _show_rename_dialog(self, path: Path):
        """Show dialog to rename a file or folder."""
        dialog = Adw.AlertDialog()
        dialog.set_heading("Rename")

        item_type = "folder" if path.is_dir() else "file"
        dialog.set_body(f"Enter new name for {item_type}:")

        # Entry for new name
        entry = Gtk.Entry()
        entry.set_text(path.name)
        entry.set_hexpand(True)
        # Select filename without extension for files
        if not path.is_dir() and "." in path.name:
            # Select only the name part, not extension
            name_without_ext = path.stem
            GLib.idle_add(lambda: entry.select_region(0, len(name_without_ext)))

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.set_margin_start(12)
        box.set_margin_end(12)
        box.append(entry)

        dialog.set_extra_child(box)

        dialog.add_response("cancel", "Cancel")
        dialog.add_response("rename", "Rename")
        dialog.set_response_appearance("rename", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("rename")
        dialog.set_close_response("cancel")

        dialog.connect("response", self._on_rename_dialog_response, entry, path)

        # Get the toplevel window for presenting the dialog
        root = self.get_root()
        dialog.present(root)

    def _on_rename_dialog_response(self, dialog, response, entry, old_path: Path):
        """Handle rename dialog response."""
        if response != "rename":
            return

        new_name = entry.get_text().strip()
        if not new_name:
            return

        # Validate name
        if "/" in new_name or "\\" in new_name or "\0" in new_name:
            ToastService.show_error("Invalid characters in name")
            return

        if new_name == old_path.name:
            return  # No change

        # Emit signal for ProjectWindow to handle
        self.emit("rename-requested", str(old_path), new_name)

    def _populate_tree(self):
        """Initial population of the tree (without recreating list_box)."""
        # Update git status
        if self._is_git_repo:
            self._git_status = self._git_service.get_file_status_map()
        else:
            self._git_status = {}

        # Build tree from root
        self._add_directory_contents(self.root_path, 0)

    def refresh(self):
        """Refresh the file tree."""
        # Save scroll position
        vadj = self.scrolled.get_vadjustment()
        scroll_pos = vadj.get_value()

        # Save selected paths for restoration
        selected_paths: set[str] = set()
        for row in self._selected_rows:
            if hasattr(row, "path"):
                selected_paths.add(str(row.path))

        # Update git status
        if self._is_git_repo:
            self._git_status = self._git_service.get_file_status_map()
        else:
            self._git_status = {}

        # Recreate list box to avoid GTK remove warnings
        if self.context_menu:
            self.context_menu.unparent()

        old_list_box = self.list_box
        self.list_box = Gtk.ListBox()
        self.list_box.set_selection_mode(Gtk.SelectionMode.NONE)
        self.list_box.add_css_class("navigation-sidebar")
        self.list_box.connect("row-activated", self._on_row_activated)

        # Clear selection tracking (will restore after building tree)
        self._selected_rows = set()
        self._last_clicked_row = None

        # Left-click for selection
        left_click = Gtk.GestureClick()
        left_click.set_button(1)
        left_click.connect("pressed", self._on_left_click)
        self.list_box.add_controller(left_click)

        # Right-click context menu
        right_click = Gtk.GestureClick()
        right_click.set_button(3)
        right_click.connect("pressed", self._on_right_click)
        self.list_box.add_controller(right_click)

        # Keyboard shortcuts
        key_controller = Gtk.EventControllerKey()
        key_controller.connect("key-pressed", self._on_key_pressed)
        self.list_box.add_controller(key_controller)

        # Replace in scrolled window
        self.scrolled.set_child(self.list_box)

        # Recreate context menu with new parent
        self._create_context_menu()

        # Build tree from root
        self._add_directory_contents(self.root_path, 0)

        # Restore selection and scroll position after UI updates
        def restore_state():
            # Restore selection
            if selected_paths:
                i = 0
                while True:
                    row = self.list_box.get_row_at_index(i)
                    if row is None:
                        break
                    if hasattr(row, "path") and str(row.path) in selected_paths:
                        self._select_row(row)
                    i += 1
            # Restore scroll
            vadj.set_value(scroll_pos)
            return False

        GLib.idle_add(restore_state)

    def _add_directory_contents(self, directory: Path, depth: int):
        """Add contents of a directory to the tree."""
        try:
            entries = sorted(
                directory.iterdir(),
                key=lambda p: (not p.is_dir(), p.name.lower())
            )
        except PermissionError:
            return

        for entry in entries:
            # Always skip .git folder
            if entry.name in self.ALWAYS_HIDDEN:
                continue

            # Always show certain files (like .gitignore)
            if entry.name in self.ALWAYS_VISIBLE:
                pass  # Don't skip
            # Skip ignored files (from .gitignore) unless show_ignored is True
            elif not self._show_ignored and self._is_ignored(entry):
                continue

            row = self._create_row(entry, depth)
            self.list_box.append(row)

            # If directory is expanded, add its contents
            if entry.is_dir() and str(entry) in self._expanded_paths:
                self._add_directory_contents(entry, depth + 1)

    def _create_row(self, path: Path, depth: int) -> Gtk.ListBoxRow:
        """Create a row for a file or directory."""
        row = Gtk.ListBoxRow()
        row.path = path
        row.is_dir = path.is_dir()

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        box.set_margin_start(12 + depth * 16)
        box.set_margin_end(12)
        box.set_margin_top(4)
        box.set_margin_bottom(4)

        # Expand indicator for directories
        if path.is_dir():
            is_expanded = str(path) in self._expanded_paths
            expander_icon = "pan-down-symbolic" if is_expanded else "pan-end-symbolic"
            expander = Gtk.Image.new_from_icon_name(expander_icon)
            expander.add_css_class("dim-label")
            box.append(expander)
            row.expander = expander
        else:
            # Spacer for alignment
            spacer = Gtk.Box()
            spacer.set_size_request(16, -1)
            box.append(spacer)

        # Icon (from cached Material Design icons, using GIcon for crisp rendering)
        is_expanded = str(path) in self._expanded_paths if path.is_dir() else False
        gicon = self._icon_cache.get_gicon(path, is_open=is_expanded)

        if gicon:
            icon = Gtk.Image.new_from_gicon(gicon)
            icon.set_pixel_size(16)
        else:
            # Fallback to system icon
            icon_name = "folder-symbolic" if path.is_dir() else "text-x-generic-symbolic"
            icon = Gtk.Image.new_from_icon_name(icon_name)
        box.append(icon)

        # Name
        label = Gtk.Label(label=path.name)
        label.set_xalign(0)
        label.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        label.set_hexpand(True)

        # Apply git status color to label
        relative_path = self._get_relative_path(path)
        git_status = self._git_status.get(relative_path)
        if git_status:
            css_class = STATUS_CSS_CLASSES.get(git_status)
            if css_class:
                label.add_css_class(css_class)

        box.append(label)

        # Git status indicator (colored dot)
        if git_status:
            indicator = Gtk.Label(label="●")
            indicator.add_css_class("git-indicator")
            css_class = STATUS_CSS_CLASSES.get(git_status)
            if css_class:
                indicator.add_css_class(css_class)
            box.append(indicator)

        row.set_child(box)
        return row

    def _get_relative_path(self, path: Path) -> str:
        """Get path relative to repository root."""
        try:
            return str(path.relative_to(self.root_path))
        except ValueError:
            return str(path)

    def _on_row_activated(self, list_box, row):
        """Handle row activation."""
        if not hasattr(row, "path"):
            return

        path = row.path

        if row.is_dir:
            # Toggle expansion
            path_str = str(path)
            if path_str in self._expanded_paths:
                self._expanded_paths.discard(path_str)
                # Remove monitor when collapsing
                self._file_monitor_service.remove_working_tree_monitor(path)
            else:
                self._expanded_paths.add(path_str)
                # Add monitor when expanding
                self._file_monitor_service.add_working_tree_monitor(path)
            self.refresh()
        else:
            # Emit file activated signal
            self.emit("file-activated", str(path))

    def expand_to_path(self, file_path: str):
        """Expand tree to show a specific file."""
        path = Path(file_path)
        try:
            relative = path.relative_to(self.root_path)
            current = self.root_path
            for part in relative.parts[:-1]:
                current = current / part
                self._expanded_paths.add(str(current))
            self.refresh()
        except ValueError:
            pass  # Path not under root

    # --- File System Monitoring via FileMonitorService ---

    def _connect_monitor_signals(self):
        """Connect to FileMonitorService signals."""
        self._file_monitor_service.connect("git-status-changed", self._on_git_status_changed)
        self._file_monitor_service.connect("working-tree-changed", self._on_working_tree_changed)

    def _setup_initial_monitors(self):
        """Setup initial working tree monitors via service."""
        # Monitor root directory
        self._file_monitor_service.add_working_tree_monitor(self.root_path)

        # Monitor all expanded directories
        for path_str in self._expanded_paths:
            self._file_monitor_service.add_working_tree_monitor(Path(path_str))

    def _update_monitors(self):
        """Update monitors to match currently expanded directories."""
        # Directories that should be monitored
        should_monitor = {str(self.root_path)}
        for path_str in self._expanded_paths:
            should_monitor.add(path_str)

        # Get currently monitored directories from service
        currently_monitored = self._file_monitor_service.get_monitored_directories()

        # Add new monitors
        for path_str in should_monitor:
            if path_str not in currently_monitored:
                self._file_monitor_service.add_working_tree_monitor(Path(path_str))

        # Remove stale monitors (except root)
        root_str = str(self.root_path)
        for path_str in currently_monitored:
            if path_str not in should_monitor and path_str != root_str:
                self._file_monitor_service.remove_working_tree_monitor(Path(path_str))

    def _on_git_status_changed(self, service):
        """Handle git status changes from monitor service."""
        self.refresh()

    def _on_working_tree_changed(self, service, path: str):
        """Handle working tree changes from monitor service."""
        # Check if .gitignore was changed
        if path.endswith(".gitignore"):
            self._load_ignore_patterns()

        self.refresh()
        # Update monitors for newly expanded/collapsed directories
        self._update_monitors()
