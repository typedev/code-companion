"""Git changes panel widget."""

from pathlib import Path

from gi.repository import Gtk, Gio, GLib, GObject, Adw

from ..services import GitService, GitFileStatus, FileStatus


# CSS classes for git status colors
STATUS_CSS_CLASSES = {
    FileStatus.MODIFIED: "git-modified",
    FileStatus.ADDED: "git-added",
    FileStatus.DELETED: "git-deleted",
    FileStatus.RENAMED: "git-renamed",
    FileStatus.UNTRACKED: "git-added",  # Same as added (green)
    FileStatus.TYPECHANGE: "git-modified",
}


class GitPanel(Gtk.Box):
    """Panel displaying git changes with stage/unstage/commit functionality."""

    __gsignals__ = {
        "file-clicked": (GObject.SignalFlags.RUN_FIRST, None, (str, bool)),  # path, staged
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

        # Branch indicator
        branch_icon = Gtk.Image.new_from_icon_name("git-branch-symbolic")
        branch_icon.add_css_class("dim-label")
        header_box.append(branch_icon)

        self.branch_label = Gtk.Label()
        self.branch_label.set_xalign(0)
        self.branch_label.set_hexpand(True)
        self.branch_label.set_margin_start(6)
        self.branch_label.add_css_class("heading")
        header_box.append(self.branch_label)

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
        """Set up file monitor for .git directory."""
        git_dir = self.project_path / ".git"
        if not git_dir.exists():
            return

        try:
            gfile = Gio.File.new_for_path(str(git_dir))
            self._file_monitor = gfile.monitor_directory(
                Gio.FileMonitorFlags.NONE,
                None
            )
            self._file_monitor.connect("changed", self._on_git_changed)
        except GLib.Error:
            pass

    def _on_git_changed(self, monitor, file, other_file, event_type):
        """Handle changes in .git directory."""
        if event_type in (
            Gio.FileMonitorEvent.CHANGED,
            Gio.FileMonitorEvent.CREATED,
            Gio.FileMonitorEvent.DELETED,
        ):
            # Debounce refresh
            GLib.timeout_add(200, self._delayed_refresh)

    def _delayed_refresh(self):
        """Delayed refresh to coalesce rapid changes."""
        self.refresh()
        return False

    def refresh(self):
        """Refresh the changes list."""
        if not hasattr(self, "branch_label"):
            return  # Not a git repo

        # Update branch name
        branch = self.service.get_branch_name()
        self.branch_label.set_label(branch)

        # Clear content
        while child := self.content_box.get_first_child():
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
        try:
            result = self.service.pull()
            self._show_toast(result)
            self.refresh()
        except Exception as e:
            self._show_error(f"Pull failed: {e}")

    def _on_push_clicked(self, button):
        """Handle push button click."""
        try:
            result = self.service.push()
            self._show_toast(result)
        except Exception as e:
            self._show_error(f"Push failed: {e}")

    def _show_toast(self, message: str):
        """Show a toast notification."""
        window = self.get_root()
        if isinstance(window, Adw.ApplicationWindow):
            toast = Adw.Toast.new(message)
            toast.set_timeout(3)
            # Find toast overlay - need to add one to the window
            self._ensure_toast_overlay(window, toast)

    def _show_error(self, message: str):
        """Show error in a dialog."""
        dialog = Adw.AlertDialog()
        dialog.set_heading("Error")
        dialog.set_body(message)
        dialog.add_response("ok", "OK")
        dialog.present(self.get_root())

    def _ensure_toast_overlay(self, window, toast):
        """Ensure window has toast overlay and show toast."""
        # For now just print - toast overlay needs to be in window
        print(f"Toast: {toast.get_title()}")
        # TODO: Add proper toast overlay to ProjectWindow
