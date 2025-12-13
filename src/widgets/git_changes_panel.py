"""Git changes panel widget."""

from pathlib import Path

from gi.repository import Gtk, Gio, GLib, GObject, Adw

from ..services import GitService, GitFileStatus, FileStatus, ToastService
from .branch_popover import BranchPopover


# CSS classes for git status colors
STATUS_CSS_CLASSES = {
    FileStatus.MODIFIED: "git-modified",
    FileStatus.ADDED: "git-added",
    FileStatus.DELETED: "git-deleted",
    FileStatus.RENAMED: "git-renamed",
    FileStatus.UNTRACKED: "git-added",  # Same as added (green)
    FileStatus.TYPECHANGE: "git-modified",
}


class GitChangesPanel(Gtk.Box):
    """Panel displaying git changes with stage/unstage/commit functionality."""

    __gsignals__ = {
        "file-clicked": (GObject.SignalFlags.RUN_FIRST, None, (str, bool)),  # path, staged
        "branch-changed": (GObject.SignalFlags.RUN_FIRST, None, ()),  # emitted when branch switches
    }

    def __init__(self, project_path: str):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)

        self.project_path = Path(project_path)
        self.service = GitService(self.project_path)
        self._file_monitor = None

        # Check if git repo
        if not self.service.is_git_repo():
            self._build_no_repo_ui()
            return

        self.service.open()
        self._build_ui()
        self._setup_css()
        self._setup_file_monitor()
        self.refresh()

    def _build_no_repo_ui(self):
        """Build UI for non-git directories."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_margin_start(12)
        box.set_margin_end(12)
        box.set_margin_top(24)
        box.set_margin_bottom(24)
        box.set_valign(Gtk.Align.CENTER)

        icon = Gtk.Image.new_from_icon_name("dialog-information-symbolic")
        icon.set_pixel_size(48)
        icon.add_css_class("dim-label")
        box.append(icon)

        label = Gtk.Label(label="Not a git repository")
        label.add_css_class("dim-label")
        box.append(label)

        self.append(box)

    def _build_ui(self):
        """Build the panel UI."""
        # Header with branch name
        header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        header_box.set_margin_start(12)
        header_box.set_margin_end(12)
        header_box.set_margin_top(12)
        header_box.set_margin_bottom(6)

        # Branch button with popover
        self.branch_btn = Gtk.MenuButton()
        self.branch_btn.add_css_class("flat")
        self.branch_btn.set_hexpand(True)

        # Branch button content
        branch_content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        branch_icon = Gtk.Image.new_from_icon_name("git-branch-symbolic")
        branch_content.append(branch_icon)

        self.branch_label = Gtk.Label()
        self.branch_label.set_xalign(0)
        self.branch_label.add_css_class("heading")
        branch_content.append(self.branch_label)

        dropdown_icon = Gtk.Image.new_from_icon_name("pan-down-symbolic")
        dropdown_icon.add_css_class("dim-label")
        branch_content.append(dropdown_icon)

        self.branch_btn.set_child(branch_content)

        # Branch popover
        self.branch_popover = BranchPopover(self.service)
        self.branch_popover.connect("branch-switched", self._on_branch_switched)
        self.branch_btn.set_popover(self.branch_popover)

        header_box.append(self.branch_btn)

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

        self.content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.content_box.set_margin_start(12)
        self.content_box.set_margin_end(12)
        self.content_box.set_margin_bottom(12)
        scrolled.set_child(self.content_box)

        self.append(scrolled)

        # Bottom actions
        actions_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        actions_box.set_margin_start(12)
        actions_box.set_margin_end(12)
        actions_box.set_margin_bottom(12)

        # Commit message entry
        self.commit_entry = Gtk.Entry()
        self.commit_entry.set_placeholder_text("Commit message...")
        self.commit_entry.connect("changed", self._on_commit_entry_changed)
        self.commit_entry.connect("activate", self._on_commit_clicked)
        actions_box.append(self.commit_entry)

        # Buttons row
        buttons_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        buttons_box.set_homogeneous(True)

        self.commit_btn = Gtk.Button(label="Commit")
        self.commit_btn.add_css_class("suggested-action")
        self.commit_btn.set_sensitive(False)
        self.commit_btn.connect("clicked", self._on_commit_clicked)
        buttons_box.append(self.commit_btn)

        self.pull_btn = Gtk.Button(label="Pull")
        self.pull_btn.connect("clicked", self._on_pull_clicked)
        buttons_box.append(self.pull_btn)

        self.push_btn = Gtk.Button(label="Push")
        self.push_btn.connect("clicked", self._on_push_clicked)
        buttons_box.append(self.push_btn)

        actions_box.append(buttons_box)
        self.append(actions_box)

    def _setup_css(self):
        """Set up CSS for git status colors."""
        css = b"""
        .git-modified { color: #f1c40f; }
        .git-added { color: #2ecc71; }
        .git-deleted { color: #e74c3c; }
        .git-renamed { color: #3498db; }
        """
        provider = Gtk.CssProvider()
        provider.load_from_data(css)
        Gtk.StyleContext.add_provider_for_display(
            self.get_display(),
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

    def _setup_file_monitor(self):
        """Set up file monitors for .git directory and working tree."""
        self._monitors = []
        self._refresh_pending = False

        # Monitor .git directory for index changes
        git_dir = self.project_path / ".git"
        if git_dir.exists():
            try:
                gfile = Gio.File.new_for_path(str(git_dir))
                monitor = gfile.monitor_directory(Gio.FileMonitorFlags.NONE, None)
                monitor.connect("changed", self._on_file_changed)
                self._monitors.append(monitor)
            except GLib.Error:
                pass

        # Monitor project root for working tree changes
        try:
            gfile = Gio.File.new_for_path(str(self.project_path))
            monitor = gfile.monitor_directory(Gio.FileMonitorFlags.WATCH_MOVES, None)
            monitor.connect("changed", self._on_file_changed)
            self._monitors.append(monitor)
        except GLib.Error:
            pass

    def _on_file_changed(self, monitor, file, other_file, event_type):
        """Handle file changes - debounced refresh."""
        if event_type not in (
            Gio.FileMonitorEvent.CHANGED,
            Gio.FileMonitorEvent.CREATED,
            Gio.FileMonitorEvent.DELETED,
            Gio.FileMonitorEvent.MOVED_IN,
            Gio.FileMonitorEvent.MOVED_OUT,
        ):
            return

        # Skip .git internal files for working tree monitor
        if file:
            path = file.get_path()
            if path and "/.git/" in path:
                return

        # Debounce - schedule refresh if not already pending
        if not self._refresh_pending:
            self._refresh_pending = True
            GLib.timeout_add(300, self._delayed_refresh)

    def _delayed_refresh(self):
        """Delayed refresh to coalesce rapid changes."""
        self._refresh_pending = False
        self.refresh()
        return False

    def refresh(self):
        """Refresh the changes list."""
        if not hasattr(self, "branch_label"):
            return  # Not a git repo

        # Update branch name
        branch = self.service.get_branch_name()
        self.branch_label.set_label(branch)

        # Update Push/Pull buttons with ahead/behind counts
        self._update_sync_buttons()

        # Clear content - collect children first to avoid modification during iteration
        children = []
        child = self.content_box.get_first_child()
        while child:
            children.append(child)
            child = child.get_next_sibling()
        for child in children:
            self.content_box.remove(child)

        # Get status
        staged = self.service.get_staged_files()
        unstaged = self.service.get_unstaged_files()

        if not staged and not unstaged:
            # No changes
            label = Gtk.Label(label="No changes")
            label.add_css_class("dim-label")
            label.set_margin_top(24)
            self.content_box.append(label)
            self._update_commit_button()
            return

        # Staged section
        if staged:
            self._add_section("Staged", staged, is_staged=True)

        # Unstaged section
        if unstaged:
            self._add_section("Changes", unstaged, is_staged=False)

        self._update_commit_button()

    def _update_sync_buttons(self):
        """Update Push/Pull button labels with ahead/behind counts."""
        ahead, behind = self.service.get_ahead_behind()

        # Update Push button
        if ahead > 0:
            self.push_btn.set_label(f"Push ({ahead})")
            self.push_btn.add_css_class("suggested-action")
        else:
            self.push_btn.set_label("Push")
            self.push_btn.remove_css_class("suggested-action")

        # Update Pull button
        if behind > 0:
            self.pull_btn.set_label(f"Pull ({behind})")
            self.pull_btn.add_css_class("suggested-action")
        else:
            self.pull_btn.set_label("Pull")
            self.pull_btn.remove_css_class("suggested-action")

    def _add_section(self, title: str, files: list[GitFileStatus], is_staged: bool):
        """Add a section (Staged or Changes) to the panel."""
        # Section header
        header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        header_box.set_margin_top(6)

        label = Gtk.Label(label=f"{title} ({len(files)})")
        label.set_xalign(0)
        label.set_hexpand(True)
        label.add_css_class("dim-label")
        header_box.append(label)

        # Stage/Unstage all button
        all_btn = Gtk.Button()
        all_btn.set_icon_name("list-remove-symbolic" if is_staged else "list-add-symbolic")
        all_btn.add_css_class("flat")
        all_btn.add_css_class("circular")
        all_btn.set_tooltip_text("Unstage all" if is_staged else "Stage all")
        all_btn.connect("clicked", self._on_stage_all_clicked, is_staged)
        header_box.append(all_btn)

        self.content_box.append(header_box)

        # Files list
        for file_status in files:
            row = self._create_file_row(file_status)
            self.content_box.append(row)

    def _create_file_row(self, file_status: GitFileStatus) -> Gtk.Box:
        """Create a row for a file."""
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        box.set_margin_start(4)
        box.set_margin_top(2)
        box.set_margin_bottom(2)

        # Status indicator
        status_label = Gtk.Label(label=file_status.status.value)
        status_label.set_width_chars(2)
        css_class = STATUS_CSS_CLASSES.get(file_status.status, "")
        if css_class:
            status_label.add_css_class(css_class)
        box.append(status_label)

        # File name (clickable)
        file_btn = Gtk.Button()
        file_btn.add_css_class("flat")
        file_btn.set_hexpand(True)

        file_label = Gtk.Label(label=file_status.path)
        file_label.set_xalign(0)
        file_label.set_ellipsize(2)  # PANGO_ELLIPSIZE_MIDDLE
        if css_class:
            file_label.add_css_class(css_class)
        file_btn.set_child(file_label)
        file_btn.connect("clicked", self._on_file_clicked, file_status)
        box.append(file_btn)

        # Stage/Unstage button
        action_btn = Gtk.Button()
        action_btn.set_icon_name("list-remove-symbolic" if file_status.staged else "list-add-symbolic")
        action_btn.add_css_class("flat")
        action_btn.add_css_class("circular")
        action_btn.set_tooltip_text("Unstage" if file_status.staged else "Stage")
        action_btn.connect("clicked", self._on_stage_clicked, file_status)
        box.append(action_btn)

        return box

    def _on_file_clicked(self, button, file_status: GitFileStatus):
        """Handle file click - emit signal to show diff."""
        self.emit("file-clicked", file_status.path, file_status.staged)

    def _on_stage_clicked(self, button, file_status: GitFileStatus):
        """Handle stage/unstage button click."""
        try:
            if file_status.staged:
                self.service.unstage(file_status.path)
            else:
                self.service.stage(file_status.path)
            self.refresh()
        except Exception as e:
            self._show_error(f"Failed to {'unstage' if file_status.staged else 'stage'}: {e}")

    def _on_stage_all_clicked(self, button, is_staged: bool):
        """Handle stage/unstage all button."""
        try:
            if is_staged:
                self.service.unstage_all()
            else:
                self.service.stage_all()
            self.refresh()
        except Exception as e:
            self._show_error(f"Failed to {'unstage' if is_staged else 'stage'} all: {e}")

    def _on_commit_entry_changed(self, entry):
        """Handle commit message entry change."""
        self._update_commit_button()

    def _update_commit_button(self):
        """Update commit button sensitivity."""
        has_staged = bool(self.service.get_staged_files())
        has_message = bool(self.commit_entry.get_text().strip())
        self.commit_btn.set_sensitive(has_staged and has_message)

    def _on_commit_clicked(self, *args):
        """Handle commit button click."""
        message = self.commit_entry.get_text().strip()
        if not message:
            return

        try:
            commit_hash = self.service.commit(message)
            self.commit_entry.set_text("")
            self._show_toast(f"Committed: {commit_hash}")
            self.refresh()
        except Exception as e:
            self._show_error(f"Commit failed: {e}")

    def _on_pull_clicked(self, button):
        """Handle pull button click."""
        # Check for uncommitted changes first
        if self.service.has_uncommitted_changes():
            self._show_uncommitted_warning()
            return

        self._do_pull()

    def _show_uncommitted_warning(self):
        """Show warning about uncommitted changes before pull."""
        dialog = Adw.AlertDialog()
        dialog.set_heading("Uncommitted Changes")
        dialog.set_body(
            "You have uncommitted changes. "
            "Please commit or stash them before pulling."
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("pull", "Pull Anyway")
        dialog.set_response_appearance("pull", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", self._on_uncommitted_warning_response)
        dialog.present(self.get_root())

    def _on_uncommitted_warning_response(self, dialog, response: str):
        """Handle uncommitted warning dialog response."""
        if response == "pull":
            self._do_pull()

    def _do_pull(self):
        """Execute pull operation."""
        try:
            result = self.service.pull()
            self._show_toast(result)
            self.refresh()
        except Exception as e:
            self._show_error_dialog("Pull Failed", str(e))

    def _on_push_clicked(self, button):
        """Handle push button click."""
        try:
            result = self.service.push()
            self._show_toast(result)
            self.refresh()  # Update counts
        except Exception as e:
            self._show_error_dialog("Push Failed", str(e))

    def _on_branch_switched(self, popover):
        """Handle branch switch - refresh and notify."""
        self.refresh()
        self.emit("branch-changed")

    def _show_toast(self, message: str):
        """Show a toast notification."""
        ToastService.show(message)

    def _show_error(self, message: str):
        """Show error toast."""
        ToastService.show_error(message)

    def _show_error_dialog(self, title: str, message: str):
        """Show error dialog with copy button."""
        dialog = Adw.AlertDialog()
        dialog.set_heading(title)

        # Create scrollable text view for error message
        text_view = Gtk.TextView()
        text_view.set_editable(False)
        text_view.set_cursor_visible(False)
        text_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        text_view.set_monospace(True)
        text_view.get_buffer().set_text(message)
        text_view.set_margin_top(8)
        text_view.set_margin_bottom(8)
        text_view.set_margin_start(8)
        text_view.set_margin_end(8)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_min_content_height(100)
        scrolled.set_max_content_height(300)
        scrolled.set_min_content_width(400)
        scrolled.set_child(text_view)

        frame = Gtk.Frame()
        frame.set_child(scrolled)

        dialog.set_extra_child(frame)

        dialog.add_response("copy", "Copy")
        dialog.add_response("close", "Close")
        dialog.set_default_response("close")
        dialog.set_close_response("close")

        dialog.connect("response", self._on_error_dialog_response, message)

        # Find parent window
        parent = self.get_root()
        dialog.present(parent)

    def _on_error_dialog_response(self, dialog, response: str, message: str):
        """Handle error dialog response."""
        if response == "copy":
            clipboard = self.get_clipboard()
            clipboard.set(message)
            ToastService.show("Error copied to clipboard")
