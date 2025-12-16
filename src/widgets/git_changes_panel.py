"""Git changes panel widget."""

from pathlib import Path

from gi.repository import Gtk, GLib, GObject, Adw

from ..services import GitService, GitFileStatus, FileStatus, ToastService, FileMonitorService, AuthenticationRequired
from ..services.icon_cache import IconCache
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

    # Polling interval for git status (ms) - fast operation ~2ms
    POLL_INTERVAL = 2000

    def __init__(self, project_path: str, file_monitor_service: FileMonitorService):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)

        self.project_path = Path(project_path)
        self.service = GitService(self.project_path)
        self._file_monitor_service = file_monitor_service
        self._icon_cache = IconCache()
        # Track expanded directories per section (staged/unstaged)
        self._expanded_staged: set[str] = set()
        self._expanded_unstaged: set[str] = set()

        # Polling state
        self._poll_timeout_id: int | None = None
        self._last_status_hash: str | None = None

        # Check if git repo
        if not self.service.is_git_repo():
            self._build_no_repo_ui()
            return

        self.service.open()
        self._build_ui()
        self._setup_css()
        self._connect_monitor_signals()
        self._start_polling()
        self.refresh()

        # Stop polling on destroy
        self.connect("destroy", self._on_destroy)

    def _connect_monitor_signals(self):
        """Connect to FileMonitorService signals."""
        self._file_monitor_service.connect("git-status-changed", self._on_git_status_changed)
        self._file_monitor_service.connect("working-tree-changed", self._on_working_tree_changed)

    def _on_git_status_changed(self, service):
        """Handle git status changes from monitor service."""
        self.refresh()

    def _on_working_tree_changed(self, service, path: str):
        """Handle working tree changes from monitor service."""
        self.refresh()

    def _start_polling(self):
        """Start polling git status for changes."""
        if self._poll_timeout_id is None:
            self._poll_timeout_id = GLib.timeout_add(
                self.POLL_INTERVAL, self._poll_git_status
            )

    def _stop_polling(self):
        """Stop polling git status."""
        if self._poll_timeout_id is not None:
            GLib.source_remove(self._poll_timeout_id)
            self._poll_timeout_id = None

    def _poll_git_status(self) -> bool:
        """Poll git status and refresh if changed."""
        try:
            staged = self.service.get_staged_files()
            unstaged = self.service.get_unstaged_files()
            all_files = staged + unstaged
            # Create a simple hash of current status
            status_hash = str(sorted((f.path, f.status.name, f.staged) for f in all_files)) if all_files else ""

            if status_hash != self._last_status_hash:
                self._last_status_hash = status_hash
                self.refresh()
        except Exception:
            pass  # Ignore errors during polling

        return True  # Keep polling

    def _on_destroy(self, widget):
        """Handle widget destruction."""
        self._stop_polling()

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
        self.branch_label.set_ellipsize(2)  # PANGO_ELLIPSIZE_MIDDLE
        self.branch_label.set_max_width_chars(30)
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

        # Buttons row - use hexpand instead of homogeneous for flexible sizing
        buttons_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)

        self.commit_btn = Gtk.Button(label="Commit")
        self.commit_btn.add_css_class("suggested-action")
        self.commit_btn.set_sensitive(False)
        self.commit_btn.set_hexpand(True)
        self.commit_btn.connect("clicked", self._on_commit_clicked)
        buttons_box.append(self.commit_btn)

        self.push_btn = Gtk.Button(label="Push")
        self.push_btn.set_hexpand(True)
        self.push_btn.connect("clicked", self._on_push_clicked)
        buttons_box.append(self.push_btn)

        self.pull_btn = Gtk.Button(label="Pull")
        self.pull_btn.set_hexpand(True)
        self.pull_btn.connect("clicked", self._on_pull_clicked)
        buttons_box.append(self.pull_btn)

        actions_box.append(buttons_box)
        self.append(actions_box)

    def _setup_css(self):
        """Set up CSS for git status colors."""
        css = b"""
        .git-modified { color: #f1c40f; }
        .git-added { color: #2ecc71; }
        .git-deleted { color: #e74c3c; }
        .git-renamed { color: #3498db; }
        .git-file-label { font-weight: normal; }
        .git-dir-label { font-weight: normal; }
        """
        provider = Gtk.CssProvider()
        provider.load_from_data(css)
        Gtk.StyleContext.add_provider_for_display(
            self.get_display(),
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

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
            self._last_status_hash = ""
            return

        # Staged section
        if staged:
            self._add_section("Staged", staged, is_staged=True)

        # Unstaged section
        if unstaged:
            self._add_section("Changes", unstaged, is_staged=False)

        self._update_commit_button()

        # Update status hash to avoid duplicate polling refreshes
        all_files = staged + unstaged
        self._last_status_hash = str(sorted((f.path, f.status.name, f.staged) for f in all_files))

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

    def _group_files_by_directory(self, files: list[GitFileStatus]) -> dict[str, list[GitFileStatus]]:
        """Group files by their parent directory.

        Returns:
            Dict mapping directory path to list of files.
            Empty string key "" for root-level files.
        """
        groups: dict[str, list[GitFileStatus]] = {}
        for file_status in files:
            path = Path(file_status.path)
            parent = str(path.parent) if path.parent != Path(".") else ""
            if parent not in groups:
                groups[parent] = []
            groups[parent].append(file_status)

        # Sort: root first, then alphabetically
        return dict(sorted(groups.items(), key=lambda x: (x[0] != "", x[0])))

    def _add_section(self, title: str, files: list[GitFileStatus], is_staged: bool):
        """Add a section (Staged or Changes) to the panel with tree structure."""
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

        # Group files by directory
        groups = self._group_files_by_directory(files)
        expanded_set = self._expanded_staged if is_staged else self._expanded_unstaged

        for dir_path, dir_files in groups.items():
            if dir_path:  # Non-root directory
                # Directory header row
                is_expanded = dir_path not in expanded_set  # Default expanded (not in collapsed set)
                dir_row = self._create_directory_row(dir_path, len(dir_files), is_expanded, is_staged)
                self.content_box.append(dir_row)

                # Files under this directory (only if expanded)
                if is_expanded:
                    for file_status in dir_files:
                        row = self._create_file_row(file_status, indent=True)
                        self.content_box.append(row)
            else:
                # Root-level files (no directory header)
                for file_status in dir_files:
                    row = self._create_file_row(file_status, indent=False)
                    self.content_box.append(row)

    def _create_directory_row(self, dir_path: str, file_count: int, is_expanded: bool, is_staged: bool) -> Gtk.Box:
        """Create a collapsible directory header row."""
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        box.set_margin_start(4)
        box.set_margin_top(4)
        box.set_margin_bottom(2)

        # Clickable area for expand/collapse
        click_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        click_box.set_hexpand(True)

        # Expand/collapse arrow
        arrow = Gtk.Image.new_from_icon_name(
            "pan-down-symbolic" if is_expanded else "pan-end-symbolic"
        )
        arrow.set_pixel_size(12)
        arrow.add_css_class("dim-label")
        click_box.append(arrow)

        # Folder icon
        folder_path = self.project_path / dir_path
        gicon = self._icon_cache.get_folder_gicon(folder_path, is_open=is_expanded)
        if gicon:
            folder_icon = Gtk.Image.new_from_gicon(gicon)
            folder_icon.set_pixel_size(16)
            click_box.append(folder_icon)

        # Directory name with file count
        label = Gtk.Label(label=f"{dir_path}/ ({file_count})")
        label.set_xalign(0)
        label.add_css_class("dim-label")
        label.add_css_class("git-dir-label")
        click_box.append(label)

        # Make clickable via button
        dir_btn = Gtk.Button()
        dir_btn.set_child(click_box)
        dir_btn.add_css_class("flat")
        dir_btn.set_hexpand(True)
        dir_btn.connect("clicked", self._on_directory_clicked, dir_path, is_staged)
        box.append(dir_btn)

        return box

    def _on_directory_clicked(self, button, dir_path: str, is_staged: bool):
        """Toggle directory expand/collapse."""
        expanded_set = self._expanded_staged if is_staged else self._expanded_unstaged

        # Toggle: if in set (collapsed), remove (expand). If not in set (expanded), add (collapse).
        if dir_path in expanded_set:
            expanded_set.discard(dir_path)
        else:
            expanded_set.add(dir_path)

        self.refresh()

    def _create_file_row(self, file_status: GitFileStatus, indent: bool = False) -> Gtk.Box:
        """Create a row for a file."""
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        box.set_margin_start(24 if indent else 4)
        box.set_margin_top(2)
        box.set_margin_bottom(2)

        # Status indicator
        status_label = Gtk.Label(label=file_status.status.value)
        status_label.set_width_chars(2)
        css_class = STATUS_CSS_CLASSES.get(file_status.status, "")
        if css_class:
            status_label.add_css_class(css_class)
        box.append(status_label)

        # File icon
        file_path = self.project_path / file_status.path
        gicon = self._icon_cache.get_file_gicon(file_path)
        if gicon:
            file_icon = Gtk.Image.new_from_gicon(gicon)
            file_icon.set_pixel_size(16)
            box.append(file_icon)

        # File name (just the filename, not full path)
        file_btn = Gtk.Button()
        file_btn.add_css_class("flat")
        file_btn.set_hexpand(True)

        display_name = Path(file_status.path).name
        file_label = Gtk.Label(label=display_name)
        file_label.set_xalign(0)
        file_label.set_ellipsize(2)  # PANGO_ELLIPSIZE_MIDDLE
        file_label.set_tooltip_text(file_status.path)  # Full path in tooltip
        file_label.add_css_class("git-file-label")
        if css_class:
            file_label.add_css_class(css_class)
        file_btn.set_child(file_label)
        file_btn.connect("clicked", self._on_file_clicked, file_status)
        box.append(file_btn)

        # Restore button (only for deleted/modified unstaged files)
        if not file_status.staged and file_status.status in (FileStatus.DELETED, FileStatus.MODIFIED):
            restore_btn = Gtk.Button()
            restore_btn.set_icon_name("edit-undo-symbolic")
            restore_btn.add_css_class("flat")
            restore_btn.add_css_class("circular")
            restore_btn.set_tooltip_text("Restore from HEAD")
            restore_btn.connect("clicked", self._on_restore_clicked, file_status)
            box.append(restore_btn)

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

    def _on_restore_clicked(self, button, file_status: GitFileStatus):
        """Handle restore button click - restore file from HEAD."""
        # Show confirmation dialog for destructive action
        dialog = Adw.AlertDialog()
        dialog.set_heading("Restore File?")
        dialog.set_body(
            f"This will discard changes to:\n{file_status.path}\n\n"
            "This action cannot be undone."
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("restore", "Restore")
        dialog.set_response_appearance("restore", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", self._on_restore_response, file_status)
        dialog.present(self.get_root())

    def _on_restore_response(self, dialog, response: str, file_status: GitFileStatus):
        """Handle restore confirmation dialog response."""
        if response == "restore":
            try:
                self.service.restore_file(file_status.path)
                self._show_toast(f"Restored: {file_status.path}")
                self.refresh()
            except Exception as e:
                self._show_error(f"Failed to restore: {e}")

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

    def _do_pull(self, credentials: tuple[str, str] | None = None):
        """Execute pull operation."""
        try:
            result = self.service.pull(credentials)
            self._show_toast(result)
            self.refresh()
        except AuthenticationRequired as e:
            self._show_credentials_dialog("Pull", e.remote_url, self._do_pull)
        except Exception as e:
            self._show_error_dialog("Pull Failed", str(e))

    def _on_push_clicked(self, button):
        """Handle push button click."""
        self._do_push()

    def _do_push(self, credentials: tuple[str, str] | None = None):
        """Execute push operation."""
        try:
            result = self.service.push(credentials)
            self._show_toast(result)
            self.refresh()  # Update counts
        except AuthenticationRequired as e:
            self._show_credentials_dialog("Push", e.remote_url, self._do_push)
        except Exception as e:
            self._show_error_dialog("Push Failed", str(e))

    def _show_credentials_dialog(self, operation: str, remote_url: str, retry_callback):
        """Show dialog to get git credentials."""
        dialog = Adw.AlertDialog()
        dialog.set_heading(f"Authentication Required")
        dialog.set_body(f"Enter credentials for {remote_url}")

        # Create form
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_margin_start(12)
        box.set_margin_end(12)

        # Username entry
        username_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        username_label = Gtk.Label(label="Username:")
        username_label.set_xalign(0)
        username_label.set_size_request(80, -1)
        username_box.append(username_label)

        username_entry = Gtk.Entry()
        username_entry.set_hexpand(True)
        username_box.append(username_entry)
        box.append(username_box)

        # Password entry
        password_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        password_label = Gtk.Label(label="Password:")
        password_label.set_xalign(0)
        password_label.set_size_request(80, -1)
        password_box.append(password_label)

        password_entry = Gtk.PasswordEntry()
        password_entry.set_hexpand(True)
        password_entry.set_show_peek_icon(True)
        password_box.append(password_entry)
        box.append(password_box)

        # Hint about tokens
        hint = Gtk.Label(label="Tip: For GitHub, use a Personal Access Token as password")
        hint.add_css_class("dim-label")
        hint.add_css_class("caption")
        hint.set_wrap(True)
        hint.set_xalign(0)
        box.append(hint)

        dialog.set_extra_child(box)

        dialog.add_response("cancel", "Cancel")
        dialog.add_response("authenticate", operation)
        dialog.set_response_appearance("authenticate", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("authenticate")
        dialog.set_close_response("cancel")

        def on_response(d, response):
            if response == "authenticate":
                username = username_entry.get_text().strip()
                password = password_entry.get_text()
                if username and password:
                    retry_callback((username, password))
                else:
                    self._show_error("Username and password are required")

        dialog.connect("response", on_response)
        dialog.present(self.get_root())

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
