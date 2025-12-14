"""Commit detail view widget showing files and diffs."""

from pathlib import Path

from gi.repository import Gtk, GObject, Adw, Gdk, GLib

from ..services import GitService, GitCommit
from .code_view import DiffView


# Status colors
STATUS_COLORS = {
    "A": "#2ecc71",  # green
    "D": "#e74c3c",  # red
    "M": "#f1c40f",  # yellow
    "R": "#3498db",  # blue
    "C": "#9b59b6",  # purple
    "T": "#f39c12",  # orange
}


class CommitDetailView(Gtk.Box):
    """Widget for viewing commit details with file list and per-file diffs.

    Layout:
    - Top: Header (SHA, author, date)
    - Middle: Horizontal split - files list (left), full message (right)
    - Bottom: Diff view for selected file
    """

    def __init__(self, git_service: GitService, commit: GitCommit):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)

        self.service = git_service
        self.commit = commit
        self._files: list[dict] = []

        self._setup_css()
        self._build_ui()
        self._load_files()

    def _setup_css(self):
        """Set up CSS for the view."""
        css = b"""
        .commit-header {
            padding: 12px;
            background: alpha(@card_bg_color, 0.5);
        }
        .commit-sha {
            font-family: monospace;
            color: #f39c12;
        }
        .commit-message-title {
            font-weight: bold;
            font-size: 1.1em;
        }
        .commit-message-body {
            font-family: monospace;
            font-size: 0.95em;
        }
        .file-stats {
            font-family: monospace;
            font-size: 0.9em;
        }
        .additions {
            color: #2ecc71;
        }
        .deletions {
            color: #e74c3c;
        }
        .section-header {
            font-weight: bold;
            padding: 8px 12px;
            background: alpha(@card_bg_color, 0.3);
        }
        """
        provider = Gtk.CssProvider()
        provider.load_from_data(css)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

    def _build_ui(self):
        """Build the UI."""
        # Header with commit info (compact)
        header = self._build_header()
        self.append(header)

        # Main vertical paned: top (files + message), bottom (diff)
        self.main_paned = Gtk.Paned(orientation=Gtk.Orientation.VERTICAL)
        self.main_paned.set_vexpand(True)

        # Top section: horizontal paned (files left, message right)
        self.top_paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        self.top_paned.set_vexpand(False)

        # Files list (left)
        files_box = self._build_files_list()
        self.top_paned.set_start_child(files_box)
        self.top_paned.set_resize_start_child(True)
        self.top_paned.set_shrink_start_child(False)

        # Full message (right)
        message_box = self._build_message_view()
        self.top_paned.set_end_child(message_box)
        self.top_paned.set_resize_end_child(True)
        self.top_paned.set_shrink_end_child(False)

        # Set initial position (50/50 split)
        self.top_paned.set_position(300)

        self.main_paned.set_start_child(self.top_paned)
        self.main_paned.set_resize_start_child(True)
        self.main_paned.set_shrink_start_child(False)

        # Diff view (bottom)
        self.diff_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.diff_container.set_vexpand(True)

        # Initial state
        placeholder = Gtk.Label(label="Select a file to view diff")
        placeholder.add_css_class("dim-label")
        placeholder.set_vexpand(True)
        placeholder.set_valign(Gtk.Align.CENTER)
        self.diff_container.append(placeholder)

        self.main_paned.set_end_child(self.diff_container)
        self.main_paned.set_resize_end_child(True)
        self.main_paned.set_shrink_end_child(False)

        self.append(self.main_paned)

    def _build_header(self) -> Gtk.Box:
        """Build compact commit header."""
        self.header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        self.header_box.add_css_class("commit-header")

        # SHA + copy button
        sha_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)

        self.sha_label = Gtk.Label(label=self.commit.short_hash)
        self.sha_label.add_css_class("commit-sha")
        sha_box.append(self.sha_label)

        copy_btn = Gtk.Button()
        copy_btn.set_icon_name("edit-copy-symbolic")
        copy_btn.add_css_class("flat")
        copy_btn.set_tooltip_text("Copy full SHA")
        copy_btn.connect("clicked", self._on_copy_sha)
        sha_box.append(copy_btn)

        self.header_box.append(sha_box)

        # Separator
        sep1 = Gtk.Label(label="·")
        sep1.add_css_class("dim-label")
        self.header_box.append(sep1)

        # Author
        self.author_label = Gtk.Label(label=self.commit.author)
        self.header_box.append(self.author_label)

        # Separator
        sep2 = Gtk.Label(label="·")
        sep2.add_css_class("dim-label")
        self.header_box.append(sep2)

        # Date
        self.date_label = Gtk.Label(label=self.commit.timestamp.strftime('%Y-%m-%d %H:%M'))
        self.date_label.add_css_class("dim-label")
        self.header_box.append(self.date_label)

        # Spacer
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        self.header_box.append(spacer)

        return self.header_box

    def _build_files_list(self) -> Gtk.Box:
        """Build the files list section."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.set_size_request(200, -1)

        # Header
        self.files_count_label = Gtk.Label(label="Files")
        self.files_count_label.add_css_class("section-header")
        self.files_count_label.set_xalign(0)
        box.append(self.files_count_label)

        # List
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_vexpand(True)

        self.files_list = Gtk.ListBox()
        self.files_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.files_list.add_css_class("boxed-list")
        self.files_list.set_margin_start(8)
        self.files_list.set_margin_end(8)
        self.files_list.set_margin_bottom(8)
        self.files_list.connect("row-selected", self._on_file_selected)

        scrolled.set_child(self.files_list)
        box.append(scrolled)

        return box

    def _build_message_view(self) -> Gtk.Box:
        """Build the full commit message view."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        # Header
        header = Gtk.Label(label="Message")
        header.add_css_class("section-header")
        header.set_xalign(0)
        box.append(header)

        # Message content in scrolled window
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_vexpand(True)

        self.message_content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.message_content_box.set_margin_start(12)
        self.message_content_box.set_margin_end(12)
        self.message_content_box.set_margin_top(8)
        self.message_content_box.set_margin_bottom(8)

        self._update_message_content()

        scrolled.set_child(self.message_content_box)
        box.append(scrolled)

        return box

    def _update_message_content(self):
        """Update message content labels."""
        # Clear existing content
        child = self.message_content_box.get_first_child()
        while child:
            next_child = child.get_next_sibling()
            self.message_content_box.remove(child)
            child = next_child

        # Split message into title and body
        message_lines = self.commit.message.split("\n")
        title = message_lines[0] if message_lines else ""
        body = "\n".join(message_lines[1:]).strip() if len(message_lines) > 1 else ""

        # Title (first line)
        title_label = Gtk.Label(label=title)
        title_label.add_css_class("commit-message-title")
        title_label.set_xalign(0)
        title_label.set_wrap(True)
        title_label.set_wrap_mode(2)  # WORD_CHAR
        title_label.set_selectable(True)
        self.message_content_box.append(title_label)

        # Body (rest of message)
        if body:
            body_label = Gtk.Label(label=body)
            body_label.add_css_class("commit-message-body")
            body_label.set_xalign(0)
            body_label.set_wrap(True)
            body_label.set_wrap_mode(2)  # WORD_CHAR
            body_label.set_selectable(True)
            self.message_content_box.append(body_label)

    def _load_files(self):
        """Load files for the commit."""
        self._files = self.service.get_commit_files(self.commit.hash)
        self.files_count_label.set_label(f"Files ({len(self._files)})")

        for file_info in self._files:
            row = self._create_file_row(file_info)
            self.files_list.append(row)

        # Auto-select first file
        if self._files:
            first_row = self.files_list.get_row_at_index(0)
            if first_row:
                self.files_list.select_row(first_row)

    def _create_file_row(self, file_info: dict) -> Gtk.ListBoxRow:
        """Create a row for a file."""
        row = Gtk.ListBoxRow()
        row.file_path = file_info["path"]

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        box.set_margin_start(8)
        box.set_margin_end(8)
        box.set_margin_top(6)
        box.set_margin_bottom(6)

        # Status indicator
        status = file_info["status"]
        status_label = Gtk.Label(label=status)
        status_label.set_width_chars(2)
        color = STATUS_COLORS.get(status, "#888")
        status_label.set_markup(f'<span color="{color}" weight="bold">{status}</span>')
        box.append(status_label)

        # File path (just filename for compactness)
        path = Path(file_info["path"])
        path_label = Gtk.Label(label=path.name)
        path_label.set_xalign(0)
        path_label.set_hexpand(True)
        path_label.set_ellipsize(2)  # MIDDLE
        path_label.set_tooltip_text(file_info["path"])
        box.append(path_label)

        # Stats
        additions = file_info["additions"]
        deletions = file_info["deletions"]
        if additions > 0 or deletions > 0:
            stats = Gtk.Label()
            stats.add_css_class("file-stats")
            parts = []
            if additions > 0:
                parts.append(f'<span class="additions">+{additions}</span>')
            if deletions > 0:
                parts.append(f'<span class="deletions">-{deletions}</span>')
            stats.set_markup(" ".join(parts))
            box.append(stats)

        row.set_child(box)
        return row

    def _on_file_selected(self, list_box, row):
        """Handle file selection - show diff."""
        if row is None or not hasattr(row, "file_path"):
            return

        file_path = row.file_path

        # Get diff for this file
        diff_text = self.service.get_commit_file_diff(self.commit.hash, file_path)

        # Clear diff container
        child = self.diff_container.get_first_child()
        while child:
            next_child = child.get_next_sibling()
            self.diff_container.remove(child)
            child = next_child

        if diff_text:
            # Create diff view with raw diff from git
            diff_view = DiffView("", "", file_path=file_path, raw_diff=diff_text)
            self.diff_container.append(diff_view)
        else:
            # No diff available
            placeholder = Gtk.Label(label="No diff available")
            placeholder.add_css_class("dim-label")
            placeholder.set_vexpand(True)
            placeholder.set_valign(Gtk.Align.CENTER)
            self.diff_container.append(placeholder)

    def _on_copy_sha(self, button):
        """Copy full SHA to clipboard."""
        clipboard = Gdk.Display.get_default().get_clipboard()
        clipboard.set(self.commit.hash)

        from ..services import ToastService
        ToastService.show(f"Copied: {self.commit.short_hash}")

    def update_commit(self, commit: GitCommit):
        """Update view to show different commit."""
        self.commit = commit

        # Update header labels
        self.sha_label.set_label(commit.short_hash)
        self.author_label.set_label(commit.author)
        self.date_label.set_label(commit.timestamp.strftime('%Y-%m-%d %H:%M'))

        # Update message
        self._update_message_content()

        # Clear files list
        self.files_list.remove_all()

        # Clear diff
        child = self.diff_container.get_first_child()
        while child:
            next_child = child.get_next_sibling()
            self.diff_container.remove(child)
            child = next_child

        # Add placeholder
        placeholder = Gtk.Label(label="Select a file to view diff")
        placeholder.add_css_class("dim-label")
        placeholder.set_vexpand(True)
        placeholder.set_valign(Gtk.Align.CENTER)
        self.diff_container.append(placeholder)

        # Reload files
        self._load_files()
