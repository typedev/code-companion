"""Project workspace window."""

import json
import os
import secrets
import shutil
import socket
import subprocess
import tempfile
from pathlib import Path

from gi.repository import Adw, Gtk, GLib, Gdk, Gio

from .models import Session
from .services import get_adapter, ProjectLock, ProjectRegistry, GitService, IconCache, ToastService, SettingsService, FileMonitorService, IssuesService, McpServer, run_async
from .services import session_notify, message_store
from .utils import git_auth, claude_session
from .utils.project_identity import resolve_project_identity
from .utils.git_worktree import is_linked_worktree, worktree_parent_root
from .widgets import SessionView, TerminalView, FileTree, FileEditor, TasksPanel, GitChangesPanel, GitHistoryPanel, DiffView, CommitDetailView, ClaudeHistoryPanel, FileSearchDialog, UnifiedSearch, NotesPanel, PreferencesDialog, QueryEditor, ProblemsPanel, ProblemsDetailView, IssuesPanel, IssueDetailView, MessagesPanel, MessageThreadView, ImageViewer, SvgEditor, BinaryFileView
from .utils.text_files import is_binary

# Managed tmux config for the persistent Claude pane (see the session supervisor
# plan). Loaded via `tmux -f`, so the user's own ~/.tmux.conf is never touched.
_TMUX_CONF = Path(__file__).resolve().parent / "resources" / "tmux" / "tmux-managed.conf"


def escape_markup(text: str) -> str:
    """Escape text for safe use in GTK markup."""
    return GLib.markup_escape_text(text)


class ProjectWindow(Adw.ApplicationWindow):
    """Project workspace window with file tree, tabs, and terminal."""

    def __init__(self, project_path: str, **kwargs):
        super().__init__(**kwargs)

        self.project_path = Path(project_path).resolve()
        self.project_name = self.project_path.name

        self.registry = ProjectRegistry()
        self.lock = ProjectLock(str(self.project_path))
        self.file_monitor_service = FileMonitorService(self.project_path)

        self.claude_terminal: TerminalView | None = None
        self.claude_container: Gtk.Box | None = None
        # Per-window MCP control surface (started with the Claude session).
        self.mcp_server: McpServer | None = None
        self._mcp_config_path: str | None = None
        self._notify_settings_path: str | None = None
        self._workspace_collapsed = False
        self._workspace_split_position = 260
        self.commit_detail_page: Adw.TabPage | None = None
        self.commit_detail_view: CommitDetailView | None = None
        self.session_detail_page: Adw.TabPage | None = None
        self.session_detail_view: SessionView | None = None
        self.session_diff_page: Adw.TabPage | None = None
        self.session_diff_view: DiffView | None = None
        self.git_diff_page: Adw.TabPage | None = None
        self.git_diff_view: DiffView | None = None
        self.git_diff_path: str | None = None
        self.git_diff_staged: bool | None = None
        self.problems_detail_page: Adw.TabPage | None = None
        self.problems_detail_view: ProblemsDetailView | None = None
        self.issue_detail_page: Adw.TabPage | None = None
        self.issue_detail_view: IssueDetailView | None = None
        self.issues_service: IssuesService | None = None
        self.message_detail_page: Adw.TabPage | None = None
        self.message_detail_view: MessageThreadView | None = None
        self._project_remote: str | None = None  # canonical remote (messages address)

        # Acquire lock
        if not self.lock.acquire():
            # This shouldn't happen if project manager checked, but be safe
            self.close()
            return

        # Connect destroy handler early so lock is always released on crash
        self.connect("destroy", self._on_destroy)

        # Register project if not already, and stamp it as just-opened so the
        # Project Manager floats it to the top (MRU). Runs for CLI opens too.
        self.registry.register_project(str(self.project_path))
        self.registry.mark_opened(str(self.project_path))

        self._setup_window()
        self._build_ui()

        # Defer heavy project loading until after window is presented
        GLib.idle_add(self._load_project)

        # Connect window signals
        self.connect("close-request", self._on_close_request)

        # Setup keyboard shortcuts
        self._setup_shortcuts()

    def _setup_window(self):
        """Configure window properties."""
        # Get settings service (needed by _build_ui)
        self.settings = SettingsService.get_instance()

        # Get AI adapter based on settings
        provider = self.settings.get("ai.provider", "claude")
        self.adapter = get_adapter(provider)

        # Initial title without branch (git service not ready yet)
        self.set_title(f"{self.project_name} - Code Companion")

        # Apply theme
        self._apply_theme()

        # Listen for theme changes
        self.settings.connect("changed", self._on_setting_changed)

        # Restore window size from settings or use default
        width = self.settings.get("window.width", 1200)
        height = self.settings.get("window.height", 800)
        self.set_default_size(width, height)

        # Restore maximized state
        if self.settings.get("window.maximized", False):
            self.maximize()

        # Connect to window state changes for saving
        self.connect("notify::maximized", self._on_window_state_changed)

    def _update_window_title(self):
        """Update window title with project name and branch."""
        # Get branch name if git repo
        branch = ""
        if hasattr(self, "git_service") and hasattr(self, "_is_git_repo") and self._is_git_repo:
            branch = self.git_service.get_branch_name()

        if branch:
            title = f"{self.project_name} / git:{branch}"
        else:
            title = self.project_name

        self.set_title(f"{title} - Code Companion")

        # Also update header title widget if available
        if hasattr(self, "window_title"):
            self.window_title.set_title(title)
            # A linked worktree advertises its parent + branch in the subtitle so
            # it's obvious this window is a worktree, not the main checkout.
            if getattr(self, "_is_worktree", False):
                parent = getattr(self, "_worktree_parent", None)
                parent_name = parent.name if parent else "parent"
                subtitle = f"⑂ worktree of {parent_name}"
                if branch:
                    subtitle += f" · {branch}"
                self.window_title.set_subtitle(subtitle)

    def _apply_theme(self):
        """Apply color theme from settings."""
        theme = self.settings.get("appearance.theme", "system")
        style_manager = Adw.StyleManager.get_default()

        if theme == "dark":
            style_manager.set_color_scheme(Adw.ColorScheme.FORCE_DARK)
        elif theme == "light":
            style_manager.set_color_scheme(Adw.ColorScheme.FORCE_LIGHT)
        else:  # system
            style_manager.set_color_scheme(Adw.ColorScheme.DEFAULT)

    def _on_setting_changed(self, settings, key, value):
        """Handle setting changes."""
        if key == "appearance.theme":
            self._apply_theme()

    def _on_window_state_changed(self, window, pspec):
        """Handle window state changes (maximized)."""
        self.settings.set("window.maximized", self.is_maximized())

    def _on_paned_position_changed(self, paned, pspec):
        """Handle sidebar pane position changes - save to settings."""
        position = paned.get_position()
        # Only save if sidebar is visible and position is reasonable
        if position >= 300:
            self.settings.set("window.sidebar_width", position)

    def _build_ui(self):
        """Build the UI layout."""
        # Main container: [Paned[Sidebar | Content]]. The old left vertical toolbar
        # is gone — its view-switcher buttons now live in the header.
        main_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)

        # Badge CSS for the activity buttons (built in _build_header)
        self._setup_badge_css()

        # Main horizontal split with resizable pane
        self.paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        self.paned.set_shrink_start_child(False)  # Don't shrink below min size
        self.paned.set_shrink_end_child(False)
        self.paned.set_resize_start_child(False)
        self.paned.set_resize_end_child(True)
        self.paned.set_hexpand(True)

        # Sidebar
        self.sidebar = self._build_sidebar()
        self.paned.set_start_child(self.sidebar)

        # Main content with tabs
        content = self._build_content()
        self.paned.set_end_child(content)

        # Restore sidebar width from settings (default 370 = minimum width)
        saved_position = self.settings.get("window.sidebar_width", 370)
        self.paned.set_position(saved_position)

        # Save sidebar position when changed
        self.paned.connect("notify::position", self._on_paned_position_changed)

        main_box.append(self.paned)

        # Wrap everything in a toolbar view
        toolbar_view = Adw.ToolbarView()
        header = self._build_header()
        toolbar_view.add_top_bar(header)
        toolbar_view.set_content(main_box)

        # Wrap in toast overlay for notifications
        self.toast_overlay = Adw.ToastOverlay()
        self.toast_overlay.set_child(toolbar_view)

        # Initialize toast service
        ToastService.init(self.toast_overlay)

        self.set_content(self.toast_overlay)

        # Update window title with branch name (now that all widgets exist)
        self._update_window_title()

    def _build_header(self) -> Adw.HeaderBar:
        """Build the header bar with buttons."""
        header = Adw.HeaderBar()

        # Toggle sidebar button
        self.sidebar_btn = Gtk.ToggleButton()
        self.sidebar_btn.set_icon_name("sidebar-show-symbolic")
        self.sidebar_btn.set_tooltip_text("Toggle sidebar")
        self.sidebar_btn.set_active(True)
        self.sidebar_btn.connect("toggled", self._on_sidebar_toggled)
        header.pack_start(self.sidebar_btn)

        # Sidebar view-switcher (Files/Git/Claude/Notes/Problems/Issues) as a linked
        # toggle group — moved here from the old left vertical toolbar.
        header.pack_start(self._build_activity_bar())

        # New terminal button
        terminal_btn = Gtk.Button()
        terminal_btn.set_icon_name("tab-new-symbolic")
        terminal_btn.set_tooltip_text("New terminal")
        terminal_btn.connect("clicked", self._on_new_terminal_clicked)
        header.pack_start(terminal_btn)

        # Title (will be updated with branch name after git service init)
        self.window_title = Adw.WindowTitle()
        self.window_title.set_title(self.project_name)
        self.window_title.set_subtitle(str(self.project_path))
        header.set_title_widget(self.window_title)

        # Open external terminal button
        ext_terminal_btn = Gtk.Button()
        ext_terminal_btn.set_icon_name("terminal-symbolic")
        ext_terminal_btn.set_tooltip_text("Open in system terminal")
        ext_terminal_btn.connect("clicked", self._on_external_terminal_clicked)
        header.pack_end(ext_terminal_btn)

        # Projects button (open Project Manager)
        projects_btn = Gtk.Button()
        projects_btn.set_icon_name("view-app-grid-symbolic")
        projects_btn.set_tooltip_text("Projects")
        projects_btn.connect("clicked", self._on_projects_clicked)
        header.pack_end(projects_btn)

        # Preferences button
        prefs_btn = Gtk.Button()
        prefs_btn.set_icon_name("emblem-system-symbolic")
        prefs_btn.set_tooltip_text("Preferences")
        prefs_btn.connect("clicked", self._on_preferences_clicked)
        header.pack_end(prefs_btn)

        return header

    def _create_toolbar_button(self, icon_name: str, tooltip: str, tab_name: str) -> Gtk.ToggleButton:
        """Create a toolbar toggle button with an icon."""
        btn = Gtk.ToggleButton()
        btn.set_tooltip_text(tooltip)
        btn.add_css_class("flat")
        btn.set_size_request(36, 36)

        # Load icon from resources — wrap in Overlay for badge support
        overlay = Gtk.Overlay()
        overlay.set_size_request(36, 36)

        icon_path = Path(__file__).parent / "resources" / "icons" / f"{icon_name}.svg"
        if icon_path.exists():
            gicon = Gio.FileIcon.new(Gio.File.new_for_path(str(icon_path)))
            image = Gtk.Image.new_from_gicon(gicon)
            image.set_pixel_size(20)
            overlay.set_child(image)

        btn.set_child(overlay)

        btn.connect("toggled", self._on_tab_toggled, tab_name)
        return btn

    def _build_activity_bar(self) -> Gtk.Box:
        """Build the sidebar view-switcher as plain square toggle buttons for the header."""
        group = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        group.set_spacing(2)

        self._tab_buttons = {}

        # Files button
        files_btn = self._create_toolbar_button("folder-open", "Files", "files")
        files_btn.set_active(True)
        group.append(files_btn)
        self._tab_buttons["files"] = files_btn

        # Git button (will be shown/hidden based on git repo status)
        self._git_toolbar_btn = self._create_toolbar_button("git", "Git", "git")
        group.append(self._git_toolbar_btn)
        self._tab_buttons["git"] = self._git_toolbar_btn

        # Claude button (shows the session-history panel in the sidebar)
        claude_btn = self._create_toolbar_button("claude", "Claude", "claude")
        group.append(claude_btn)
        self._tab_buttons["claude"] = claude_btn

        # Notes button
        notes_btn = self._create_toolbar_button("file", "Notes", "notes")
        group.append(notes_btn)
        self._tab_buttons["notes"] = notes_btn

        # Problems button
        problems_btn = self._create_toolbar_button("problems", "Problems (ruff/mypy)", "problems")
        group.append(problems_btn)
        self._tab_buttons["problems"] = problems_btn

        # Issues button (GitHub Issues)
        self._issues_toolbar_btn = self._create_toolbar_button("todo", "Issues", "issues")
        group.append(self._issues_toolbar_btn)
        self._tab_buttons["issues"] = self._issues_toolbar_btn

        # Messages button (inter-project mailbox)
        self._messages_toolbar_btn = self._create_toolbar_button("mail", "Messages", "messages")
        group.append(self._messages_toolbar_btn)
        self._tab_buttons["messages"] = self._messages_toolbar_btn

        return group

    def _setup_badge_css(self):
        """Setup CSS for toolbar badge indicators."""
        css = """
        .toolbar-badge {
            font-size: 8px;
            font-weight: bold;
            min-width: 14px;
            min-height: 14px;
            padding: 0 3px;
            border-radius: 8px;
            color: white;
            margin-right: 1px;
            margin-top: 1px;
        }
        .toolbar-badge-red {
            background-color: #e33;
        }
        .toolbar-badge-yellow {
            background-color: #e9a100;
            color: alpha(black, 0.85);
        }
        .toolbar-badge-blue {
            background-color: #3584e4;
        }
        """
        provider = Gtk.CssProvider()
        provider.load_from_data(css.encode())
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

    def _set_button_badge(self, btn: Gtk.ToggleButton, count: int, color: str):
        """Set or update a badge on a toolbar button.

        Args:
            btn: The toggle button (must have Gtk.Overlay as child).
            count: Number to display. 0 hides the badge.
            color: 'red' or 'yellow'.
        """
        overlay = btn.get_child()
        if not isinstance(overlay, Gtk.Overlay):
            return

        # Remove existing badge
        badge_attr = f"_badge_{id(btn)}"
        old_badge = getattr(self, badge_attr, None)
        if old_badge is not None:
            overlay.remove_overlay(old_badge)
            setattr(self, badge_attr, None)

        if count <= 0:
            return

        label = Gtk.Label(label=str(count) if count <= 99 else "99+")
        label.add_css_class("toolbar-badge")
        label.add_css_class(f"toolbar-badge-{color}")
        label.set_halign(Gtk.Align.END)
        label.set_valign(Gtk.Align.START)
        overlay.add_overlay(label)
        setattr(self, badge_attr, label)

    def _update_git_badge(self):
        """Update the Git toolbar button badge based on changes/ahead count."""
        if not self._is_git_repo:
            return

        project_dir = str(self.project_path)
        env = git_auth.build_git_env()

        def _fetch():
            # Use git CLI (subprocess) to avoid pygit2 GIL blocking
            changes_count, ahead = 0, 0
            try:
                result = subprocess.run(
                    ["git", "status", "--porcelain", "-z"],
                    capture_output=True, cwd=project_dir, timeout=30, env=env,
                )
                # Count non-empty entries
                entries = [e for e in result.stdout.split(b"\x00") if len(e) >= 3]
                changes_count = len(entries)

                # Check ahead/behind
                result2 = subprocess.run(
                    ["git", "rev-list", "--left-right", "--count", "@{upstream}...HEAD"],
                    capture_output=True, cwd=project_dir, timeout=10, text=True, env=env,
                )
                if result2.returncode == 0 and result2.stdout.strip():
                    parts = result2.stdout.strip().split()
                    if len(parts) == 2:
                        ahead = int(parts[1])
            except Exception:
                pass
            return (changes_count, ahead)

        def _apply(data):
            changes_count, ahead = data
            if not hasattr(self, "_git_toolbar_btn"):
                return
            if changes_count > 0:
                self._set_button_badge(self._git_toolbar_btn, changes_count, "red")
            elif ahead > 0:
                self._set_button_badge(self._git_toolbar_btn, ahead, "yellow")
            else:
                self._set_button_badge(self._git_toolbar_btn, 0, "red")

        # Generation token → a stale count can't land after a newer one (roadmap 2.8).
        run_async(self, worker=_fetch, on_done=_apply, key="git_badge")

    def _build_sidebar(self) -> Gtk.Box:
        """Build the sidebar with Files/Git/Claude tabs."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.set_hexpand(False)
        box.set_size_request(370, -1)  # Minimum width

        # Check if this is a git repo
        self.git_service = GitService(self.project_path)
        self._is_git_repo = self.git_service.is_git_repo()
        if self._is_git_repo:
            self.git_service.open()

        # Worktree self-awareness: a linked worktree gets a "worktree of <parent>"
        # subtitle so it's never mistaken for the main checkout.
        self._is_worktree = is_linked_worktree(self.project_path)
        self._worktree_parent = (
            worktree_parent_root(self.project_path) if self._is_worktree else None
        )

        # Issues service (GitHub Issues); harmless for non-GitHub repos
        self.issues_service = IssuesService(self.project_path)

        # Canonical git remote — the address this project uses to send/receive messages.
        # None for a local-only project (which cannot participate in the mailbox).
        identity = resolve_project_identity(self.project_path)
        self._project_remote = identity.canonical_remote if identity else None

        # Show/hide Git button based on git repo status
        if hasattr(self, "_git_toolbar_btn"):
            self._git_toolbar_btn.set_visible(self._is_git_repo)

        # Show/hide Issues button based on GitHub remote
        if hasattr(self, "_issues_toolbar_btn"):
            self._issues_toolbar_btn.set_visible(self.issues_service.is_github_repo())

        # Show/hide Messages button based on whether the project has a remote identity
        if hasattr(self, "_messages_toolbar_btn"):
            self._messages_toolbar_btn.set_visible(self._project_remote is not None)

        # Track which sidebar tabs have been fully built (for lazy loading)
        self._built_tabs: set[str] = set()

        # Top-level stack: Files / Git / Claude / Notes
        self.sidebar_stack = Gtk.Stack()
        self.sidebar_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.sidebar_stack.set_vexpand(True)
        self.sidebar_stack.set_hexpand(False)

        # Files page — build immediately (default visible tab)
        files_page = self._build_files_page()
        self.sidebar_stack.add_named(files_page, "files")
        self._built_tabs.add("files")

        # Deferred tabs — add placeholders, built on first switch
        if self._is_git_repo:
            self.sidebar_stack.add_named(self._create_tab_placeholder(), "git")

        self.sidebar_stack.add_named(self._create_tab_placeholder(), "claude")
        self.sidebar_stack.add_named(self._create_tab_placeholder(), "notes")
        self.sidebar_stack.add_named(self._create_tab_placeholder(), "problems")
        self.sidebar_stack.add_named(self._create_tab_placeholder(), "issues")
        self.sidebar_stack.add_named(self._create_tab_placeholder(), "messages")

        # Wrap stack in scrolled window to allow shrinking
        scrolled = Gtk.ScrolledWindow()
        # NEVER for horizontal = force content to fit width, AUTOMATIC for vertical
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        # IMPORTANT: Don't let content width inflate sidebar width
        scrolled.set_propagate_natural_width(False)
        scrolled.set_vexpand(True)
        scrolled.set_child(self.sidebar_stack)
        box.append(scrolled)

        return box

    def _create_tab_placeholder(self) -> Gtk.Box:
        """Create a placeholder with spinner for deferred tab loading."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.set_vexpand(True)
        box.set_valign(Gtk.Align.CENTER)
        box.set_halign(Gtk.Align.CENTER)

        spinner = Gtk.Spinner()
        spinner.set_size_request(32, 32)
        spinner.start()
        box.append(spinner)

        return box

    def _ensure_tab_built(self, tab_name: str):
        """Build a sidebar tab on first access (lazy loading)."""
        if tab_name in self._built_tabs:
            return

        # Remove placeholder
        placeholder = self.sidebar_stack.get_child_by_name(tab_name)
        if placeholder:
            self.sidebar_stack.remove(placeholder)

        # Build the real page
        if tab_name == "git" and self._is_git_repo:
            page = self._build_git_page()
        elif tab_name == "claude":
            page = self._build_claude_page()
        elif tab_name == "notes":
            page = self._build_notes_page()
        elif tab_name == "problems":
            page = self._build_problems_page()
        elif tab_name == "issues":
            page = self._build_issues_page()
        elif tab_name == "messages":
            page = self._build_messages_page()
        else:
            return

        self.sidebar_stack.add_named(page, tab_name)
        self._built_tabs.add(tab_name)

    def _on_tab_toggled(self, button, tab_name):
        """Handle tab button toggle."""
        if button.get_active():
            # Skip if sidebar_stack not yet created (during init)
            if not hasattr(self, "sidebar_stack"):
                return
            # Activity buttons live in the header now; clicking one reveals the
            # sidebar they control if it was hidden.
            if hasattr(self, "sidebar") and not self.sidebar.get_visible():
                self.sidebar_btn.set_active(True)
            # Lazy build the tab if needed
            if hasattr(self, "_built_tabs"):
                self._ensure_tab_built(tab_name)
            # Switch to this tab (only if page exists in stack)
            if self.sidebar_stack.get_child_by_name(tab_name):
                self.sidebar_stack.set_visible_child_name(tab_name)
            # Deactivate other buttons
            for name, btn in self._tab_buttons.items():
                if name != tab_name:
                    btn.set_active(False)
            # Lazy load Claude history when tab is shown
            if tab_name == "claude" and hasattr(self, "claude_history_panel"):
                self.claude_history_panel.load_if_needed()
            # Lazy load Problems when tab is shown
            if tab_name == "problems" and hasattr(self, "problems_panel"):
                self.problems_panel.load_if_needed()
            # Lazy load Issues when tab is shown
            if tab_name == "issues" and hasattr(self, "issues_panel"):
                self.issues_panel.load_if_needed()
            # Lazy load Messages when tab is shown
            if tab_name == "messages" and hasattr(self, "messages_panel"):
                self.messages_panel.load_if_needed()
        else:
            # Don't allow deactivating without activating another
            # Check if any other visible button is active
            any_active = any(
                btn.get_active() for btn in self._tab_buttons.values()
                if btn.get_visible()
            )
            if not any_active:
                button.set_active(True)

    def _build_notes_page(self) -> Gtk.Box:
        """Build the Notes tab content."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.set_vexpand(True)

        self.notes_panel = NotesPanel(str(self.project_path), self.file_monitor_service)
        self.notes_panel.connect("open-file", self._on_notes_open_file)
        self.notes_panel.connect("open-file-at-line", self._on_notes_open_file_at_line)
        box.append(self.notes_panel)

        return box

    def _build_problems_page(self) -> Gtk.Box:
        """Build the Problems tab content."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.set_vexpand(True)

        self.problems_panel = ProblemsPanel(str(self.project_path))
        self.problems_panel.connect("file-selected", self._on_problems_file_selected)
        box.append(self.problems_panel)

        return box

    def _on_problems_file_selected(self, panel, file_path: str, file_problems):
        """Handle problems panel file selection - show detail view."""
        if not file_problems or not file_problems.problems:
            return

        # Reuse existing problems detail view if available
        if self.problems_detail_view is not None and self.problems_detail_page is not None:
            self.problems_detail_view.update(file_path, file_problems)
            self.problems_detail_page.set_title(f"Problems: {Path(file_path).name}")
            self.tab_view.set_selected_page(self.problems_detail_page)
            return

        # Create new problems detail view
        self.problems_detail_view = ProblemsDetailView(file_path, file_problems, self.project_path)

        # Add tab
        self.problems_detail_page = self.tab_view.append(self.problems_detail_view)
        self.problems_detail_page.set_title(f"Problems: {Path(file_path).name}")
        self.problems_detail_page.set_icon(Gio.ThemedIcon.new("dialog-warning-symbolic"))

        self.tab_view.set_selected_page(self.problems_detail_page)

    def _build_issues_page(self) -> Gtk.Box:
        """Build the Issues tab content (GitHub Issues)."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.set_vexpand(True)

        self.issues_panel = IssuesPanel(str(self.project_path), self.issues_service)
        self.issues_panel.connect("issue-selected", self._on_issue_selected)
        self.issues_panel.connect("issues-changed", lambda _p: self._update_issues_badge())
        box.append(self.issues_panel)

        return box

    def _on_issue_selected(self, panel, issue):
        """Handle issue selection - show detail view (single-tab reuse)."""
        # Reuse existing detail view if available
        if self.issue_detail_view is not None and self.issue_detail_page is not None:
            self.issue_detail_view.update(issue)
            self.issue_detail_page.set_title(f"Issue #{issue.number}")
            self.tab_view.set_selected_page(self.issue_detail_page)
            return

        self.issue_detail_view = IssueDetailView(issue, self.issues_service)
        self.issue_detail_view.connect("send-to-claude", self._on_send_issue_to_claude)
        self.issue_detail_view.connect(
            "issue-changed", lambda _v, _i: self._update_issues_badge()
        )

        self.issue_detail_page = self.tab_view.append(self.issue_detail_view)
        self.issue_detail_page.set_title(f"Issue #{issue.number}")
        self.issue_detail_page.set_icon(Gio.ThemedIcon.new("dialog-information-symbolic"))

        self.tab_view.set_selected_page(self.issue_detail_page)

    def _on_send_issue_to_claude(self, view, prompt: str):
        """Feed a prepared issue prompt into the singleton Claude terminal."""
        self._feed_prompt_to_claude(prompt)

    def _feed_prompt_to_claude(self, prompt: str):
        """Feed a prepared prompt into the singleton Claude terminal (starting it if needed)."""
        started_now = self.claude_terminal is None
        if started_now:
            self._on_claude_clicked(None)

        def _feed():
            if self.claude_terminal:
                self.claude_terminal.terminal.feed_child(prompt.encode("utf-8"))
                GLib.timeout_add(50, self._send_enter_to_terminal)
            return False

        # If the session was just created, give the CLI a moment to start.
        GLib.timeout_add(400 if started_now else 0, _feed)

    def _update_issues_badge(self):
        """Update the Issues toolbar badge with the open-issue count."""
        if not hasattr(self, "_issues_toolbar_btn"):
            return
        if self.issues_service is None or not self.issues_service.is_github_repo():
            return

        def _fetch():
            try:
                return len(self.issues_service.list_issues("open"))
            except Exception:
                return 0

        def _apply(count):
            if hasattr(self, "_issues_toolbar_btn"):
                self._set_button_badge(self._issues_toolbar_btn, count, "blue")

        run_async(self, worker=_fetch, on_done=_apply, key="issues_badge")

    # ------------------------------------------------------------------
    # Messages (inter-project mailbox)
    # ------------------------------------------------------------------
    def _build_messages_page(self) -> Gtk.Box:
        """Build the Messages tab content (inter-project mailbox)."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.set_vexpand(True)

        self.messages_panel = MessagesPanel(self._project_remote)
        self.messages_panel.connect("message-selected", self._on_message_selected)
        self.messages_panel.connect(
            "messages-changed", lambda _p: self._update_messages_badge()
        )
        box.append(self.messages_panel)
        return box

    def _on_message_selected(self, panel, thread):
        """Show a message thread in the main area (single-tab reuse)."""
        if self.message_detail_view is not None and self.message_detail_page is not None:
            self.message_detail_view.update(thread)
            self.message_detail_page.set_title(thread.subject or "Message")
            self.tab_view.set_selected_page(self.message_detail_page)
            return

        self.message_detail_view = MessageThreadView(thread, self._project_remote or "")
        self.message_detail_view.connect("send-to-claude", self._on_send_thread_to_claude)
        self.message_detail_view.connect("thread-changed", self._on_thread_changed)

        self.message_detail_page = self.tab_view.append(self.message_detail_view)
        self.message_detail_page.set_title(thread.subject or "Message")
        self.message_detail_page.set_icon(Gio.ThemedIcon.new("mail-read-symbolic"))
        self.tab_view.set_selected_page(self.message_detail_page)

    def _on_thread_changed(self, view, thread):
        """After a reply/status/delete: refresh the list and badge."""
        if hasattr(self, "messages_panel"):
            self.messages_panel.refresh()
        self._update_messages_badge()

    def _on_send_thread_to_claude(self, view, prompt: str):
        """Feed a prepared message-thread prompt into the singleton Claude terminal."""
        self._feed_prompt_to_claude(prompt)

    def _update_messages_badge(self):
        """Update the Messages toolbar badge with the open-inbox count."""
        if not hasattr(self, "_messages_toolbar_btn") or not self._project_remote:
            return
        me = self._project_remote

        def _fetch():
            try:
                return len(message_store.threads_for(me, box="inbox", status="open"))
            except Exception:
                return 0

        def _apply(count):
            if hasattr(self, "_messages_toolbar_btn"):
                self._set_button_badge(self._messages_toolbar_btn, count, "blue")

        run_async(self, worker=_fetch, on_done=_apply, key="messages_badge")

    def _on_notes_open_file(self, panel, file_path: str):
        """Handle notes panel file open."""
        self._on_file_activated(self.file_tree, file_path)

    def _on_notes_open_file_at_line(self, panel, file_path: str, line_number: int, tag: str):
        """Handle notes panel TODO click."""
        self._on_file_activated(self.file_tree, file_path)
        GLib.idle_add(lambda: self._go_to_line_in_editor(file_path, line_number, tag))

    def _on_search_open_file_at_line(self, widget, file_path: str, line_number: int, search_term: str):
        """Handle content search result click - open file at line."""
        self._on_file_activated(self.file_tree, file_path)
        # Go to line after file is opened
        GLib.idle_add(lambda: self._go_to_line_in_editor(file_path, line_number, search_term))

    def _on_search_open_file(self, widget, file_path: str):
        """Handle filename search result click - just open file."""
        self._on_file_activated(self.file_tree, file_path)

    def _go_to_line_in_editor(self, file_path: str, line_number: int, search_term: str = None):
        """Go to specific line in already-open editor."""
        file_path = self._canonical_path(file_path)
        for i in range(self.tab_view.get_n_pages()):
            page = self.tab_view.get_nth_page(i)
            child = page.get_child()
            if hasattr(child, "file_path") and child.file_path == file_path:
                if hasattr(child, "go_to_line"):
                    child.go_to_line(line_number, search_term)
                break
        return False

    def _select_lines_in_editor(self, file_path: str, start_line: int, end_line: int):
        """Select a line range in an already-open editor (mirrors _go_to_line_in_editor)."""
        file_path = self._canonical_path(file_path)
        for i in range(self.tab_view.get_n_pages()):
            page = self.tab_view.get_nth_page(i)
            child = page.get_child()
            if hasattr(child, "file_path") and child.file_path == file_path:
                if hasattr(child, "select_line_range"):
                    child.select_line_range(start_line, end_line)
                break
        return False

    def _build_files_page(self) -> Gtk.Box:
        """Build the Files tab content."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.set_vexpand(True)

        # Header with action buttons
        header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        header_box.set_margin_start(12)
        header_box.set_margin_end(12)
        header_box.set_margin_top(6)
        header_box.set_margin_bottom(6)

        # New file button
        new_file_btn = Gtk.Button()
        new_file_btn.set_icon_name("document-new-symbolic")
        new_file_btn.add_css_class("flat")
        new_file_btn.set_tooltip_text("New file")
        new_file_btn.connect("clicked", self._on_new_file_clicked)
        header_box.append(new_file_btn)

        # New folder button
        new_folder_btn = Gtk.Button()
        new_folder_btn.set_icon_name("folder-new-symbolic")
        new_folder_btn.add_css_class("flat")
        new_folder_btn.set_tooltip_text("New folder")
        new_folder_btn.connect("clicked", self._on_new_folder_clicked)
        header_box.append(new_folder_btn)

        # Delete button
        self.delete_btn = Gtk.Button()
        self.delete_btn.set_icon_name("user-trash-symbolic")
        self.delete_btn.add_css_class("flat")
        self.delete_btn.set_tooltip_text("Delete selected")
        self.delete_btn.set_sensitive(False)
        self.delete_btn.connect("clicked", self._on_delete_clicked)
        header_box.append(self.delete_btn)

        # Spacer for alignment
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        header_box.append(spacer)

        # Show ignored files toggle (files from .gitignore)
        self.show_ignored_btn = Gtk.ToggleButton()
        self.show_ignored_btn.set_icon_name("view-reveal-symbolic")
        self.show_ignored_btn.add_css_class("flat")
        self.show_ignored_btn.set_tooltip_text("Show files ignored by .gitignore")
        self.show_ignored_btn.connect("toggled", self._on_show_ignored_toggled)
        header_box.append(self.show_ignored_btn)

        # Refresh button
        refresh_btn = Gtk.Button()
        refresh_btn.set_icon_name("view-refresh-symbolic")
        refresh_btn.add_css_class("flat")
        refresh_btn.set_tooltip_text("Refresh files")
        refresh_btn.connect("clicked", self._on_refresh_files_clicked)
        header_box.append(refresh_btn)

        box.append(header_box)

        # Unified search (files + content)
        self.unified_search = UnifiedSearch(str(self.project_path))
        self.unified_search.connect("open-file-at-line", self._on_search_open_file_at_line)
        self.unified_search.connect("open-file", self._on_search_open_file)
        box.append(self.unified_search)

        # File tree
        self.file_tree = FileTree(str(self.project_path), self.file_monitor_service)
        self.file_tree.connect("file-activated", self._on_file_activated)
        self.file_tree.connect("selection-changed", self._on_file_selection_changed)
        self.file_tree.connect("rename-requested", self._on_rename_requested)
        self.file_tree.connect("delete-requested", self._on_delete_requested)
        box.append(self.file_tree)

        # Tasks panel (below file tree)
        self.tasks_panel = TasksPanel(str(self.project_path), self.file_monitor_service)
        self.tasks_panel.connect("task-run", self._on_task_run)
        box.append(self.tasks_panel)

        return box

    def _build_git_page(self) -> Gtk.Box:
        """Build the Git tab with nested Changes/History tabs."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.set_vexpand(True)

        # Nested stack for Changes/History
        self.git_stack = Gtk.Stack()
        self.git_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.git_stack.set_vexpand(True)

        # Nested stack switcher
        git_switcher = Gtk.StackSwitcher()
        git_switcher.set_stack(self.git_stack)
        git_switcher.set_margin_start(12)
        git_switcher.set_margin_end(12)
        git_switcher.set_margin_top(4)
        git_switcher.set_margin_bottom(4)
        box.append(git_switcher)

        # Changes panel (refresh is async via subprocess)
        self.git_changes_panel = GitChangesPanel(str(self.project_path), self.file_monitor_service)
        self.git_changes_panel.connect("file-clicked", self._on_git_file_clicked)
        self.git_changes_panel.connect("branch-changed", self._on_branch_changed)
        self.git_stack.add_titled(self.git_changes_panel, "changes", "Changes")

        # History panel
        self.git_history_panel = GitHistoryPanel(self.git_service, self.file_monitor_service)
        self.git_history_panel.connect("commit-view-diff", self._on_commit_view_diff)
        self.git_stack.add_titled(self.git_history_panel, "history", "History")

        box.append(self.git_stack)

        return box

    def _build_claude_page(self) -> Gtk.Box:
        """Build the Claude tab with session history."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.set_vexpand(True)

        # Claude history panel
        self.claude_history_panel = ClaudeHistoryPanel(
            self.project_path,
            self.adapter
        )
        self.claude_history_panel.connect("session-activated", self._on_session_activated)
        box.append(self.claude_history_panel)

        return box

    def _build_content(self) -> Gtk.Box:
        """Build the main content area: tab bar + vertical split (tabs / Claude pane)."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        # Tab bar - stays visible even when the tabs area is collapsed
        self.tab_bar = Adw.TabBar()
        self.tab_bar.set_autohide(False)
        box.append(self.tab_bar)

        # Collapse chevron in the tab bar (folds the tabs area down to just this bar)
        self.workspace_toggle_btn = Gtk.ToggleButton()
        self.workspace_toggle_btn.set_icon_name("go-down-symbolic")
        self.workspace_toggle_btn.set_tooltip_text("Collapse tabs area")
        self.workspace_toggle_btn.add_css_class("flat")
        self.workspace_toggle_btn.connect("toggled", self._on_workspace_toggle)
        self.tab_bar.set_end_action_widget(self.workspace_toggle_btn)

        # Tab view
        self.tab_view = Adw.TabView()
        self.tab_view.set_vexpand(True)
        self.tab_view.connect("close-page", self._on_tab_close_requested)
        self.tab_view.connect("page-detached", self._on_page_detached)
        # Selecting/opening any tab reveals the tabs area if it was collapsed
        self.tab_view.connect("notify::selected-page", self._on_selected_page_changed)
        self.tab_bar.set_view(self.tab_view)

        # Vertical split: tabs (top) / persistent Claude pane (bottom)
        self.content_vpaned = Gtk.Paned(orientation=Gtk.Orientation.VERTICAL)
        self.content_vpaned.set_shrink_start_child(True)   # tabs area can collapse to 0
        self.content_vpaned.set_shrink_end_child(False)    # Claude keeps its min height
        self.content_vpaned.set_resize_start_child(True)   # tabs take extra space on resize
        self.content_vpaned.set_resize_end_child(False)    # Claude keeps ~its size on resize
        self.content_vpaned.set_start_child(self.tab_view)
        self.content_vpaned.set_end_child(self._build_claude_pane())
        self.content_vpaned.set_vexpand(True)
        box.append(self.content_vpaned)

        # Restore split position + collapsed state
        self._workspace_split_position = self.settings.get("window.workspace_split_position", 260)
        self.content_vpaned.set_position(self._workspace_split_position)
        self.content_vpaned.connect("notify::position", self._on_workspace_split_changed)
        if self.settings.get("window.workspace_collapsed", False):
            self.collapse_workspace()

        return box

    def _build_claude_pane(self) -> Gtk.Box:
        """Build the persistent bottom Claude pane (the CLI starts lazily)."""
        self.claude_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.claude_container.set_size_request(-1, 220)  # minimum height, always visible
        self._show_claude_placeholder()
        return self.claude_container

    def _clear_claude_container(self):
        """Remove all children from the Claude pane container."""
        child = self.claude_container.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.claude_container.remove(child)
            child = nxt

    def _show_claude_placeholder(self):
        """Show the 'Start' placeholder in the Claude pane (no CLI running)."""
        self._clear_claude_container()

        placeholder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        placeholder.set_valign(Gtk.Align.CENTER)
        placeholder.set_halign(Gtk.Align.CENTER)
        placeholder.set_vexpand(True)

        # A live tmux session means we'll re-attach rather than launch fresh —
        # reflect that in the button so the user knows the session survived.
        live = shutil.which("tmux") and self._tmux_has_session(
            self._claude_session_name()
        )
        label = f"Reconnect to {self.adapter.name}" if live else f"Start {self.adapter.name}"

        start_btn = Gtk.Button(label=label)
        start_btn.add_css_class("pill")
        start_btn.add_css_class("suggested-action")
        start_btn.connect("clicked", lambda _b: self._start_claude_session())
        placeholder.append(start_btn)

        if live:
            hint = Gtk.Label(label="A previous session is still running")
            hint.add_css_class("dim-label")
            hint.add_css_class("caption")
            hint.set_margin_top(8)
            placeholder.append(hint)

        self.claude_container.append(placeholder)

    def _claude_session_name(self) -> str:
        """Deterministic, machine-local tmux session name for this project."""
        return claude_session.session_name(str(self.project_path))

    @staticmethod
    def _tmux(*args: str, timeout: float = 5) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["tmux", *args], capture_output=True, text=True, timeout=timeout
        )

    def _tmux_has_session(self, name: str) -> bool:
        try:
            return self._tmux("has-session", "-t", f"={name}").returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False

    def _tmux_show_env(self, name: str, var: str) -> str | None:
        """Read one var from the tmux session environment, or None."""
        try:
            result = self._tmux("show-environment", "-t", f"={name}", var)
        except (OSError, subprocess.SubprocessError):
            return None
        line = result.stdout.strip()
        # Format is "VAR=value"; a leading "-VAR" means the var is unset.
        if result.returncode != 0 or "=" not in line or line.startswith("-"):
            return None
        return line.split("=", 1)[1]

    def _start_claude_session(self):
        """Start the AI CLI in the persistent Claude pane (or focus it if running).

        With tmux available the CLI runs inside a per-project tmux session that
        survives window restarts: a fresh window creates the session, a reopened
        window re-attaches. Without tmux we fall back to a plain, non-persistent
        session (the pre-supervisor behavior).
        """
        if self.claude_terminal is not None:
            self.claude_terminal.terminal.grab_focus()
            return

        # Opening the pane = the user is attending this session; clear any
        # pending "needs attention" marker so the PM dot drops back to green.
        session_notify.clear_marker(self._claude_session_name())

        if shutil.which("tmux") is None:
            self._start_claude_plain()
            return

        name = self._claude_session_name()
        if self._tmux_has_session(name):
            self._start_claude_attached(name)
        else:
            self._start_claude_fresh(name)

    def _mount_claude_pane(self, terminal: TerminalView):
        """Mount a Claude terminal + query editor in the pane.

        Snippets live in the query editor's always-visible header; a click lands
        in the editor when it is expanded, otherwise 'snippet-to-terminal' routes
        it to the terminal here.
        """
        self._clear_claude_container()

        terminal.set_vexpand(True)
        self.claude_container.append(terminal)

        query_editor = QueryEditor()
        query_editor.connect("send-requested", self._on_query_send)
        query_editor.connect("make-issue-requested", self._on_query_make_issue)
        query_editor.connect("snippet-to-terminal", self._on_snippet_clicked)
        self.claude_container.append(query_editor)

        terminal.connect("child-exited", self._on_claude_exited)

        self.claude_terminal = terminal
        terminal.terminal.grab_focus()

    def _claude_cli_command(self, mcp_env: dict | None) -> str:
        """The CLI launch string, with --mcp-config and --settings flags."""
        cli_command = self.adapter.cli_command
        if mcp_env is not None:
            cli_command = (
                f"{cli_command} --strict-mcp-config "
                f"--mcp-config {GLib.shell_quote(self._mcp_config_path)}"
            )
        settings_path = self._write_notify_settings()
        if settings_path:
            cli_command = f"{cli_command} --settings {GLib.shell_quote(settings_path)}"
        prompt = self._worktree_system_prompt()
        if prompt:
            cli_command = f"{cli_command} --append-system-prompt {GLib.shell_quote(prompt)}"
        return cli_command

    def _worktree_system_prompt(self) -> str | None:
        """The completion protocol appended for a worktree session (Stage 5).

        Tells the agent to self-verify, review, commit, and report to the parent
        via MCP — and never to merge from the worktree. None for a normal project.
        """
        if not getattr(self, "_is_worktree", False):
            return None
        parent = getattr(self, "_worktree_parent", None)
        parent_name = parent.name if parent else "the parent project"
        branch = self.git_service.get_branch_name() if self._is_git_repo else "this branch"
        return (
            f"You are working in a git worktree of “{parent_name}” on branch "
            f"“{branch}”. When the task is complete: (1) run the feature and "
            f"confirm it actually works; (2) run /code-review on your branch; "
            f"(3) commit your work (the merge preview only sees committed state); "
            f"(4) call the report_worktree_complete MCP tool with a short summary, the "
            f"review findings, and the test status. Do NOT merge or switch branches "
            f"from this worktree — the parent project integrates your branch."
        )

    def _write_notify_settings(self) -> str | None:
        """Write a temp --settings file with a Notification hook that marks this
        session as needing attention. Returns the path, or None when disabled."""
        if not self.settings.get("sessions.notifications", True):
            return None
        try:
            payload = session_notify.hook_settings(self._claude_session_name())
            fd, path = tempfile.mkstemp(prefix="cc_notify_", suffix=".json")
            os.write(fd, json.dumps(payload).encode())
            os.close(fd)
            self._notify_settings_path = path
            return path
        except OSError:
            return None

    def _start_claude_plain(self):
        """Fallback when tmux is unavailable: a plain, non-persistent session."""
        mcp_env = self._start_mcp_server()
        terminal = TerminalView(
            working_directory=str(self.project_path),
            run_command=self._claude_cli_command(mcp_env),
            env=mcp_env,
        )
        self._mount_claude_pane(terminal)

    def _start_claude_fresh(self, name: str):
        """Start the CLI in a new tmux session (survives window restart).

        The stable (port, token) is injected into the tmux session environment
        via ``-e`` so a reopened window can recover it with ``show-environment``.
        """
        mcp_env = self._start_mcp_server()
        cli_command = self._claude_cli_command(mcp_env)

        tmux_argv = ["tmux", "-f", str(_TMUX_CONF), "new-session", "-s", name]
        if mcp_env is not None:
            tmux_argv += [
                "-e", f"CC_MCP_PORT={mcp_env['CC_MCP_PORT']}",
                "-e", f"CC_MCP_TOKEN={mcp_env['CC_MCP_TOKEN']}",
            ]
        tmux_argv.append(cli_command)  # session command, run by tmux via `sh -c`

        terminal = TerminalView(
            working_directory=str(self.project_path),
            argv=tmux_argv,
        )
        self._mount_claude_pane(terminal)

    def _start_claude_attached(self, name: str):
        """Re-attach to a running tmux session; re-bind the same MCP endpoint.

        The CLI already read its --mcp-config once at launch, so we only need to
        re-bind the MCP server to the (port, token) recovered from the session
        env — no new config file is written.
        """
        port = self._tmux_show_env(name, "CC_MCP_PORT")
        token = self._tmux_show_env(name, "CC_MCP_TOKEN")
        if port and token and port.isdigit():
            self._start_mcp_server(port=int(port), token=token)

        tmux_argv = ["tmux", "-f", str(_TMUX_CONF), "attach", "-t", f"={name}"]
        terminal = TerminalView(
            working_directory=str(self.project_path),
            argv=tmux_argv,
        )
        self._mount_claude_pane(terminal)

    @staticmethod
    def _port_is_free(port: int) -> bool:
        """True if ``port`` can be bound on loopback right now.

        SO_REUSEADDR mirrors how uvicorn binds (it sets the same flag), so a
        just-freed port lingering in TIME_WAIT after the previous window closed
        reads as free instead of a false "busy".
        """
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                probe.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False

    def _start_mcp_server(
        self, port: int | None = None, token: str | None = None
    ) -> dict | None:
        """Start the per-window MCP server; return env vars for the CLI, or None.

        Fresh session (no args): allocate a free port + new token and write the
        temp ``--strict-mcp-config``. Re-attach (``port`` + ``token`` given):
        re-bind the *same* stable endpoint an existing tmux session already
        points at — no config is written, since the running CLI already read it.

        Returns a ``{CC_MCP_PORT, CC_MCP_TOKEN}`` dict, or None when MCP is
        disabled or startup failed (caller then launches/attaches bare). On
        success sets ``self.mcp_server`` (and ``self._mcp_config_path`` when fresh).
        """
        if not self.settings.get("mcp.enabled", True):
            return None
        rebind = port is not None and token is not None
        try:
            if rebind:
                # Confirm the stable port is still free before re-binding; else
                # uvicorn's bind would fail silently on its own thread. Degraded
                # (tool errors, not a hang) until the session is restarted.
                if not self._port_is_free(port):
                    ToastService.show_error(
                        f"MCP port {port} is busy; Claude tools are disabled for this "
                        "session — restart it (exit Claude, then Start) to restore them"
                    )
                    return None
            else:
                # Deterministic, reservation-aware port in a non-ephemeral range,
                # so a normal window restart re-binds the same port (roadmap 3.10+
                # supervisor port-reservation). Avoid ports owned by other live
                # cc- sessions (detached ones still hold theirs).
                name = self._claude_session_name()
                reserved = claude_session.reserved_ports(exclude=name)
                port = claude_session.pick_stable_port(name, reserved, self._port_is_free)
                if port is None:
                    ToastService.show_error(
                        "No free MCP port available; Claude tools disabled this session"
                    )
                    return None
                token = secrets.token_urlsafe(32)

            server = McpServer(self)
            server.start(port, token)
            self.mcp_server = server

            if not rebind:
                # Temp --strict-mcp-config referencing the secret by env var, so
                # the plaintext token never lands on disk.
                config = {
                    "mcpServers": {
                        "code-companion": {
                            "type": "http",
                            "url": "http://127.0.0.1:${CC_MCP_PORT}/mcp",
                            "headers": {"Authorization": "Bearer ${CC_MCP_TOKEN}"},
                        }
                    }
                }
                fd, config_path = tempfile.mkstemp(prefix="cc_mcp_", suffix=".json")
                os.write(fd, json.dumps(config).encode())
                os.close(fd)
                self._mcp_config_path = config_path

            return {"CC_MCP_PORT": str(port), "CC_MCP_TOKEN": token}
        except Exception as exc:  # noqa: BLE001 - MCP is optional; fall back to bare CLI
            ToastService.show_error(f"MCP server failed to start: {exc}")
            self._stop_mcp_server()
            return None

    def _stop_mcp_server(self) -> None:
        """Stop the MCP server and delete its temp config (idempotent)."""
        if getattr(self, "mcp_server", None) is not None:
            self.mcp_server.stop()
            self.mcp_server = None
        if getattr(self, "_mcp_config_path", None):
            try:
                os.unlink(self._mcp_config_path)
            except OSError:
                pass
            self._mcp_config_path = None
        if getattr(self, "_notify_settings_path", None):
            try:
                os.unlink(self._notify_settings_path)
            except OSError:
                pass
            self._notify_settings_path = None

    # --- Workspace collapse/expand (also the future MCP tool surface) ---

    def _on_workspace_toggle(self, button):
        """Chevron toggled: collapse or expand the tabs area."""
        if button.get_active():
            self.collapse_workspace()
        else:
            self.expand_workspace()

    def _sync_workspace_toggle(self):
        """Reflect collapsed state into the chevron without re-triggering it."""
        btn = self.workspace_toggle_btn
        btn.handler_block_by_func(self._on_workspace_toggle)
        btn.set_active(self._workspace_collapsed)
        btn.set_icon_name("go-up-symbolic" if self._workspace_collapsed else "go-down-symbolic")
        btn.set_tooltip_text("Expand tabs area" if self._workspace_collapsed else "Collapse tabs area")
        btn.handler_unblock_by_func(self._on_workspace_toggle)

    def collapse_workspace(self):
        """Collapse the tabs area down to just the tab bar; Claude fills the height."""
        if self._workspace_collapsed:
            return
        position = self.content_vpaned.get_position()
        if position > 60:
            self._workspace_split_position = position
        self._workspace_collapsed = True
        self.tab_view.set_visible(False)
        self._sync_workspace_toggle()
        self.settings.set("window.workspace_collapsed", True)

    def expand_workspace(self):
        """Restore the tabs area to its saved split position."""
        if not self._workspace_collapsed:
            return
        self._workspace_collapsed = False
        self.tab_view.set_visible(True)
        self.content_vpaned.set_position(self._workspace_split_position)
        self._sync_workspace_toggle()
        self.settings.set("window.workspace_collapsed", False)

    def toggle_workspace(self):
        """Toggle the tabs area collapsed/expanded."""
        if self._workspace_collapsed:
            self.expand_workspace()
        else:
            self.collapse_workspace()

    def _on_selected_page_changed(self, tab_view, pspec):
        """Opening/selecting any tab reveals the tabs area if it was collapsed."""
        if self._workspace_collapsed and tab_view.get_selected_page() is not None:
            self.expand_workspace()

    def _on_workspace_split_changed(self, paned, pspec):
        """Persist the tabs/Claude split position."""
        if self._workspace_collapsed:
            return
        position = paned.get_position()
        if position > 60:
            self._workspace_split_position = position
            self.settings.set("window.workspace_split_position", position)

    def _load_project(self):
        """Load project data and create initial tabs."""
        # Subscribe to file changes to update open diff views
        self.file_monitor_service.connect("working-tree-changed", self._on_working_tree_changed)
        self.file_monitor_service.connect("git-status-changed", self._on_git_status_changed_for_diff)

        # Git badge updates
        if self._is_git_repo:
            self.file_monitor_service.connect("git-status-changed", lambda _s: self._update_git_badge())
            self.file_monitor_service.connect("git-history-changed", lambda _s: self._update_git_badge())
            self._update_git_badge()  # Initial badge

        # Issues badge (only meaningful for GitHub repos)
        if self.issues_service is not None and self.issues_service.is_github_repo():
            self._update_issues_badge()  # Initial badge

        # Messages badge (only for projects with a remote identity)
        if self._project_remote is not None:
            self._update_messages_badge()  # Initial badge

    def _on_session_activated(self, panel, session: Session):
        """Handle session activation - show in main area, reuse single tab."""
        # Reuse existing session detail view if available
        if self.session_detail_view is not None and self.session_detail_page is not None:
            # Update existing view with new session
            self.session_detail_view.load_session(session)
            self.session_detail_page.set_title(f"Session: {session.display_date}")
            self.tab_view.set_selected_page(self.session_detail_page)
            return

        # Create new session detail view (with git access for the Changes section)
        self.session_detail_view = SessionView(
            self.adapter,
            project_path=self.project_path,
            git_service=self.git_service if self._is_git_repo else None,
        )
        self.session_detail_view.connect("commit-selected", self._on_session_commit_selected)
        self.session_detail_view.connect("show-diff", self._on_session_show_diff)
        self.session_detail_view.load_session(session)

        # Add tab
        self.session_detail_page = self.tab_view.append(self.session_detail_view)
        self.session_detail_page.set_title(f"Session: {session.display_date}")
        self.session_detail_page.set_icon(Gio.ThemedIcon.new("document-open-recent-symbolic"))

        self.tab_view.set_selected_page(self.session_detail_page)

    def _on_sidebar_toggled(self, button):
        """Toggle sidebar visibility."""
        if button.get_active():
            # Restore sidebar from saved position or settings
            self.sidebar.set_visible(True)
            default_width = self.settings.get("window.sidebar_width", 370)
            self.paned.set_position(self._saved_pane_position if hasattr(self, '_saved_pane_position') else default_width)
        else:
            # Hide sidebar
            self._saved_pane_position = self.paned.get_position()
            self.sidebar.set_visible(False)

    def _on_claude_clicked(self, button):
        """Header button: start the Claude session, or focus it if already running."""
        self._start_claude_session()

    def _on_snippet_clicked(self, sender, text: str):
        """Feed snippet text into the Claude terminal (collapsed-editor path)."""
        if self.claude_terminal:
            self.claude_terminal.terminal.feed_child(text.encode("utf-8"))
            self.claude_terminal.terminal.grab_focus()

    def _on_query_send(self, query_editor, text: str):
        """Handle query editor send - paste text into Claude terminal."""
        if self.claude_terminal and text.strip():
            # Send text to terminal
            self.claude_terminal.terminal.feed_child(text.encode("utf-8"))
            # Send Enter separately after short delay
            GLib.timeout_add(50, self._send_enter_to_terminal)

    def _on_query_make_issue(self, query_editor, text: str):
        """Ask Claude to format the editor text as a GitHub issue and create it."""
        if not self.claude_terminal or not text.strip():
            return
        prompt = self._build_make_issue_prompt(text)
        self.claude_terminal.terminal.feed_child(prompt.encode("utf-8"))
        GLib.timeout_add(50, self._send_enter_to_terminal)
        query_editor.clear()

    def _build_make_issue_prompt(self, text: str) -> str:
        """Build the prompt that asks Claude to create a GitHub issue from text."""
        return (
            "Please turn the following into a well-formed GitHub issue and create it "
            "for this repository with `gh issue create` (write a clear title and a "
            "structured body). Write the issue in English unless the text below is "
            "clearly meant to be in another language. Reply with the new issue "
            "number and URL.\n\n"
            f"---\n{text}"
        )

    def _send_enter_to_terminal(self):
        """Send Enter key to terminal."""
        if self.claude_terminal:
            self.claude_terminal.terminal.feed_child(b"\n")
            self.claude_terminal.terminal.grab_focus()
        return False  # Don't repeat

    def _on_claude_exited(self, terminal, status):
        """The Claude pane's PTY child exited.

        Under tmux this fires both when the CLI truly exits (session gone) and
        when we merely detach — window closing or the tmux client dying — while
        the session keeps running. Only tear down on a real exit; a live session
        must survive so a reopened window can re-attach.
        """
        if shutil.which("tmux") and self._tmux_has_session(self._claude_session_name()):
            # Detached, session still alive: drop the widget, keep MCP + session.
            self.claude_terminal = None
            return

        # Real exit — server lifecycle == session lifecycle: stop it, free the
        # port, delete the temp --strict-mcp-config, restore the Start placeholder.
        self._stop_mcp_server()
        self.claude_terminal = None
        self._show_claude_placeholder()

    def _on_new_terminal_clicked(self, button):
        """Create new terminal tab."""
        terminal = TerminalView(working_directory=str(self.project_path))

        # Count existing terminal tabs for naming
        terminal_count = 1
        for i in range(self.tab_view.get_n_pages()):
            page = self.tab_view.get_nth_page(i)
            if page.get_title().startswith("Terminal"):
                terminal_count += 1

        page = self.tab_view.append(terminal)
        page.set_title(f"Terminal {terminal_count}")
        page.set_icon(Gio.ThemedIcon.new("utilities-terminal-symbolic"))

        self.tab_view.set_selected_page(page)

    def _on_external_terminal_clicked(self, button):
        """Open system terminal."""
        terminal = TerminalView()
        terminal.current_directory = str(self.project_path)
        terminal.open_system_terminal()

    def _on_preferences_clicked(self, button):
        """Show preferences dialog."""
        dialog = PreferencesDialog()
        dialog.present(self)

    def _on_projects_clicked(self, button):
        """Open or activate Project Manager window."""
        from .services.project_lock import ManagerLock

        # Try to activate existing Project Manager
        if ManagerLock.activate_existing():
            return

        # No existing Project Manager, start a new one
        import subprocess
        import sys

        subprocess.Popen(
            [sys.executable, "-m", "src.main"],
            start_new_session=True,
        )

    def _on_refresh_files_clicked(self, button):
        """Refresh file tree."""
        self.file_tree.refresh()

    def _on_show_ignored_toggled(self, button):
        """Toggle showing ignored files in file tree."""
        self.file_tree.show_ignored = button.get_active()
        if button.get_active():
            ToastService.show("Showing ignored files")
        else:
            ToastService.show("Hiding ignored files")

    def _on_new_file_clicked(self, button):
        """Create a new file."""
        self._show_create_dialog("file")

    def _on_new_folder_clicked(self, button):
        """Create a new folder."""
        self._show_create_dialog("folder")

    def _show_create_dialog(self, item_type: str):
        """Show dialog to create a new file or folder."""
        # Get selected directory or use project root
        selected_dir = self._get_selected_directory()

        dialog = Adw.AlertDialog()
        dialog.set_heading(f"New {item_type.title()}")
        dialog.set_body(f"Create in: {selected_dir.relative_to(self.project_path) if selected_dir != self.project_path else '.'}")

        # Add entry for name in a box
        entry = Gtk.Entry()
        entry.set_placeholder_text(f"Enter {item_type} name")
        entry.set_hexpand(True)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.set_margin_start(12)
        box.set_margin_end(12)
        box.append(entry)

        dialog.set_extra_child(box)

        dialog.add_response("cancel", "Cancel")
        dialog.add_response("create", "Create")
        dialog.set_response_appearance("create", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("create")

        dialog.connect("response", self._on_create_dialog_response, entry, selected_dir, item_type)
        dialog.present(self)

    def _get_selected_directory(self) -> Path:
        """Get the selected directory in file tree, or project root."""
        selected_paths = self.file_tree.get_selected_paths()
        if selected_paths:
            path = selected_paths[0]
            if path.is_dir():
                return path
            else:
                return path.parent
        return self.project_path

    def _on_create_dialog_response(self, dialog, response, entry, parent_dir, item_type):
        """Handle create dialog response."""
        if response != "create":
            return

        name = entry.get_text().strip()
        if not name:
            ToastService.show_error("Name cannot be empty")
            return

        # Validate name
        if "/" in name or "\\" in name:
            ToastService.show_error("Name cannot contain slashes")
            return

        new_path = parent_dir / name

        if new_path.exists():
            ToastService.show_error(f"{item_type.title()} already exists")
            return

        try:
            if item_type == "folder":
                new_path.mkdir(parents=True)
                ToastService.show(f"Created folder: {name}")
            else:
                new_path.parent.mkdir(parents=True, exist_ok=True)
                new_path.touch()
                ToastService.show(f"Created file: {name}")
                # Open the new file
                self._on_file_activated(self.file_tree, str(new_path))
        except OSError as e:
            ToastService.show_error(f"Failed to create {item_type}: {e}")

    def _on_file_selection_changed(self, file_tree, has_selection: bool):
        """Handle file tree selection change."""
        self.delete_btn.set_sensitive(has_selection)

    def _on_delete_clicked(self, button):
        """Delete selected file or folder."""
        selected_paths = self.file_tree.get_selected_paths()
        if not selected_paths:
            return

        path = selected_paths[0]
        item_type = "folder" if path.is_dir() else "file"
        relative_path = path.relative_to(self.project_path) if path.is_relative_to(self.project_path) else path

        dialog = Adw.AlertDialog()
        dialog.set_heading(f"Delete {item_type}?")

        if path.is_dir():
            dialog.set_body(f"Are you sure you want to delete the folder '{relative_path}' and all its contents?\n\nThis action cannot be undone.")
        else:
            dialog.set_body(f"Are you sure you want to delete '{relative_path}'?\n\nThis action cannot be undone.")

        dialog.add_response("cancel", "Cancel")
        dialog.add_response("delete", "Delete")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")

        dialog.connect("response", self._on_delete_dialog_response, path)
        dialog.present(self)

    def _on_delete_dialog_response(self, dialog, response, path: Path):
        """Handle delete dialog response."""
        if response != "delete":
            return

        import shutil

        try:
            if path.is_dir():
                shutil.rmtree(path)
                ToastService.show(f"Deleted folder: {path.name}")
            else:
                path.unlink()
                ToastService.show(f"Deleted: {path.name}")

            # Close any tabs with the deleted file (without a save prompt)
            self._force_close_tabs_for_path(path)

        except OSError as e:
            ToastService.show_error(f"Failed to delete: {e}")

    def _force_close_tabs_for_path(self, target_path: Path):
        """Close tabs referencing target_path WITHOUT prompting to save (roadmap 1.8).

        Clearing the modified flag first prevents the tab-close handler from popping
        a 'Save?' dialog that would recreate the just-deleted file.
        """
        pages_to_close = []
        for i in range(self.tab_view.get_n_pages()):
            page = self.tab_view.get_nth_page(i)
            child = page.get_child()
            if hasattr(child, "file_path"):
                child_path = Path(child.file_path)
                if child_path == target_path or target_path in child_path.parents:
                    if isinstance(child, (FileEditor, SvgEditor)):
                        child._modified = False
                        child.buffer.set_modified(False)
                    pages_to_close.append(page)

        for page in pages_to_close:
            self.tab_view.close_page(page)

    def _repoint_tabs(self, old_path: Path, new_path: Path):
        """Repoint open tabs from old_path to new_path in place after a rename (roadmap 1.8)."""
        for i in range(self.tab_view.get_n_pages()):
            page = self.tab_view.get_nth_page(i)
            child = page.get_child()
            if not hasattr(child, "file_path"):
                continue
            child_path = Path(child.file_path)
            if child_path == old_path:
                new_child = new_path
            elif old_path in child_path.parents:
                new_child = new_path / child_path.relative_to(old_path)
            else:
                continue

            if hasattr(child, "set_file_path"):
                child.set_file_path(str(new_child))
            else:
                child.file_path = str(new_child)  # ImageViewer / BinaryFileView

            page.set_title(new_child.name)
            page.set_tooltip(str(new_child))
            gicon = IconCache().get_file_gicon(new_child)
            page.set_icon(gicon or Gio.ThemedIcon.new("text-x-generic-symbolic"))

    def _on_rename_requested(self, file_tree, old_path_str: str, new_name: str):
        """Handle rename request from file tree."""
        old_path = Path(old_path_str)
        new_path = old_path.parent / new_name

        # Validate
        if new_path.exists():
            ToastService.show_error(f"'{new_name}' already exists")
            return

        # Rename on disk first, then repoint any open tabs in place (keeping edits).
        try:
            old_path.rename(new_path)
        except OSError as e:
            ToastService.show_error(f"Failed to rename: {e}")
            return

        self._repoint_tabs(old_path, new_path)
        item_type = "Folder" if new_path.is_dir() else "File"
        ToastService.show(f"{item_type} renamed to '{new_name}'")

    def _on_delete_requested(self, file_tree, paths: list):
        """Handle delete request from file tree (via context menu or Delete key)."""
        if not paths:
            return

        # Convert to Path objects
        path_objects = [Path(p) for p in paths]

        # Build confirmation message
        if len(path_objects) == 1:
            path = path_objects[0]
            item_type = "folder" if path.is_dir() else "file"
            relative_path = path.relative_to(self.project_path) if path.is_relative_to(self.project_path) else path

            if path.is_dir():
                body = f"Are you sure you want to delete the folder '{relative_path}' and all its contents?\n\nThis action cannot be undone."
            else:
                body = f"Are you sure you want to delete '{relative_path}'?\n\nThis action cannot be undone."
            heading = f"Delete {item_type}?"
        else:
            # Multiple items
            folder_count = sum(1 for p in path_objects if p.is_dir())
            file_count = len(path_objects) - folder_count

            parts = []
            if file_count > 0:
                parts.append(f"{file_count} file{'s' if file_count > 1 else ''}")
            if folder_count > 0:
                parts.append(f"{folder_count} folder{'s' if folder_count > 1 else ''}")

            heading = f"Delete {len(path_objects)} items?"
            body = f"Are you sure you want to delete {' and '.join(parts)}?\n\nThis action cannot be undone."

        dialog = Adw.AlertDialog()
        dialog.set_heading(heading)
        dialog.set_body(body)

        dialog.add_response("cancel", "Cancel")
        dialog.add_response("delete", "Delete")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")

        dialog.connect("response", self._on_multi_delete_response, path_objects)
        dialog.present(self)

    def _on_multi_delete_response(self, dialog, response, paths: list[Path]):
        """Handle multi-delete dialog response."""
        if response != "delete":
            return

        import shutil

        deleted_count = 0
        errors = []

        for path in paths:
            try:
                # Force-close tabs first (no save prompt → no file resurrection)
                self._force_close_tabs_for_path(path)

                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink()
                deleted_count += 1
            except OSError as e:
                errors.append(f"{path.name}: {e}")

        if deleted_count > 0:
            if deleted_count == 1:
                ToastService.show(f"Deleted: {paths[0].name}")
            else:
                ToastService.show(f"Deleted {deleted_count} items")

        if errors:
            ToastService.show_error(f"Failed to delete: {errors[0]}")

    def _on_git_file_clicked(self, git_panel, path: str, staged: bool):
        """Handle git file click - open diff view (reuses tab for same file)."""
        # If same file already open - refresh and switch to it
        if (self.git_diff_page is not None and
            self.git_diff_path == path and
            self.git_diff_staged == staged):
            # Refresh the diff content
            old_content, new_content = self.git_service.get_diff(path, staged)
            self.git_diff_view.update(old_content, new_content)
            self.tab_view.set_selected_page(self.git_diff_page)
            return

        # Get diff content
        old_content, new_content = self.git_service.get_diff(path, staged)

        status_prefix = "[staged] " if staged else ""
        title = f"{status_prefix}{Path(path).name}"

        # Close existing diff tab if open (different file)
        if self.git_diff_page is not None:
            self.tab_view.close_page(self.git_diff_page)

        # Create new diff view and tab
        self.git_diff_view = DiffView(old_content, new_content, file_path=path)
        self.git_diff_page = self.tab_view.append(self.git_diff_view)
        self.git_diff_page.set_title(title)
        self.git_diff_page.set_icon(Gio.ThemedIcon.new("document-edit-symbolic"))
        self.git_diff_page.set_tooltip(f"Diff: {path}")
        self.git_diff_path = path
        self.git_diff_staged = staged

        self.tab_view.set_selected_page(self.git_diff_page)

    def _on_branch_changed(self, git_panel):
        """Handle branch change - update window title."""
        self._update_window_title()

    def _on_working_tree_changed(self, service, path: str):
        """Handle working tree file change - update open diff view if applicable."""
        # Check if we have an open diff view for an unstaged file
        if (self.git_diff_view is not None and
            self.git_diff_path is not None and
            not self.git_diff_staged):
            # Check if the changed path matches or is related to the open diff
            # path can be empty string for general refresh signals
            if not path or path == self.git_diff_path:
                # Refresh the diff view
                old_content, new_content = self.git_service.get_diff(
                    self.git_diff_path, staged=False
                )
                self.git_diff_view.update(old_content, new_content)

    def _on_git_status_changed_for_diff(self, service):
        """Handle git status change (stage/unstage) - update open diff view."""
        # Check if we have an open staged diff view
        if (self.git_diff_view is not None and
            self.git_diff_path is not None and
            self.git_diff_staged):
            # Refresh the staged diff view
            old_content, new_content = self.git_service.get_diff(
                self.git_diff_path, staged=True
            )
            self.git_diff_view.update(old_content, new_content)

    def _on_commit_view_diff(self, history_panel, commit_hash: str):
        """Handle commit diff view request - reuse single commit tab."""
        # Get commit info
        commit = self.git_service.get_commit(commit_hash)
        if not commit:
            ToastService.show_error("Commit not found")
            return

        # Reuse existing commit detail view if available
        if self.commit_detail_view is not None and self.commit_detail_page is not None:
            # Update existing view with new commit
            self.commit_detail_view.update_commit(commit)
            self.commit_detail_page.set_title(f"Commit {commit.short_hash}")
            self.tab_view.set_selected_page(self.commit_detail_page)
            return

        # Create new commit detail view
        self.commit_detail_view = CommitDetailView(self.git_service, commit)

        # Add tab
        self.commit_detail_page = self.tab_view.append(self.commit_detail_view)
        self.commit_detail_page.set_title(f"Commit {commit.short_hash}")
        self.commit_detail_page.set_icon(Gio.ThemedIcon.new("emblem-documents-symbolic"))

        self.tab_view.set_selected_page(self.commit_detail_page)

    def _on_session_commit_selected(self, session_view, commit_hash: str):
        """A commit in a session's Changes section was clicked — open its detail."""
        self._on_commit_view_diff(None, commit_hash)

    def _on_session_show_diff(self, session_view, title: str, raw_diff: str):
        """Show a session's aggregate diff in a single reused diff tab."""
        if self.session_diff_page is not None:
            self.tab_view.close_page(self.session_diff_page)
            self.session_diff_page = None
            self.session_diff_view = None

        self.session_diff_view = DiffView("", "", file_path=None, raw_diff=raw_diff)
        self.session_diff_page = self.tab_view.append(self.session_diff_view)
        self.session_diff_page.set_title(title)
        self.session_diff_page.set_icon(Gio.ThemedIcon.new("emblem-documents-symbolic"))
        self.tab_view.set_selected_page(self.session_diff_page)

    def _on_task_run(self, tasks_panel, label: str, command: str):
        """Handle task run - create terminal and execute command."""
        terminal = TerminalView(
            working_directory=str(self.project_path),
            run_command=command
        )

        page = self.tab_view.append(terminal)
        page.set_title(f"Task: {label}")
        page.set_icon(Gio.ThemedIcon.new("media-playback-start-symbolic"))

        self.tab_view.set_selected_page(page)

    def _canonical_path(self, file_path: str) -> str:
        """Canonical form used for tab dedup.

        Paths reach us from the tree, search, notes and (future) the MCP
        open_file tool in different forms — symlinked, relative, with ``..``
        segments. Resolving them means one underlying file maps to exactly one
        tab, so we never open a duplicate tab with a divergent buffer (which
        would clobber on save).
        """
        try:
            return str(Path(file_path).resolve())
        except OSError:
            return str(file_path)

    def _on_file_activated(self, file_tree, file_path: str):
        """Handle file activation from tree - open in tab."""
        file_path = self._canonical_path(file_path)
        # Check if file is already open
        for i in range(self.tab_view.get_n_pages()):
            page = self.tab_view.get_nth_page(i)
            child = page.get_child()
            if hasattr(child, "file_path") and child.file_path == file_path:
                self.tab_view.set_selected_page(page)
                return

        # Route by file extension
        ext = Path(file_path).suffix.lower()

        try:
            if ext in ImageViewer.EXTENSIONS:
                widget = ImageViewer(file_path)
            elif ext == ".svg":
                widget = SvgEditor(file_path)
                widget.connect("modified-changed", self._on_editor_modified_changed)
            elif is_binary(file_path):
                widget = BinaryFileView(file_path)
            else:
                widget = FileEditor(file_path)
                widget.connect("modified-changed", self._on_editor_modified_changed)
                widget.connect("run-requested", self._on_run_requested)
        except Exception as e:
            ToastService.show_error(f"Error opening file: {e}")
            return

        # Add tab with appropriate file icon
        page = self.tab_view.append(widget)
        file_name = Path(file_path).name
        page.set_title(file_name)
        page.set_tooltip(file_path)

        # Use Material Design icon for the file tab
        icon_cache = IconCache()
        gicon = icon_cache.get_file_gicon(Path(file_path))
        page.set_icon(gicon or Gio.ThemedIcon.new("text-x-generic-symbolic"))

        # Store page reference in widget for later lookup
        widget._tab_page = page

        self.tab_view.set_selected_page(page)
        widget.grab_focus()

    def _on_editor_modified_changed(self, editor, is_modified):
        """Handle editor modification state change - update tab title."""
        if hasattr(editor, "_tab_page"):
            page = editor._tab_page
            file_name = Path(editor.file_path).name
            if is_modified:
                page.set_title(f"● {file_name}")
            else:
                page.set_title(file_name)

    def _on_run_requested(self, editor, file_path: str, args: str):
        """Handle run script request from editor toolbar."""
        ext = Path(file_path).suffix.lower()
        filename = Path(file_path).name

        # Build command based on file type
        if ext == ".py":
            if args:
                command = f"uv run python {file_path} {args}"
            else:
                command = f"uv run python {file_path}"
        elif ext == ".sh":
            if args:
                command = f"bash {file_path} {args}"
            else:
                command = f"bash {file_path}"
        else:
            return

        # Create terminal tab for running the script
        terminal = TerminalView(working_directory=str(self.project_path))
        page = self.tab_view.append(terminal)
        page.set_title(f"Run: {filename}")
        page.set_icon(Gio.ThemedIcon.new("media-playback-start-symbolic"))

        self.tab_view.set_selected_page(page)

        # Run command after terminal is ready
        GLib.timeout_add(100, lambda: terminal.run_command(command) or False)

    def _on_page_detached(self, tab_view, page, position):
        """Dispose editor disk monitors when their tab is removed (no leak)."""
        child = page.get_child()
        disk_sync = getattr(child, "_disk_sync", None)
        if disk_sync is not None:
            disk_sync.dispose()

    def open_text_diff(self, file_path: str, old_text: str, new_text: str, title: str):
        """Open a read-only diff of two in-memory texts in a reusable tab.

        Used by the editor's disk-change banner to show buffer-vs-disk (roadmap 1.1).
        """
        diff_view = DiffView(old_text, new_text, file_path=file_path)
        page = self.tab_view.append(diff_view)
        page.set_title(title)
        page.set_icon(Gio.ThemedIcon.new("emblem-documents-symbolic"))
        self.tab_view.set_selected_page(page)

    def _on_tab_close_requested(self, tab_view, page) -> bool:
        """Handle tab close request."""
        # Get the child widget
        child = page.get_child()

        # File editor with unsaved changes - show warning
        if isinstance(child, (FileEditor, SvgEditor)) and child.is_modified:
            dialog = Adw.AlertDialog()
            dialog.set_heading("Unsaved Changes")
            dialog.set_body(f"'{Path(child.file_path).name}' has unsaved changes. What do you want to do?")
            dialog.add_response("cancel", "Cancel")
            dialog.add_response("discard", "Discard")
            dialog.add_response("save", "Save")
            dialog.set_response_appearance("discard", Adw.ResponseAppearance.DESTRUCTIVE)
            dialog.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
            dialog.set_default_response("save")
            dialog.connect("response", self._on_unsaved_close_response, page, child)
            dialog.present(self)
            return True  # Prevent immediate close

        # Commit detail tab - clear references when closed
        if page == self.commit_detail_page:
            self.commit_detail_page = None
            self.commit_detail_view = None

        # Session detail tab - clear references when closed
        if page == self.session_detail_page:
            self.session_detail_page = None
            self.session_detail_view = None

        # Git diff tab - clear references when closed
        if page == self.git_diff_page:
            self.git_diff_page = None
            self.git_diff_view = None
            self.git_diff_path = None
            self.git_diff_staged = None

        # Session-changes diff tab - clear references when closed
        if page == getattr(self, "session_diff_page", None):
            self.session_diff_page = None
            self.session_diff_view = None

        # Problems detail tab - clear references when closed
        if page == self.problems_detail_page:
            self.problems_detail_page = None
            self.problems_detail_view = None

        # Issue detail tab - clear references when closed
        if page == self.issue_detail_page:
            self.issue_detail_page = None
            self.issue_detail_view = None

        # Terminal tab - kill child shell + its process group so dev servers
        # don't survive as orphans holding ports.
        if isinstance(child, TerminalView):
            child.cleanup()

        return False  # Allow close

    def _on_unsaved_close_response(self, dialog, response, page, editor):
        """Handle unsaved file close dialog response."""
        if response == "save":
            # Save, then close ONLY if the save actually succeeded (roadmap 1.4).
            # request_save may surface a conflict dialog; the tab stays open on
            # failure/cancel (a save-failure banner is shown by the editor).
            editor.request_save(lambda ok: self.tab_view.close_page_finish(page, ok))
        elif response == "discard":
            # Discard changes and close
            self.tab_view.close_page_finish(page, True)
        else:  # cancel
            self.tab_view.close_page_finish(page, False)

    def _on_close_request(self, window) -> bool:
        """Handle window close request - check for unsaved changes."""
        # Collect unsaved files
        unsaved_files = []
        n_pages = self.tab_view.get_n_pages()
        for i in range(n_pages):
            page = self.tab_view.get_nth_page(i)
            child = page.get_child()
            if isinstance(child, (FileEditor, SvgEditor)) and child.is_modified:
                unsaved_files.append((page, child))

        if not unsaved_files:
            return False  # Allow close

        # Show warning dialog
        file_names = [Path(editor.file_path).name for _, editor in unsaved_files]
        if len(file_names) == 1:
            body = f"'{file_names[0]}' has unsaved changes."
        else:
            body = f"{len(file_names)} files have unsaved changes:\n• " + "\n• ".join(file_names)

        dialog = Adw.AlertDialog()
        dialog.set_heading("Unsaved Changes")
        dialog.set_body(body)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("discard", "Discard All")
        dialog.add_response("save", "Save All")
        dialog.set_response_appearance("discard", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("save")
        dialog.connect("response", self._on_window_close_response, unsaved_files)
        dialog.present(self)
        return True  # Prevent close, dialog will handle it

    def _on_window_close_response(self, dialog, response, unsaved_files):
        """Handle window close dialog response."""
        if response == "cancel":
            return
        elif response == "save":
            # Save all, close ONLY if every save succeeded (roadmap 1.4).
            self._save_all_then_close(unsaved_files)
        elif response == "discard":
            # Mark files as not modified to prevent dialogs on close
            for _, editor in unsaved_files:
                editor._modified = False
                editor.buffer.set_modified(False)
            self.close()

    def _save_all_then_close(self, unsaved_files):
        """Save every unsaved editor; close the window only if all succeed."""
        state = {"pending": len(unsaved_files), "all_ok": True}

        def one_done(ok: bool):
            state["pending"] -= 1
            if not ok:
                state["all_ok"] = False
            if state["pending"] == 0 and state["all_ok"]:
                self.close()
            # On any failure the window stays open; the failing editor(s) show a
            # save-error banner so the tab isn't a silent mystery.

        for _, editor in unsaved_files:
            editor.request_save(one_done)

    def _on_destroy(self, window):
        """Clean up on window destroy."""
        # Save window size (only if not maximized and settings initialized)
        if hasattr(self, "settings") and not self.is_maximized():
            width, height = self.get_default_size()
            # get_default_size returns -1 if not set, use actual size
            if width <= 0 or height <= 0:
                width = self.get_width()
                height = self.get_height()
            self.settings.set("window.width", width)
            self.settings.set("window.height", height)

        # Kill the persistent Claude CLI child + its process group so it doesn't
        # survive as an orphan (terminal tabs are handled on tab close).
        if getattr(self, "claude_terminal", None) is not None:
            self.claude_terminal.cleanup()

        # Backstop: stop the MCP server + delete its temp config on window close /
        # crash (the normal path is _on_claude_exited).
        self._stop_mcp_server()

        # Shutdown file monitor service
        if hasattr(self, "file_monitor_service"):
            self.file_monitor_service.shutdown()

        self.lock.release()

    def _setup_shortcuts(self):
        """Setup keyboard shortcuts using proper GTK4 approach."""
        # Create shortcut controller with LOCAL scope
        # LOCAL means shortcuts only work when this window has focus (not in dialogs)
        shortcut_controller = Gtk.ShortcutController()
        shortcut_controller.set_scope(Gtk.ShortcutScope.LOCAL)

        # Ctrl+P - File search
        shortcut_controller.add_shortcut(Gtk.Shortcut(
            trigger=Gtk.ShortcutTrigger.parse_string("<Control>p"),
            action=Gtk.CallbackAction.new(lambda *args: self._show_file_search() or True)
        ))

        # Ctrl+Shift+F - Switch to search tab and focus
        shortcut_controller.add_shortcut(Gtk.Shortcut(
            trigger=Gtk.ShortcutTrigger.parse_string("<Control><Shift>f"),
            action=Gtk.CallbackAction.new(lambda *args: self._focus_search() or True)
        ))

        # Ctrl+W - Close current tab (with autosave)
        shortcut_controller.add_shortcut(Gtk.Shortcut(
            trigger=Gtk.ShortcutTrigger.parse_string("<Control>w"),
            action=Gtk.CallbackAction.new(lambda *args: self._close_current_tab() or True)
        ))

        self.add_controller(shortcut_controller)

    def _show_file_search(self):
        """Show file search dialog."""
        # Collect file list from file tree
        file_list = self._collect_file_list()

        # Create and present dialog
        dialog = FileSearchDialog(self.project_path, file_list)
        dialog.connect("file-selected", self._on_file_search_selected)
        dialog.present_dialog(self)

    def _collect_file_list(self) -> list[Path]:
        """Collect all files from project, respecting gitignore."""
        files = []
        show_ignored = self.show_ignored_btn.get_active()

        # Walk the directory tree
        for path in self.project_path.rglob("*"):
            if not path.is_file():
                continue

            # Skip hidden files/folders
            if any(part.startswith(".") for part in path.relative_to(self.project_path).parts):
                continue

            # Check gitignore patterns using file tree's method
            if not show_ignored and self.file_tree._is_ignored(path):
                continue

            files.append(path)

        # Sort by modification time (most recent first)
        files.sort(key=lambda p: p.stat().st_mtime, reverse=True)

        return files

    def _on_file_search_selected(self, dialog, file_path: str):
        """Handle file selection from search dialog."""
        self._on_file_activated(self.file_tree, file_path)

    def _focus_search(self):
        """Switch to Files tab and focus the unified search entry."""
        self._tab_buttons["files"].set_active(True)
        GLib.idle_add(lambda: self.unified_search.grab_focus() or False)

    def _close_current_tab(self):
        """Close current tab (shows warning for unsaved files)."""
        page = self.tab_view.get_selected_page()
        if page is None:
            return

        # Close the page - close-page signal handler will check for unsaved changes
        self.tab_view.close_page(page)
