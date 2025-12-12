"""File tree widget for browsing project files."""

from pathlib import Path

import pathspec
from gi.repository import Gtk, Gio, GLib, GObject, Pango, Gdk

from ..services import GitService, FileStatus, IconCache, ToastService


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
    }

    # Folders to always hide (not toggleable)
    ALWAYS_HIDDEN = {".git"}

    # Default ignore patterns (common build/dependency folders)
    DEFAULT_IGNORE_PATTERNS = [
        "node_modules/",
        "__pycache__/",
        "*.pyc",
        ".venv/",
        "venv/",
        ".env/",
        "env/",
        ".mypy_cache/",
        ".pytest_cache/",
        ".ruff_cache/",
        "dist/",
        "build/",
        "*.egg-info/",
        ".tox/",
        ".nox/",
        "coverage/",
        ".coverage",
        "htmlcov/",
        ".DS_Store",
        "Thumbs.db",
        "*.swp",
        "*.swo",
        "*~",
    ]

    # Debounce delay for file system events (ms)
    FS_DEBOUNCE_DELAY = 300

    def __init__(self, root_path: str):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)

        self.root_path = Path(root_path)
        self._expanded_paths: set[str] = set()
        self._git_status: dict[str, FileStatus] = {}
        self.context_menu = None

        # File filtering
        self._show_ignored = False
        self._ignore_spec: pathspec.PathSpec | None = None
        self._load_ignore_patterns()

        # File monitoring
        self._file_monitors: dict[str, Gio.FileMonitor] = {}
        self._refresh_scheduled = False
        self._refresh_timeout_id: int | None = None

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

        # Start file system monitoring
        self._setup_file_monitoring()

        # Cleanup on destroy
        self.connect("destroy", self._on_destroy)

    def _load_ignore_patterns(self):
        """Load ignore patterns from .gitignore and defaults."""
        patterns = list(self.DEFAULT_IGNORE_PATTERNS)

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

        # Create pathspec matcher
        self._ignore_spec = pathspec.PathSpec.from_lines(
            pathspec.patterns.GitWildMatchPattern,
            patterns
        )

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
        """Get whether ignored files are shown."""
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
        self.list_box.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.list_box.add_css_class("navigation-sidebar")
        self.list_box.connect("row-activated", self._on_row_activated)

        # Right-click context menu
        click_controller = Gtk.GestureClick()
        click_controller.set_button(3)  # Right click
        click_controller.connect("pressed", self._on_right_click)
        self.list_box.add_controller(click_controller)

        # Keyboard shortcuts
        key_controller = Gtk.EventControllerKey()
        key_controller.connect("key-pressed", self._on_key_pressed)
        self.list_box.add_controller(key_controller)

        self.scrolled.set_child(self.list_box)
        self.append(self.scrolled)

        # Create context menu
        self._create_context_menu()

    def _setup_css(self):
        """Set up CSS for git status colors."""
        css = b"""
        .git-modified { color: #f1c40f; }
        .git-added { color: #2ecc71; }
        .git-deleted { color: #e74c3c; }
        .git-renamed { color: #3498db; }
        .git-indicator {
            font-size: 8px;
            margin-left: 4px;
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
        menu.append("Copy Path", "filetree.copy-path")
        menu.append("Copy Relative Path", "filetree.copy-relative-path")

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

        self.list_box.insert_action_group("filetree", action_group)

    def _on_right_click(self, gesture, n_press, x, y):
        """Handle right-click to show context menu."""
        # Get row at position
        row = self.list_box.get_row_at_y(int(y))
        if row and hasattr(row, "path"):
            # Select the row if not already selected
            if not row.is_selected():
                self.list_box.unselect_all()
                self.list_box.select_row(row)

        # Position and show menu
        rect = Gdk.Rectangle()
        rect.x = int(x)
        rect.y = int(y)
        rect.width = 1
        rect.height = 1
        self.context_menu.set_pointing_to(rect)
        self.context_menu.popup()

    def _on_key_pressed(self, controller, keyval, keycode, state):
        """Handle keyboard shortcuts."""
        ctrl_pressed = state & Gdk.ModifierType.CONTROL_MASK

        if ctrl_pressed and keyval == Gdk.KEY_c:
            # Ctrl+C - copy relative path
            self._copy_selected_paths(relative=True)
            return True

        return False

    def _get_selected_paths(self) -> list[Path]:
        """Get list of selected paths."""
        paths = []
        for row in self.list_box.get_selected_rows():
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
        paths = self._get_selected_paths()
        if not paths:
            return

        if relative:
            text = "\n".join(self._get_relative_path(p) for p in paths)
        else:
            text = "\n".join(str(p) for p in paths)

        clipboard = Gdk.Display.get_default().get_clipboard()
        clipboard.set(text)

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

        # Save selected path
        selected_path: str | None = None
        selected_rows = self.list_box.get_selected_rows()
        if selected_rows and hasattr(selected_rows[0], "path"):
            selected_path = str(selected_rows[0].path)

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
        self.list_box.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.list_box.add_css_class("navigation-sidebar")
        self.list_box.connect("row-activated", self._on_row_activated)

        # Right-click context menu
        click_controller = Gtk.GestureClick()
        click_controller.set_button(3)
        click_controller.connect("pressed", self._on_right_click)
        self.list_box.add_controller(click_controller)

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
            if selected_path:
                i = 0
                while True:
                    row = self.list_box.get_row_at_index(i)
                    if row is None:
                        break
                    if hasattr(row, "path") and str(row.path) == selected_path:
                        self.list_box.select_row(row)
                        break
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

            # Skip ignored files unless show_ignored is True
            if not self._show_ignored and self._is_ignored(entry):
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

        # Icon (from cached Material Design icons)
        if path.is_dir():
            is_expanded = str(path) in self._expanded_paths
            texture = self._icon_cache.get_folder_icon(path, is_open=is_expanded)
        else:
            texture = self._icon_cache.get_file_icon(path)

        if texture:
            icon = Gtk.Image.new_from_paintable(texture)
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
                self._remove_monitor(path)
            else:
                self._expanded_paths.add(path_str)
                # Add monitor when expanding
                self._add_monitor(path)
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

    # --- File System Monitoring ---

    def _setup_file_monitoring(self):
        """Set up file system monitoring for the project."""
        # Monitor root directory
        self._add_monitor(self.root_path)

        # Monitor all expanded directories
        for path_str in self._expanded_paths:
            self._add_monitor(Path(path_str))

    def _add_monitor(self, directory: Path):
        """Add a file monitor for a directory."""
        path_str = str(directory)

        # Skip if already monitoring or hidden
        if path_str in self._file_monitors:
            return
        if directory.name in self.ALWAYS_HIDDEN:
            return

        try:
            gfile = Gio.File.new_for_path(path_str)
            monitor = gfile.monitor_directory(
                Gio.FileMonitorFlags.WATCH_MOVES,
                None
            )
            monitor.connect("changed", self._on_file_changed)
            self._file_monitors[path_str] = monitor
        except GLib.Error:
            pass  # Directory may not exist or be accessible

    def _remove_monitor(self, directory: Path):
        """Remove a file monitor for a directory."""
        path_str = str(directory)
        if path_str in self._file_monitors:
            monitor = self._file_monitors.pop(path_str)
            monitor.cancel()

    def _update_monitors(self):
        """Update monitors to match currently expanded directories."""
        # Directories that should be monitored
        should_monitor = {str(self.root_path)}
        for path_str in self._expanded_paths:
            should_monitor.add(path_str)

        # Add new monitors
        for path_str in should_monitor:
            if path_str not in self._file_monitors:
                self._add_monitor(Path(path_str))

        # Remove stale monitors (except root)
        for path_str in list(self._file_monitors.keys()):
            if path_str not in should_monitor and path_str != str(self.root_path):
                self._remove_monitor(Path(path_str))

    def _on_file_changed(self, monitor, file, other_file, event_type):
        """Handle file system changes."""
        # Only react to relevant events
        if event_type not in (
            Gio.FileMonitorEvent.CHANGED,
            Gio.FileMonitorEvent.CREATED,
            Gio.FileMonitorEvent.DELETED,
            Gio.FileMonitorEvent.MOVED_IN,
            Gio.FileMonitorEvent.MOVED_OUT,
            Gio.FileMonitorEvent.RENAMED,
        ):
            return

        # Ignore hidden files and .git changes
        filename = file.get_basename()
        if filename.startswith("."):
            # Still allow refresh for non-.git hidden files if they affect tree
            if filename == ".git" or file.get_path().startswith(str(self.root_path / ".git")):
                return

        # Schedule debounced refresh
        self._schedule_refresh()

    def _schedule_refresh(self):
        """Schedule a debounced refresh."""
        # Cancel any pending refresh
        if self._refresh_timeout_id is not None:
            GLib.source_remove(self._refresh_timeout_id)

        # Schedule new refresh
        self._refresh_timeout_id = GLib.timeout_add(
            self.FS_DEBOUNCE_DELAY,
            self._do_scheduled_refresh
        )

    def _do_scheduled_refresh(self) -> bool:
        """Perform the scheduled refresh."""
        self._refresh_timeout_id = None
        self.refresh()
        # Update monitors for newly expanded/collapsed directories
        self._update_monitors()
        return False  # Don't repeat

    def _on_destroy(self, widget):
        """Clean up monitors on widget destruction."""
        # Cancel pending refresh
        if self._refresh_timeout_id is not None:
            GLib.source_remove(self._refresh_timeout_id)
            self._refresh_timeout_id = None

        # Cancel all monitors
        for monitor in self._file_monitors.values():
            monitor.cancel()
        self._file_monitors.clear()
