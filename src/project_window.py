"""Project workspace window."""

from pathlib import Path

from gi.repository import Adw, Gtk, GLib, Gio

from .models import Session
from .services import HistoryService, ProjectLock, ProjectRegistry, GitService
from .widgets import SessionView, TerminalView, FileTree, FileEditor, TasksPanel, GitPanel, DiffView


def escape_markup(text: str) -> str:
    """Escape text for safe use in GTK markup."""
    return GLib.markup_escape_text(text)


class ProjectWindow(Adw.ApplicationWindow):
    """Project workspace window with file tree, tabs, and terminal."""

    # Auto-refresh interval for history (5 seconds)
    HISTORY_REFRESH_INTERVAL = 5000

    def __init__(self, project_path: str, **kwargs):
        super().__init__(**kwargs)

        self.project_path = Path(project_path).resolve()
        self.project_name = self.project_path.name

        self.history_service = HistoryService()
        self.registry = ProjectRegistry()
        self.lock = ProjectLock(str(self.project_path))

        self.claude_tab_page: Adw.TabPage | None = None
        self.claude_terminal: TerminalView | None = None
        self.history_tab_page: Adw.TabPage | None = None
        self._refresh_timer_id: int | None = None

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

    def _setup_window(self):
        """Configure window properties."""
        self.set_title(f"{self.project_name} - Claude Companion")
        self.set_default_size(1200, 800)

    def _build_ui(self):
        """Build the UI layout."""
        # Main horizontal split
        self.split_view = Adw.OverlaySplitView()
        self.split_view.set_collapsed(False)
        self.split_view.set_min_sidebar_width(200)
        self.split_view.set_max_sidebar_width(400)

        # Sidebar (file tree)
        sidebar = self._build_sidebar()
        self.split_view.set_sidebar(sidebar)

        # Main content with tabs
        content = self._build_content()
        self.split_view.set_content(content)

        # Wrap everything in a toolbar view
        toolbar_view = Adw.ToolbarView()
        header = self._build_header()
        toolbar_view.add_top_bar(header)
        toolbar_view.set_content(self.split_view)

        self.set_content(toolbar_view)

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

        # Claude button
        self.claude_btn = Gtk.Button()
        self.claude_btn.set_icon_name("utilities-terminal-symbolic")
        self.claude_btn.set_tooltip_text("Start Claude session")
        self.claude_btn.add_css_class("suggested-action")
        self.claude_btn.connect("clicked", self._on_claude_clicked)

        claude_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        claude_icon = Gtk.Image.new_from_icon_name("utilities-terminal-symbolic")
        claude_label = Gtk.Label(label="Claude")
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

        # Title
        title = Adw.WindowTitle()
        title.set_title(self.project_name)
        title.set_subtitle(str(self.project_path))
        header.set_title_widget(title)

        # Open external terminal button
        ext_terminal_btn = Gtk.Button()
        ext_terminal_btn.set_icon_name("terminal-symbolic")
        ext_terminal_btn.set_tooltip_text("Open in system terminal")
        ext_terminal_btn.connect("clicked", self._on_external_terminal_clicked)
        header.pack_end(ext_terminal_btn)

        return header

    def _build_sidebar(self) -> Gtk.Box:
        """Build the sidebar with Files/Changes tabs."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        # Check if this is a git repo
        self.git_service = GitService(self.project_path)
        is_git_repo = self.git_service.is_git_repo()

        # Tab switcher (only if git repo)
        if is_git_repo:
            self.sidebar_stack = Gtk.Stack()
            self.sidebar_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
            self.sidebar_stack.set_vexpand(True)

            # Stack switcher
            switcher = Gtk.StackSwitcher()
            switcher.set_stack(self.sidebar_stack)
            switcher.set_margin_start(12)
            switcher.set_margin_end(12)
            switcher.set_margin_top(8)
            switcher.set_margin_bottom(4)
            box.append(switcher)

            # Files page
            files_page = self._build_files_page()
            self.sidebar_stack.add_titled(files_page, "files", "Files")

            # Changes page
            changes_page = self._build_changes_page()
            self.sidebar_stack.add_titled(changes_page, "changes", "Changes")

            box.append(self.sidebar_stack)
        else:
            # No git - just show files
            files_page = self._build_files_page()
            box.append(files_page)

        return box

    def _build_files_page(self) -> Gtk.Box:
        """Build the Files tab content."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        # Header with refresh button
        header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        header_box.set_margin_start(12)
        header_box.set_margin_end(12)
        header_box.set_margin_top(6)
        header_box.set_margin_bottom(6)

        # Spacer for alignment
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        header_box.append(spacer)

        # Refresh button
        refresh_btn = Gtk.Button()
        refresh_btn.set_icon_name("view-refresh-symbolic")
        refresh_btn.add_css_class("flat")
        refresh_btn.set_tooltip_text("Refresh files")
        refresh_btn.connect("clicked", self._on_refresh_files_clicked)
        header_box.append(refresh_btn)

        box.append(header_box)

        # File tree
        self.file_tree = FileTree(str(self.project_path))
        self.file_tree.connect("file-activated", self._on_file_activated)
        box.append(self.file_tree)

        # Tasks panel (below file tree)
        self.tasks_panel = TasksPanel(str(self.project_path))
        self.tasks_panel.connect("task-run", self._on_task_run)
        box.append(self.tasks_panel)

        return box

    def _build_changes_page(self) -> Gtk.Box:
        """Build the Changes tab content (git panel)."""
        self.git_panel = GitPanel(str(self.project_path))
        self.git_panel.connect("file-clicked", self._on_git_file_clicked)
        return self.git_panel

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
        # Create History tab (pinned)
        self._create_history_tab()

    def _create_history_tab(self):
        """Create the pinned History tab."""
        history_view = self._build_history_view()
        page = self.tab_view.append(history_view)
        page.set_title("History")
        page.set_icon(Gio.ThemedIcon.new("document-open-recent-symbolic"))
        self.tab_view.set_page_pinned(page, True)
        self.history_tab_page = page

    def _build_history_view(self) -> Gtk.Box:
        """Build the history view content."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        # Navigation view for session list -> detail
        self.history_nav = Adw.NavigationView()

        # Sessions list page
        sessions_page = self._build_sessions_page()
        self.history_nav.add(sessions_page)

        box.append(self.history_nav)
        return box

    def _build_sessions_page(self) -> Adw.NavigationPage:
        """Build the sessions list page."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        # Session list in scrolled window
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        self.session_list = Gtk.ListBox()
        self.session_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.session_list.add_css_class("boxed-list")
        self.session_list.set_margin_start(12)
        self.session_list.set_margin_end(12)
        self.session_list.set_margin_top(12)
        self.session_list.set_margin_bottom(12)
        self.session_list.connect("row-activated", self._on_session_activated)

        scrolled.set_child(self.session_list)
        box.append(scrolled)

        # Load sessions
        self._load_sessions()

        # Start auto-refresh
        self._start_history_refresh()

        page = Adw.NavigationPage()
        page.set_title("Sessions")
        page.set_tag("sessions")
        page.set_child(box)

        return page

    def _load_sessions(self):
        """Load sessions for the project."""
        sessions = self.history_service.get_sessions_for_path(self.project_path)

        # Clear existing
        while row := self.session_list.get_first_child():
            self.session_list.remove(row)

        if not sessions:
            label = Gtk.Label(label="No sessions yet")
            label.add_css_class("dim-label")
            label.set_margin_top(24)
            self.session_list.append(label)
            return

        for session in sessions:
            row = self._create_session_row(session)
            self.session_list.append(row)

    def _create_session_row(self, session: Session) -> Gtk.ListBoxRow:
        """Create a list row for a session."""
        row = Adw.ActionRow()
        row.set_title(escape_markup(session.display_date))
        row.set_subtitle(escape_markup(session.short_preview) if session.short_preview else "(empty session)")
        row.set_activatable(True)
        row.session = session

        # Message count badge
        badge = Gtk.Label(label=str(session.message_count))
        badge.add_css_class("dim-label")
        row.add_suffix(badge)

        # Chevron
        chevron = Gtk.Image.new_from_icon_name("go-next-symbolic")
        row.add_suffix(chevron)

        return row

    def _on_session_activated(self, listbox, row):
        """Handle session selection."""
        if not hasattr(row, "session"):
            return

        session = row.session

        # Build session detail page
        detail_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        # Header
        header = Adw.HeaderBar()
        detail_box.append(header)

        # Session view
        session_view = SessionView(self.history_service)
        session_view.load_session(session)
        detail_box.append(session_view)

        page = Adw.NavigationPage()
        page.set_title(session.display_date)
        page.set_tag("session-detail")
        page.set_child(detail_box)

        self.history_nav.push(page)

    def _start_history_refresh(self):
        """Start periodic history refresh."""
        self._stop_history_refresh()
        self._refresh_timer_id = GLib.timeout_add(
            self.HISTORY_REFRESH_INTERVAL,
            self._on_history_refresh_tick
        )

    def _stop_history_refresh(self):
        """Stop history refresh timer."""
        if self._refresh_timer_id is not None:
            GLib.source_remove(self._refresh_timer_id)
            self._refresh_timer_id = None

    def _on_history_refresh_tick(self) -> bool:
        """Periodically refresh history."""
        self._load_sessions()
        return True

    def _on_sidebar_toggled(self, button):
        """Toggle sidebar visibility."""
        self.split_view.set_show_sidebar(button.get_active())

    def _on_claude_clicked(self, button):
        """Start Claude terminal session."""
        if self.claude_tab_page is not None:
            # Claude tab already exists, switch to it
            self.tab_view.set_selected_page(self.claude_tab_page)
            return

        # Create Claude terminal in project directory with claude command
        terminal = TerminalView(
            working_directory=str(self.project_path),
            run_command="claude"
        )

        # Connect to child-exited to know when claude exits
        terminal.connect("child-exited", self._on_claude_exited)

        # Add tab
        page = self.tab_view.append(terminal)
        page.set_title("Claude")
        page.set_icon(Gio.ThemedIcon.new("utilities-terminal-symbolic"))

        self.claude_tab_page = page
        self.claude_terminal = terminal
        self.tab_view.set_selected_page(page)

        # Disable Claude button
        self.claude_btn.set_sensitive(False)

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

    def _on_refresh_files_clicked(self, button):
        """Refresh file tree."""
        self.file_tree.refresh()

    def _on_git_file_clicked(self, git_panel, path: str, staged: bool):
        """Handle git file click - open diff view."""
        # Get diff content
        old_content, new_content = self.git_service.get_diff(path, staged)

        # Create diff view
        diff_view = DiffView(old_content, new_content, file_path=path)

        # Add tab
        page = self.tab_view.append(diff_view)
        status_prefix = "[staged] " if staged else ""
        page.set_title(f"{status_prefix}{Path(path).name}")
        page.set_icon(Gio.ThemedIcon.new("document-edit-symbolic"))
        page.set_tooltip(f"Diff: {path}")

        self.tab_view.set_selected_page(page)

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
            # TODO: Show error toast
            print(f"Error opening file: {e}")
            return

        # Track modifications for tab title
        editor.connect("modified-changed", self._on_editor_modified_changed)

        # Add tab
        page = self.tab_view.append(editor)
        file_name = Path(file_path).name
        page.set_title(file_name)
        page.set_icon(Gio.ThemedIcon.new("text-x-generic-symbolic"))
        page.set_tooltip(file_path)

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

    def _on_tab_close_requested(self, tab_view, page) -> bool:
        """Handle tab close request."""
        # History tab cannot be closed
        if page == self.history_tab_page:
            return True  # Prevent close

        # Claude tab - show warning if active
        if page == self.claude_tab_page:
            dialog = Adw.AlertDialog()
            dialog.set_heading("Close Claude Session?")
            dialog.set_body("The Claude session may still be active. Close anyway?")
            dialog.add_response("cancel", "Cancel")
            dialog.add_response("close", "Close")
            dialog.set_response_appearance("close", Adw.ResponseAppearance.DESTRUCTIVE)
            dialog.set_default_response("cancel")
            dialog.connect("response", self._on_claude_close_response, page)
            dialog.present(self)
            return True  # Prevent immediate close, dialog will handle it

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
        self._stop_history_refresh()
        self.lock.release()
