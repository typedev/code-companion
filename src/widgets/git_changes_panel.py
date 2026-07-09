"""Git changes panel widget."""

import subprocess
from pathlib import Path

from gi.repository import Gtk, GLib, GObject, Adw, Gio

from ..services import GitService, GitFileStatus, FileStatus, ToastService, FileMonitorService, AuthenticationRequired, PushRejected, run_async
from ..services.icon_cache import IconCache
from ..utils import git_auth
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

    # Slow fallback poll (ms). FileMonitorService signals drive most refreshes now;
    # this only catches anything the monitors miss (roadmap 2.4).
    POLL_INTERVAL = 30000
    # Debounce window for coalescing bursty refresh triggers (ms).
    REFRESH_DEBOUNCE = 200
    # Cap on interactive auth retries before giving up (roadmap 2.2).
    MAX_AUTH_ATTEMPTS = 3

    def __init__(self, project_path: str, file_monitor_service: FileMonitorService):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)

        self.project_path = Path(project_path)
        self.service = GitService(self.project_path)
        self._file_monitor_service = file_monitor_service
        self._icon_cache = IconCache()
        # Track expanded directories per section (staged/unstaged)
        self._expanded_staged: set[str] = set()
        self._expanded_unstaged: set[str] = set()

        # Polling / refresh state
        self._poll_timeout_id: int | None = None
        self._refresh_timeout_id: int = 0
        self._last_status_hash: str | None = None

        # In-flight guard: serializes mutating git ops (2.2/2.3) so a double-click
        # can't produce two commits and slow network ops don't stack.
        self._busy = False
        self._has_staged = False  # cached from the last refresh; gates the Commit button
        self._auth_attempts = 0
        self._remember_creds = False  # opt-in credential persistence (roadmap 3.7)

        # Check if git repo
        if not self.service.is_git_repo():
            self._build_no_repo_ui()
            # Watch for an in-app `git init`/clone so the panel can activate
            # later without the window being reopened (roadmap 3.10).
            self._no_repo_handler_id = self._file_monitor_service.connect(
                "git-status-changed", self._on_repo_maybe_created
            )
            return

        self._activate_repo()

    def _activate_repo(self):
        """Build the live changes UI and start monitoring/polling."""
        self.service.open()
        self._build_ui()
        self._setup_css()
        self._connect_monitor_signals()
        self._start_polling()
        self.refresh()

        # Stop polling on destroy
        self.connect("destroy", self._on_destroy)

    def _on_repo_maybe_created(self, service):
        """Activate the panel once a repo appears under a previously non-git dir."""
        if hasattr(self, "branch_label"):
            return  # already activated
        if not self.service.is_git_repo():
            return
        # Drop the temporary watcher and the placeholder, then become live.
        self._file_monitor_service.disconnect(self._no_repo_handler_id)
        if getattr(self, "_no_repo_box", None) is not None:
            self.remove(self._no_repo_box)
            self._no_repo_box = None
        self._activate_repo()

    def _connect_monitor_signals(self):
        """Connect to FileMonitorService signals."""
        self._file_monitor_service.connect("git-status-changed", self._on_git_status_changed)
        self._file_monitor_service.connect("working-tree-changed", self._on_working_tree_changed)

    def _on_git_status_changed(self, service):
        """Handle git status changes from monitor service."""
        self._schedule_refresh()

    def _on_working_tree_changed(self, service, path: str):
        """Handle working tree changes from monitor service."""
        self._schedule_refresh()

    def _schedule_refresh(self):
        """Coalesce bursty refresh triggers (monitor signals, post-actions) into one.

        Cancels any pending refresh and reschedules; combined with the generation
        token in refresh(), N rapid triggers produce a single visible refresh
        (roadmap 2.4).
        """
        if self._refresh_timeout_id:
            GLib.source_remove(self._refresh_timeout_id)
        self._refresh_timeout_id = GLib.timeout_add(self.REFRESH_DEBOUNCE, self._do_scheduled_refresh)

    def _do_scheduled_refresh(self) -> bool:
        self._refresh_timeout_id = 0
        self.refresh()
        return False

    def _set_busy(self, busy: bool):
        """Enable/disable persistent mutating buttons while a git op is in flight."""
        self._busy = busy
        if hasattr(self, "push_btn"):
            self.push_btn.set_sensitive(not busy)
        if hasattr(self, "pull_btn"):
            self.pull_btn.set_sensitive(not busy)
        if busy:
            if hasattr(self, "commit_btn"):
                self.commit_btn.set_sensitive(False)
        else:
            self._update_commit_button()

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
        """Poll git status via subprocess (avoids pygit2 GIL blocking)."""
        self.refresh()
        return True  # Keep polling

    def _on_destroy(self, widget):
        """Handle widget destruction."""
        self._stop_polling()
        if self._refresh_timeout_id:
            GLib.source_remove(self._refresh_timeout_id)
            self._refresh_timeout_id = 0

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
        self._no_repo_box = box

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

        # Commit message: multi-line text view (Ctrl+Enter commits)
        commit_scroller = Gtk.ScrolledWindow()
        commit_scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        commit_scroller.set_min_content_height(52)
        commit_scroller.set_max_content_height(120)
        commit_scroller.add_css_class("card")

        self.commit_view = Gtk.TextView()
        self.commit_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.commit_view.set_top_margin(4)
        self.commit_view.set_bottom_margin(4)
        self.commit_view.set_left_margin(6)
        self.commit_view.set_right_margin(6)
        self.commit_buffer = self.commit_view.get_buffer()
        self.commit_buffer.connect("changed", self._on_commit_entry_changed)
        commit_scroller.set_child(self.commit_view)

        # Placeholder overlay (Gtk.TextView has no native placeholder text).
        commit_overlay = Gtk.Overlay()
        commit_overlay.set_child(commit_scroller)
        self._commit_placeholder = Gtk.Label(label="Commit message…")
        self._commit_placeholder.add_css_class("dim-label")
        self._commit_placeholder.set_halign(Gtk.Align.START)
        self._commit_placeholder.set_valign(Gtk.Align.START)
        self._commit_placeholder.set_margin_top(4)
        self._commit_placeholder.set_margin_start(8)
        self._commit_placeholder.set_can_target(False)
        commit_overlay.add_overlay(self._commit_placeholder)
        actions_box.append(commit_overlay)

        # Ctrl+Enter commits (LOCAL scope so it won't interfere with dialogs).
        commit_shortcuts = Gtk.ShortcutController()
        commit_shortcuts.set_scope(Gtk.ShortcutScope.LOCAL)
        commit_shortcuts.add_shortcut(Gtk.Shortcut(
            trigger=Gtk.ShortcutTrigger.parse_string("<Control>Return"),
            action=Gtk.CallbackAction.new(lambda *a: (self._on_commit_clicked(), True)[1]),
        ))
        self.commit_view.add_controller(commit_shortcuts)

        # Buttons row - use hexpand instead of homogeneous for flexible sizing
        buttons_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)

        # Commit is a split button: primary = commit, menu = amend last commit.
        commit_menu = Gio.Menu()
        commit_menu.append("Amend last commit…", "gitcommit.amend")
        self.commit_btn = Adw.SplitButton()
        self.commit_btn.set_label("Commit")
        self.commit_btn.add_css_class("suggested-action")
        self.commit_btn.set_hexpand(True)
        self.commit_btn.set_menu_model(commit_menu)
        self.commit_btn.connect("clicked", self._on_commit_clicked)
        buttons_box.append(self.commit_btn)

        commit_actions = Gio.SimpleActionGroup()
        amend_action = Gio.SimpleAction.new("amend", None)
        amend_action.connect("activate", lambda a, p: self._on_amend_clicked())
        commit_actions.add_action(amend_action)
        self.insert_action_group("gitcommit", commit_actions)

        # Push is a split button: primary = normal push, menu = force / upstream.
        push_menu = Gio.Menu()
        push_menu.append("Force push (with lease)…", "gitpush.force")
        push_menu.append("Set upstream and push", "gitpush.upstream")
        self.push_btn = Adw.SplitButton()
        self.push_btn.set_label("Push")
        self.push_btn.set_hexpand(True)
        self.push_btn.set_menu_model(push_menu)
        self.push_btn.connect("clicked", self._on_push_clicked)
        buttons_box.append(self.push_btn)

        push_actions = Gio.SimpleActionGroup()
        force_action = Gio.SimpleAction.new("force", None)
        force_action.connect("activate", lambda a, p: self._confirm_force_push())
        push_actions.add_action(force_action)
        upstream_action = Gio.SimpleAction.new("upstream", None)
        upstream_action.connect("activate", lambda a, p: self._on_push_clicked(None))
        push_actions.add_action(upstream_action)
        self.insert_action_group("gitpush", push_actions)

        self.pull_btn = Gtk.Button(label="Pull")
        self.pull_btn.set_hexpand(True)
        self.pull_btn.connect("clicked", self._on_pull_clicked)
        buttons_box.append(self.pull_btn)

        # Upstream visibility: shown when the current branch has no upstream, so a
        # silent (0,0) ahead/behind no longer reads as "in sync".
        self._upstream_label = Gtk.Label(label="⤴ not published")
        self._upstream_label.add_css_class("dim-label")
        self._upstream_label.add_css_class("caption")
        self._upstream_label.set_xalign(0)
        self._upstream_label.set_visible(False)
        actions_box.append(self._upstream_label)

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
        """Refresh the changes list asynchronously via git CLI."""
        if not hasattr(self, "branch_label"):
            return  # Not a git repo

        project_dir = str(self.project_path)
        env = git_auth.build_git_env()

        def _fetch():
            branch, ahead, behind = "?", 0, 0
            staged, unstaged = [], []
            error = None
            # Branch + ahead/behind are best-effort (defaults are fine on failure).
            try:
                r = subprocess.run(
                    ["git", "branch", "--show-current"],
                    capture_output=True, text=True, cwd=project_dir, timeout=10, env=env,
                )
                branch = r.stdout.strip() or "HEAD"

                r = subprocess.run(
                    ["git", "rev-list", "--left-right", "--count", "@{upstream}...HEAD"],
                    capture_output=True, text=True, cwd=project_dir, timeout=10, env=env,
                )
                if r.returncode == 0 and r.stdout.strip():
                    parts = r.stdout.strip().split()
                    if len(parts) == 2:
                        behind, ahead = int(parts[0]), int(parts[1])
            except Exception:
                pass
            # Status is the change list — a failure here must surface, not read
            # as "No changes". get_porcelain_status is the single status source.
            try:
                staged, unstaged = self.service.get_porcelain_status(env=env)
            except Exception as e:
                error = str(e)
            has_upstream = self.service.has_upstream()
            return (branch, ahead, behind, staged, unstaged, error, has_upstream)

        # run_async gives a generation token (only the newest refresh renders) and a
        # liveness guard for free (roadmap 2.4).
        run_async(self, worker=_fetch, on_done=lambda data: self._apply_refresh(*data), key="refresh")

    def _apply_refresh(self, branch, ahead, behind, staged, unstaged, error=None,
                       has_upstream=True):
        """Apply fetched git data to the UI (runs on main thread)."""
        if not hasattr(self, "branch_label"):
            return False

        # Update branch name
        self.branch_label.set_label(branch)

        # Update Push/Pull buttons. No upstream -> "Publish" (a normal push auto-sets
        # upstream) + a visible "not published" hint; otherwise the usual Push (N).
        self._upstream_label.set_visible(not has_upstream)
        if not has_upstream:
            self.push_btn.set_label("Publish")
            self.push_btn.add_css_class("suggested-action")
            self.push_btn.set_tooltip_text("This branch has no upstream — publish it")
        else:
            self.push_btn.set_tooltip_text("")
            if ahead > 0:
                self.push_btn.set_label(f"Push ({ahead})")
                self.push_btn.add_css_class("suggested-action")
            else:
                self.push_btn.set_label("Push")
                self.push_btn.remove_css_class("suggested-action")

        if behind > 0:
            self.pull_btn.set_label(f"Pull ({behind})")
            self.pull_btn.add_css_class("suggested-action")
        else:
            self.pull_btn.set_label("Pull")
            self.pull_btn.remove_css_class("suggested-action")

        # Clear content
        children = []
        child = self.content_box.get_first_child()
        while child:
            children.append(child)
            child = child.get_next_sibling()
        for child in children:
            self.content_box.remove(child)

        # A failed status read must not masquerade as a clean tree.
        if error is not None:
            self._has_staged = False
            self._update_commit_button()
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            box.set_valign(Gtk.Align.CENTER)
            box.set_margin_top(36)
            icon = Gtk.Image.new_from_icon_name("dialog-error-symbolic")
            icon.set_pixel_size(48)
            box.append(icon)
            title = Gtk.Label(label="Couldn't read repository status")
            title.add_css_class("dim-label")
            box.append(title)
            detail = Gtk.Label(label=error)
            detail.add_css_class("dim-label")
            detail.add_css_class("caption")
            detail.set_wrap(True)
            detail.set_justify(Gtk.Justification.CENTER)
            box.append(detail)
            retry = Gtk.Button(label="Retry")
            retry.set_halign(Gtk.Align.CENTER)
            retry.set_margin_top(4)
            retry.connect("clicked", lambda _b: self.refresh())
            box.append(retry)
            self.content_box.append(box)
            self._last_status_hash = None
            return False

        if not staged and not unstaged:
            label = Gtk.Label(label="No changes")
            label.add_css_class("dim-label")
            label.set_margin_top(24)
            self.content_box.append(label)
            self._update_commit_button_with(staged)
            self._last_status_hash = ""
            return False

        # Staged section
        if staged:
            self._add_section("Staged", staged, is_staged=True)

        # Unstaged section
        if unstaged:
            self._add_section("Changes", unstaged, is_staged=False)

        self._update_commit_button_with(staged)

        # Update status hash
        all_files = staged + unstaged
        self._last_status_hash = str(sorted((f.path, f.status.name, f.staged) for f in all_files))
        return False

    def _update_commit_button_with(self, staged_files):
        """Cache staged-presence from a fresh refresh, then update the button."""
        self._has_staged = bool(staged_files)
        self._update_commit_button()

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
        header_box._section_name = title  # section tag (Staged/Changes)

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
        tooltip = file_status.path
        # Show renames as "old → new" so the pairing isn't lost.
        if file_status.old_path:
            display_name = f"{Path(file_status.old_path).name} → {display_name}"
            tooltip = f"{file_status.old_path} → {file_status.path}"
        file_label = Gtk.Label(label=display_name)
        file_label.set_xalign(0)
        file_label.set_ellipsize(2)  # PANGO_ELLIPSIZE_MIDDLE
        file_label.set_tooltip_text(tooltip)  # Full path in tooltip
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
        """Handle stage/unstage button click (off-thread, serialized)."""
        if self._busy:
            return
        self._set_busy(True)
        staged, path = file_status.staged, file_status.path

        def work():
            self.service.unstage(path) if staged else self.service.stage(path)

        def done(_):
            self._set_busy(False)
            self.refresh()

        def err(e):
            self._set_busy(False)
            self._show_error(f"Failed to {'unstage' if staged else 'stage'}: {e}")

        run_async(self, worker=work, on_done=done, on_error=err, key="mutate")

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
        """Handle restore confirmation dialog response (off-thread, serialized)."""
        if response != "restore" or self._busy:
            return
        self._set_busy(True)
        path = file_status.path

        def work():
            self.service.restore_file(path)

        def done(_):
            self._set_busy(False)
            self._show_toast(f"Restored: {path}")
            self.refresh()

        def err(e):
            self._set_busy(False)
            self._show_error(f"Failed to restore: {e}")

        run_async(self, worker=work, on_done=done, on_error=err, key="mutate")

    def _on_stage_all_clicked(self, button, is_staged: bool):
        """Handle stage/unstage all button (off-thread, serialized)."""
        if self._busy:
            return
        self._set_busy(True)

        def work():
            self.service.unstage_all() if is_staged else self.service.stage_all()

        def done(_):
            self._set_busy(False)
            self.refresh()

        def err(e):
            self._set_busy(False)
            self._show_error(f"Failed to {'unstage' if is_staged else 'stage'} all: {e}")

        run_async(self, worker=work, on_done=done, on_error=err, key="mutate")

    def _commit_message(self) -> str:
        """The trimmed commit-message text from the multi-line view."""
        buf = self.commit_buffer
        return buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False).strip()

    def _clear_commit_message(self):
        self.commit_buffer.set_text("")

    def _on_commit_entry_changed(self, *args):
        """Toggle the placeholder and refresh button state on message edits."""
        self._commit_placeholder.set_visible(self.commit_buffer.get_char_count() == 0)
        self._update_commit_button()

    def _update_commit_button(self):
        """Keep Commit clickable except mid-op.

        The button stays enabled (so the Amend menu is always reachable, including a
        message-only amend); an empty message or an empty stage is reported via a toast
        / the backend error rather than a disabled button.
        """
        self.commit_btn.set_sensitive(not self._busy)

    def _on_commit_clicked(self, *args):
        """Handle commit button click (off-thread; the busy guard blocks double-commits)."""
        if self._busy:
            return
        message = self._commit_message()
        if not message:
            self._show_toast("Enter a commit message")
            return
        self._set_busy(True)

        def work():
            return self.service.commit(message)

        def done(commit_hash):
            self._set_busy(False)
            self._clear_commit_message()
            self._show_toast(f"Committed: {commit_hash}")
            self.refresh()

        def err(e):
            self._set_busy(False)
            self._show_error(f"Commit failed: {e}")

        run_async(self, worker=work, on_done=done, on_error=err, key="mutate")

    def _on_amend_clicked(self):
        """Amend HEAD with the message in the box (prefill it from HEAD if empty)."""
        if self._busy:
            return
        message = self._commit_message()
        if not message:
            last = self.service.get_head_message()
            if last:
                self.commit_buffer.set_text(last)
                self._show_toast("Loaded last commit message — edit, then Amend again")
            else:
                self._show_error("No commit to amend")
            return
        # Rewriting an already-pushed commit needs a force-push afterwards → confirm.
        if self.service.has_upstream() and self.service.get_ahead_behind()[0] == 0:
            self._confirm_amend(message)
        else:
            self._do_amend(message)

    def _confirm_amend(self, message: str):
        dialog = Adw.AlertDialog()
        dialog.set_heading("Amend a pushed commit?")
        dialog.set_body(
            "HEAD appears to be already pushed. Amending rewrites history; you'll need "
            "a force-push (with lease) to update the remote."
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("amend", "Amend")
        dialog.set_response_appearance("amend", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_close_response("cancel")
        dialog.connect(
            "response",
            lambda _d, r: self._do_amend(message) if r == "amend" else None,
        )
        dialog.present(self.get_root())

    def _do_amend(self, message: str):
        self._set_busy(True)

        def work():
            return self.service.amend_commit(message)

        def done(commit_hash):
            self._set_busy(False)
            self._clear_commit_message()
            self._show_toast(f"Amended: {commit_hash}")
            self.refresh()

        def err(e):
            self._set_busy(False)
            self._show_error(f"Amend failed: {e}")

        run_async(self, worker=work, on_done=done, on_error=err, key="mutate")

    def _ssh_precheck(self, op):
        """Run ``op`` (a push/pull entry point), warning first if the remote is SSH but
        the ssh-agent has no identities — the common cause of a failed SSH auth, where
        the username/password dialog would be useless. The user can still proceed.
        """
        remote = self.service.get_remote()
        url = remote.url if remote else ""
        if url and git_auth.is_ssh_remote(url) and not git_auth.ssh_agent_has_keys():
            dialog = Adw.AlertDialog()
            dialog.set_heading("No SSH key loaded")
            dialog.set_body(
                "This repository uses an SSH remote, but your ssh-agent has no "
                "identities loaded. Run `ssh-add` to add your key, or continue and it "
                "may fail to authenticate."
            )
            dialog.add_response("cancel", "Cancel")
            dialog.add_response("continue", "Try anyway")
            dialog.set_response_appearance("continue", Adw.ResponseAppearance.SUGGESTED)
            dialog.set_close_response("cancel")
            dialog.connect("response", lambda _d, r: op() if r == "continue" else None)
            dialog.present(self.get_root())
        else:
            op()

    def _on_pull_clicked(self, button):
        """Handle pull button click (SSH pre-check, then off-thread dirty check)."""
        if self._busy:
            return
        self._auth_attempts = 0
        self._ssh_precheck(self._start_pull)

    def _start_pull(self):
        self._set_busy(True)

        def work():
            return self.service.has_uncommitted_changes()

        def done(dirty):
            self._set_busy(False)
            if dirty:
                self._show_uncommitted_warning()
            else:
                self._do_pull()

        def err(e):
            self._set_busy(False)
            self._show_error_dialog("Pull Failed", str(e))

        run_async(self, worker=work, on_done=done, on_error=err, key="mutate")

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
        """Execute pull off-thread so a slow network never freezes the window (2.2)."""
        if self._busy:
            return
        self._set_busy(True)
        self._show_toast("Pulling…")

        def work():
            return self.service.pull(credentials, remember=self._remember_creds)

        def done(result):
            self._auth_attempts = 0
            self._set_busy(False)
            self._show_toast(result)
            self.refresh()
            self._file_monitor_service.emit("git-history-changed")

        def err(e):
            self._set_busy(False)
            if isinstance(e, AuthenticationRequired):
                self._retry_with_auth("Pull", e.remote_url, self._do_pull)
            else:
                self._show_error_dialog("Pull Failed", str(e))

        run_async(self, worker=work, on_done=done, on_error=err, key="mutate")

    def _on_push_clicked(self, button):
        """Handle push button click (SSH pre-check, then push)."""
        if self._busy:
            return
        self._auth_attempts = 0
        self._ssh_precheck(self._do_push)

    def _do_push(self, credentials: tuple[str, str] | None = None,
                 force_with_lease: bool = False):
        """Execute push off-thread (2.2)."""
        if self._busy:
            return
        self._set_busy(True)
        self._show_toast("Force pushing…" if force_with_lease else "Pushing…")

        def work():
            return self.service.push(credentials, force_with_lease=force_with_lease,
                                     remember=self._remember_creds)

        def done(result):
            self._auth_attempts = 0
            self._set_busy(False)
            self._show_toast(result)
            self.refresh()  # Update counts
            self._file_monitor_service.emit("git-history-changed")

        def err(e):
            self._set_busy(False)
            if isinstance(e, AuthenticationRequired):
                # Preserve the force choice through the auth retry.
                self._retry_with_auth(
                    "Push", e.remote_url,
                    lambda creds: self._do_push(creds, force_with_lease=force_with_lease),
                )
            elif isinstance(e, PushRejected):
                self._show_push_rejected_dialog()
            else:
                self._show_error_dialog("Push Failed", str(e))

        run_async(self, worker=work, on_done=done, on_error=err, key="mutate")

    def _show_push_rejected_dialog(self):
        """Offer a recovery path when the remote has diverged."""
        dialog = Adw.AlertDialog()
        dialog.set_heading("Push Rejected")
        dialog.set_body(
            "The remote has commits you don't have locally, so the push was "
            "rejected. Pull and integrate them first, or force-push to overwrite "
            "the remote with your branch."
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("pull", "Pull, then Push")
        dialog.add_response("force", "Force Push (with lease)")
        dialog.set_response_appearance("force", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("pull")
        dialog.set_close_response("cancel")

        def on_response(dlg, response):
            if response == "pull":
                self._auth_attempts = 0
                self._do_pull()
            elif response == "force":
                self._confirm_force_push()

        dialog.connect("response", on_response)
        dialog.present(self.get_root())

    def _confirm_force_push(self):
        """Confirm a --force-with-lease push (destructive)."""
        if self._busy:
            return
        dialog = Adw.AlertDialog()
        dialog.set_heading("Force Push?")
        dialog.set_body(
            "Force-push with lease overwrites the remote branch with your local "
            "one. It refuses if someone else pushed since your last fetch, but it "
            "still rewrites remote history. Continue?"
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("force", "Force Push")
        dialog.set_response_appearance("force", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_close_response("cancel")

        def on_response(dlg, response):
            if response == "force":
                self._auth_attempts = 0
                self._do_push(force_with_lease=True)

        dialog.connect("response", on_response)
        dialog.present(self.get_root())

    def _retry_with_auth(self, operation: str, remote_url: str, retry_callback):
        """Prompt for credentials and retry, capped at MAX_AUTH_ATTEMPTS (2.2)."""
        self._auth_attempts += 1
        if self._auth_attempts > self.MAX_AUTH_ATTEMPTS:
            self._auth_attempts = 0
            self._show_error_dialog(
                f"{operation} Failed", "Authentication failed after several attempts."
            )
            return
        self._show_credentials_dialog(operation, remote_url, retry_callback)

    def _show_credentials_dialog(self, operation: str, remote_url: str, retry_callback):
        """Show dialog to get git credentials."""
        dialog = Adw.AlertDialog()
        dialog.set_heading("Authentication Required")
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

        # Opt-in persistence (roadmap 3.7). Default on where a keyring exists;
        # otherwise it would fall back to the plaintext store, so default off.
        from ..services.credential_service import CredentialService
        keyring = CredentialService.get_instance().available()
        remember_check = Gtk.CheckButton(
            label="Remember in keyring" if keyring else "Remember (plaintext — no keyring found)"
        )
        remember_check.set_active(keyring)
        box.append(remember_check)

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
                    self._remember_creds = remember_check.get_active()
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
