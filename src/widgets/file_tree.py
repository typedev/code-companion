"""File tree widget for browsing project files."""

from pathlib import Path

from gi.repository import Gtk, Gio, GLib, GObject, Pango


class FileTree(Gtk.Box):
    """A widget for browsing project files as a tree."""

    __gsignals__ = {
        "file-activated": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    # Folders to hide
    HIDDEN_FOLDERS = {".git"}

    def __init__(self, root_path: str):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)

        self.root_path = Path(root_path)
        self._expanded_paths: set[str] = set()

        self._build_ui()
        self.refresh()

    def _build_ui(self):
        """Build the file tree UI."""
        # Scrolled window
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)

        # List box for tree items
        self.list_box = Gtk.ListBox()
        self.list_box.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.list_box.add_css_class("navigation-sidebar")
        self.list_box.connect("row-activated", self._on_row_activated)

        scrolled.set_child(self.list_box)
        self.append(scrolled)

    def refresh(self):
        """Refresh the file tree."""
        # Clear existing
        while row := self.list_box.get_first_child():
            self.list_box.remove(row)

        # Build tree from root
        self._add_directory_contents(self.root_path, 0)

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
            # Skip only .git folder
            if entry.name in self.HIDDEN_FOLDERS:
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

        # Icon
        if path.is_dir():
            icon_name = "folder-symbolic"
        else:
            icon_name = self._get_file_icon(path)
        icon = Gtk.Image.new_from_icon_name(icon_name)
        box.append(icon)

        # Name
        label = Gtk.Label(label=path.name)
        label.set_xalign(0)
        label.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        label.set_hexpand(True)
        box.append(label)

        row.set_child(box)
        return row

    def _get_file_icon(self, path: Path) -> str:
        """Get appropriate icon for file type."""
        suffix = path.suffix.lower()

        icon_map = {
            ".py": "text-x-python-symbolic",
            ".js": "text-x-javascript-symbolic",
            ".ts": "text-x-javascript-symbolic",
            ".json": "text-x-generic-symbolic",
            ".md": "text-x-generic-symbolic",
            ".txt": "text-x-generic-symbolic",
            ".yaml": "text-x-generic-symbolic",
            ".yml": "text-x-generic-symbolic",
            ".toml": "text-x-generic-symbolic",
            ".sh": "text-x-script-symbolic",
            ".bash": "text-x-script-symbolic",
            ".html": "text-html-symbolic",
            ".css": "text-css-symbolic",
            ".xml": "text-xml-symbolic",
            ".rs": "text-x-generic-symbolic",
            ".go": "text-x-generic-symbolic",
            ".java": "text-x-generic-symbolic",
            ".c": "text-x-csrc-symbolic",
            ".cpp": "text-x-c++src-symbolic",
            ".h": "text-x-chdr-symbolic",
        }

        return icon_map.get(suffix, "text-x-generic-symbolic")

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
            else:
                self._expanded_paths.add(path_str)
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
