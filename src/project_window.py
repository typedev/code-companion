"""Project workspace window."""

from pathlib import Path

from gi.repository import Adw, Gtk, GLib, Gio

from .models import Session
from .services import get_adapter, ProjectLock, ProjectRegistry, GitService, IconCache, ToastService, SettingsService, FileMonitorService
from .widgets import SessionView, TerminalView, FileTree, FileEditor, TasksPanel, GitChangesPanel, GitHistoryPanel, DiffView, CommitDetailView, ClaudeHistoryPanel, FileSearchDialog, UnifiedSearch, NotesPanel, PreferencesDialog, SnippetsBar, ProblemsPanel, ProblemsDetailView


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

        self.claude_tab_page: Adw.TabPage | None = None
        self.claude_terminal: TerminalView | None = None
        self.commit_detail_page: Adw.TabPage | None = None
        self.commit_detail_view: CommitDetailView | None = None
        self.session_detail_page: Adw.TabPage | None = None
        self.session_detail_view: SessionView | None = None
        self.git_diff_page: Adw.TabPage | None = None
        self.git_diff_view: DiffView | None = None
        self.git_diff_path: str | None = None
        self.git_diff_staged: bool | None = None
        self.problems_detail_page: Adw.TabPage | None = None
        self.problems_detail_view: ProblemsDetailView | None = None

        # Acquire lock
        if not self.lock.acquire():
            # This shouldn't happen if project manager checked, but be safe
            self.close()
            return

        # Register project if not already
        self.registry.register_project(str(self.project_path))

        self._setup_window()
        self._build_ui()
        self._load_project()

        # Connect destroy signal to release lock
        self.connect("destroy", self._on_destroy)

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
        # Main container: [Vertical Toolbar | Paned[Sidebar | Content]]
        main_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)

        # Vertical toolbar on the left
        self.vertical_toolbar = self._build_vertical_toolbar()
        main_box.append(self.vertical_toolbar)

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
        sidebar_btn = Gtk.ToggleButton()
        sidebar_btn.set_icon_name("sidebar-show-symbolic")
        sidebar_btn.set_tooltip_text("Toggle sidebar")
        sidebar_btn.set_active(True)
        sidebar_btn.connect("toggled", self._on_sidebar_toggled)
        header.pack_start(sidebar_btn)

        # AI CLI button with Material Design icon
        self.claude_btn = Gtk.Button()
        self.claude_btn.set_tooltip_text(f"Start {self.adapter.name} session")
        self.claude_btn.add_css_class("suggested-action")
        self.claude_btn.connect("clicked", self._on_claude_clicked)

        claude_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)

        # Use provider icon from cache (dynamically based on adapter)
        icon_cache = IconCache()
        provider_texture = icon_cache.get_provider_texture(self.adapter.icon_name)
        if provider_texture:
            claude_icon = Gtk.Image.new_from_paintable(provider_texture)
            claude_icon.set_pixel_size(16)
        else:
            claude_icon = Gtk.Image.new_from_icon_name("utilities-terminal-symbolic")

        claude_label = Gtk.Label(label=self.adapter.name)
        claude_box.append(claude_icon)
        claude_box.append(claude_label)
        self.claude_btn.set_child(claude_box)

        header.pack_start(self.claude_btn)

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

        # Load icon from resources
        icon_path = Path(__file__).parent / "resources" / "icons" / f"{icon_name}.svg"
        if icon_path.exists():
            gicon = Gio.FileIcon.new(Gio.File.new_for_path(str(icon_path)))
            image = Gtk.Image.new_from_gicon(gicon)
            image.set_pixel_size(20)
            btn.set_child(image)

        btn.connect("toggled", self._on_tab_toggled, tab_name)
        return btn

    def _build_vertical_toolbar(self) -> Gtk.Box:
        """Build vertical toolbar with tab buttons on the left."""
        toolbar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        toolbar.set_spacing(2)
        toolbar.set_margin_top(8)
        toolbar.set_margin_bottom(8)
        toolbar.set_margin_start(6)
        toolbar.set_margin_end(6)

        self._tab_buttons = {}

        # Files button
        files_btn = self._create_toolbar_button("folder-open", "Files", "files")
        files_btn.set_active(True)
        toolbar.append(files_btn)
        self._tab_buttons["files"] = files_btn

        # Git button (will be shown/hidden based on git repo status)
        self._git_toolbar_btn = self._create_toolbar_button("git", "Git", "git")
        toolbar.append(self._git_toolbar_btn)
        self._tab_buttons["git"] = self._git_toolbar_btn

        # Claude button
        claude_btn = self._create_toolbar_button("claude", "Claude", "claude")
        toolbar.append(claude_btn)
        self._tab_buttons["claude"] = claude_btn

        # Notes button
        notes_btn = self._create_toolbar_button("file", "Notes", "notes")
        toolbar.append(notes_btn)
        self._tab_buttons["notes"] = notes_btn

        # Problems button
        problems_btn = self._create_toolbar_button("problems", "Problems (ruff/mypy)", "problems")
        toolbar.append(problems_btn)
        self._tab_buttons["problems"] = problems_btn

        # Spacer to push future buttons to bottom
        spacer = Gtk.Box()
        spacer.set_vexpand(True)
        toolbar.append(spacer)

        # Separator line on the right side
        separator = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)

        # Wrap toolbar + separator in horizontal box
        container = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        container.append(toolbar)
        container.append(separator)

        return container

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

        # Show/hide Git button based on git repo status
        if hasattr(self, "_git_toolbar_btn"):
            self._git_toolbar_btn.set_visible(self._is_git_repo)

        # Top-level stack: Files / Git / Claude / Notes
        self.sidebar_stack = Gtk.Stack()
        self.sidebar_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.sidebar_stack.set_vexpand(True)
        self.sidebar_stack.set_hexpand(False)

        # Files page
        files_page = self._build_files_page()
        self.sidebar_stack.add_named(files_page, "files")

        # Git page (always add to stack, button visibility controls access)
        if self._is_git_repo:
            git_page = self._build_git_page()
            self.sidebar_stack.add_named(git_page, "git")

        # Claude page
        claude_page = self._build_claude_page()
        self.sidebar_stack.add_named(claude_page, "claude")

        # Notes page
        notes_page = self._build_notes_page()
        self.sidebar_stack.add_named(notes_page, "notes")

        # Problems page
        problems_page = self._build_problems_page()
        self.sidebar_stack.add_named(problems_page, "problems")

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

    def _on_tab_toggled(self, button, tab_name):
        """Handle tab button toggle."""
        if button.get_active():
            # Skip if sidebar_stack not yet created (during init)
            if not hasattr(self, "sidebar_stack"):
                return
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
        for i in range(self.tab_view.get_n_pages()):
            page = self.tab_view.get_nth_page(i)
            child = page.get_child()
            if hasattr(child, "file_path") and child.file_path == file_path:
                if hasattr(child, "go_to_line"):
                    child.go_to_line(line_number, search_term)
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

        # Changes panel
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
        """Build the main content area with tab view."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        # Tab bar
        self.tab_bar = Adw.TabBar()
        self.tab_bar.set_autohide(False)
        box.append(self.tab_bar)

        # Tab view
        self.tab_view = Adw.TabView()
        self.tab_view.set_vexpand(True)
        self.tab_view.connect("close-page", self._on_tab_close_requested)
        box.append(self.tab_view)

        # Connect tab bar to tab view
        self.tab_bar.set_view(self.tab_view)

        return box

    def _load_project(self):
        """Load project data and create initial tabs."""
        # No pinned tabs needed - history is in sidebar now
        pass

    def _on_session_activated(self, panel, session: Session):
        """Handle session activation - show in main area, reuse single tab."""
        # Reuse existing session detail view if available
        if self.session_detail_view is not None and self.session_detail_page is not None:
            # Update existing view with new session
            self.session_detail_view.load_session(session)
            self.session_detail_page.set_title(f"Session: {session.display_date}")
            self.tab_view.set_selected_page(self.session_detail_page)
            return

        # Create new session detail view
        self.session_detail_view = SessionView(self.adapter)
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
        """Start Claude terminal session."""
        if self.claude_tab_page is not None:
            # Claude tab already exists, switch to it
            self.tab_view.set_selected_page(self.claude_tab_page)
            return

        # Create container for terminal + snippets bar
        container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        container.set_vexpand(True)

        # Create AI CLI terminal in project directory
        terminal = TerminalView(
            working_directory=str(self.project_path),
            run_command=self.adapter.cli_command
        )
        terminal.set_vexpand(True)
        container.append(terminal)

        # Add snippets bar below terminal
        snippets_bar = SnippetsBar()
        snippets_bar.connect("snippet-clicked", self._on_snippet_clicked)
        container.append(snippets_bar)

        # Connect to child-exited to know when claude exits
        terminal.connect("child-exited", self._on_claude_exited)

        # Add tab with Claude icon
        page = self.tab_view.append(container)
        page.set_title(self.adapter.name)

        # Use provider icon from cache (dynamically based on adapter)
        icon_cache = IconCache()
        provider_gicon = icon_cache.get_provider_gicon(self.adapter.icon_name)
        page.set_icon(provider_gicon or Gio.ThemedIcon.new("utilities-terminal-symbolic"))

        self.claude_tab_page = page
        self.claude_terminal = terminal
        self.tab_view.set_selected_page(page)

        # Disable Claude button
        self.claude_btn.set_sensitive(False)

    def _on_snippet_clicked(self, snippets_bar, text: str):
        """Handle snippet button click - insert text into Claude terminal."""
        if self.claude_terminal:
            self.claude_terminal.terminal.feed_child(text.encode("utf-8"))
            self.claude_terminal.terminal.grab_focus()

    def _on_claude_exited(self, terminal, status):
        """Handle Claude shell exit - close tab and re-enable button."""
        if self.claude_tab_page:
            self.tab_view.close_page(self.claude_tab_page)
        self.claude_btn.set_sensitive(True)
        self.claude_tab_page = None
        self.claude_terminal = None

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

            # Close any tabs with the deleted file
            self._close_tabs_for_path(path)

        except OSError as e:
            ToastService.show_error(f"Failed to delete: {e}")

    def _close_tabs_for_path(self, deleted_path: Path):
        """Close any tabs that reference the deleted path."""
        pages_to_close = []
        for i in range(self.tab_view.get_n_pages()):
            page = self.tab_view.get_nth_page(i)
            child = page.get_child()
            if hasattr(child, "file_path"):
                child_path = Path(child.file_path)
                # Check if the file is the deleted path or inside deleted folder
                if child_path == deleted_path or deleted_path in child_path.parents:
                    pages_to_close.append(page)

        for page in pages_to_close:
            self.tab_view.close_page(page)

    def _save_and_close_tabs_for_path(self, target_path: Path):
        """Save and close any tabs that reference the target path."""
        pages_to_close = []
        for i in range(self.tab_view.get_n_pages()):
            page = self.tab_view.get_nth_page(i)
            child = page.get_child()
            if hasattr(child, "file_path"):
                child_path = Path(child.file_path)
                # Check if the file is the target path or inside target folder
                if child_path == target_path or target_path in child_path.parents:
                    # Save if modified
                    if isinstance(child, FileEditor) and child._modified:
                        child.save()
                    pages_to_close.append(page)

        for page in pages_to_close:
            self.tab_view.close_page(page)

    def _on_rename_requested(self, file_tree, old_path_str: str, new_name: str):
        """Handle rename request from file tree."""
        old_path = Path(old_path_str)
        new_path = old_path.parent / new_name

        # Validate
        if new_path.exists():
            ToastService.show_error(f"'{new_name}' already exists")
            return

        # Save and close affected tabs
        self._save_and_close_tabs_for_path(old_path)

        # Perform rename
        try:
            old_path.rename(new_path)
            item_type = "Folder" if new_path.is_dir() else "File"
            ToastService.show(f"{item_type} renamed to '{new_name}'")
        except OSError as e:
            ToastService.show_error(f"Failed to rename: {e}")

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
                # Save and close tabs first
                self._save_and_close_tabs_for_path(path)

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
        # If same file already open - just switch to it
        if (self.git_diff_page is not None and
            self.git_diff_path == path and
            self.git_diff_staged == staged):
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

    def _on_file_activated(self, file_tree, file_path: str):
        """Handle file activation from tree - open in tab."""
        # Check if file is already open
        for i in range(self.tab_view.get_n_pages()):
            page = self.tab_view.get_nth_page(i)
            child = page.get_child()
            if hasattr(child, "file_path") and child.file_path == file_path:
                self.tab_view.set_selected_page(page)
                return

        # Create file editor
        try:
            editor = FileEditor(file_path)
        except Exception as e:
            ToastService.show_error(f"Error opening file: {e}")
            return

        # Track modifications for tab title
        editor.connect("modified-changed", self._on_editor_modified_changed)

        # Track run requests for script files
        editor.connect("run-requested", self._on_run_requested)

        # Add tab with appropriate file icon
        page = self.tab_view.append(editor)
        file_name = Path(file_path).name
        page.set_title(file_name)
        page.set_tooltip(file_path)

        # Use Material Design icon for the file tab
        icon_cache = IconCache()
        gicon = icon_cache.get_file_gicon(Path(file_path))
        page.set_icon(gicon or Gio.ThemedIcon.new("text-x-generic-symbolic"))

        # Store page reference in editor for later lookup
        editor._tab_page = page

        self.tab_view.set_selected_page(page)
        editor.grab_focus()

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

    def _on_tab_close_requested(self, tab_view, page) -> bool:
        """Handle tab close request."""
        # AI CLI tab - show warning if active
        if page == self.claude_tab_page:
            dialog = Adw.AlertDialog()
            dialog.set_heading(f"Close {self.adapter.name} Session?")
            dialog.set_body(f"The {self.adapter.name} session may still be active. Close anyway?")
            dialog.add_response("cancel", "Cancel")
            dialog.add_response("close", "Close")
            dialog.set_response_appearance("close", Adw.ResponseAppearance.DESTRUCTIVE)
            dialog.set_default_response("cancel")
            dialog.connect("response", self._on_claude_close_response, page)
            dialog.present(self)
            return True  # Prevent immediate close, dialog will handle it

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

        # Problems detail tab - clear references when closed
        if page == self.problems_detail_page:
            self.problems_detail_page = None
            self.problems_detail_view = None

        return False  # Allow close

    def _on_claude_close_response(self, dialog, response, page):
        """Handle Claude close dialog response."""
        if response == "close":
            self.tab_view.close_page_finish(page, True)
            self.claude_tab_page = None
            self.claude_terminal = None
            self.claude_btn.set_sensitive(True)

    def _on_destroy(self, window):
        """Clean up on window destroy."""
        # Save window size (only if not maximized)
        if not self.is_maximized():
            width, height = self.get_default_size()
            # get_default_size returns -1 if not set, use actual size
            if width <= 0 or height <= 0:
                width = self.get_width()
                height = self.get_height()
            self.settings.set("window.width", width)
            self.settings.set("window.height", height)

        # Shutdown file monitor service
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
        """Close current tab with autosave if it's a modified file."""
        page = self.tab_view.get_selected_page()
        if page is None:
            return

        # Get the child widget
        child = page.get_child()

        # If it's a FileEditor with unsaved changes, save first
        if isinstance(child, FileEditor) and child._modified:
            child.save()

        # Close the page (this will trigger close-page signal for cleanup)
        self.tab_view.close_page(page)
