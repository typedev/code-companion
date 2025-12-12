"""Main application window."""

from gi.repository import Adw, Gtk, GLib

from .models import Project, Session
from .services import HistoryService
from .widgets import SessionView, TerminalView


def escape_markup(text: str) -> str:
    """Escape text for safe use in GTK markup."""
    return GLib.markup_escape_text(text)


class MainWindow(Adw.ApplicationWindow):
    """Main application window with project list, terminal, and history tabs."""

    # Auto-refresh interval in milliseconds (5 seconds)
    HISTORY_REFRESH_INTERVAL = 5000

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        self.history_service = HistoryService()
        self.current_project: Project | None = None
        self.current_session: Session | None = None
        self._refresh_timer_id: int | None = None

        self._setup_window()
        self._build_ui()
        self._load_projects()

    def _setup_window(self):
        """Configure window properties."""
        self.set_title("Claude Companion")
        self.set_default_size(1100, 700)

    def _build_ui(self):
        """Build the main UI layout."""
        # Main split view
        self.split_view = Adw.NavigationSplitView()

        # Sidebar (projects)
        sidebar_page = self._build_sidebar()
        self.split_view.set_sidebar(sidebar_page)

        # Content area with tabs (Terminal / History)
        content_page = self._build_content_with_tabs()
        self.split_view.set_content(content_page)

        self.set_content(self.split_view)

    def _build_sidebar(self) -> Adw.NavigationPage:
        """Build the sidebar with project list."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        # Header
        header = Adw.HeaderBar()
        header.set_show_end_title_buttons(False)
        box.append(header)

        # Project list
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        self.project_list = Gtk.ListBox()
        self.project_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.project_list.add_css_class("navigation-sidebar")
        self.project_list.connect("row-selected", self._on_project_selected)

        scrolled.set_child(self.project_list)
        box.append(scrolled)

        page = Adw.NavigationPage()
        page.set_title("Projects")
        page.set_child(box)

        return page

    def _build_content_with_tabs(self) -> Adw.NavigationPage:
        """Build the content area with Terminal/History tabs."""
        # Main content box
        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        # Header with view switcher
        self.content_header = Adw.HeaderBar()

        # View switcher for tabs
        self.view_switcher = Adw.ViewSwitcher()
        self.view_switcher.set_policy(Adw.ViewSwitcherPolicy.WIDE)
        self.content_header.set_title_widget(self.view_switcher)

        # Button to open system terminal
        self.open_terminal_btn = Gtk.Button()
        self.open_terminal_btn.set_icon_name("terminal-symbolic")
        self.open_terminal_btn.set_tooltip_text("Open in system terminal")
        self.open_terminal_btn.connect("clicked", self._on_open_terminal_clicked)
        self.content_header.pack_end(self.open_terminal_btn)

        content_box.append(self.content_header)

        # View stack for Terminal / History
        self.view_stack = Adw.ViewStack()
        self.view_stack.set_vexpand(True)

        # Terminal tab
        self.terminal_view = TerminalView()
        self.view_stack.add_titled_with_icon(
            self.terminal_view,
            "terminal",
            "Terminal",
            "utilities-terminal-symbolic"
        )

        # History tab (with navigation for session details)
        self.history_nav = Adw.NavigationView()
        history_page = self._build_history_page()
        self.history_nav.add(history_page)

        self.view_stack.add_titled_with_icon(
            self.history_nav,
            "history",
            "History",
            "document-open-recent-symbolic"
        )

        # Connect view switcher to stack
        self.view_switcher.set_stack(self.view_stack)

        # Connect to page changes for history refresh
        self.view_stack.connect("notify::visible-child", self._on_tab_changed)

        content_box.append(self.view_stack)

        # Wrap in NavigationPage
        page = Adw.NavigationPage()
        page.set_title("Claude Companion")
        page.set_child(content_box)

        return page

    def _build_history_page(self) -> Adw.NavigationPage:
        """Build the history/sessions list page."""
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

        # Empty state placeholder
        self.empty_status = Adw.StatusPage()
        self.empty_status.set_title("No Project Selected")
        self.empty_status.set_description("Select a project from the sidebar")
        self.empty_status.set_icon_name("folder-symbolic")

        # Stack for content/empty state
        self.sessions_stack = Gtk.Stack()
        self.sessions_stack.set_vexpand(True)
        self.sessions_stack.add_named(self.empty_status, "empty")
        self.sessions_stack.add_named(scrolled, "sessions")
        self.sessions_stack.set_visible_child_name("empty")

        box.append(self.sessions_stack)

        page = Adw.NavigationPage()
        page.set_title("Sessions")
        page.set_tag("sessions")
        page.set_child(box)

        return page

    def _build_session_detail_page(self, session: Session) -> Adw.NavigationPage:
        """Build the session detail page."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        # Header with back functionality (handled by NavigationView)
        header = Adw.HeaderBar()
        box.append(header)

        # Session view
        session_view = SessionView(self.history_service)
        session_view.load_session(session)
        box.append(session_view)

        page = Adw.NavigationPage()
        page.set_title(session.display_date)
        page.set_tag("session-detail")
        page.set_child(box)

        return page

    def _load_projects(self):
        """Load projects from Claude Code history."""
        projects = self.history_service.get_projects()

        # Clear existing
        while row := self.project_list.get_first_child():
            self.project_list.remove(row)

        if not projects:
            # Show empty state in sidebar
            label = Gtk.Label(label="No projects found")
            label.add_css_class("dim-label")
            label.set_margin_top(24)
            self.project_list.append(label)
            return

        for project in projects:
            row = self._create_project_row(project)
            self.project_list.append(row)

    def _create_project_row(self, project: Project) -> Gtk.ListBoxRow:
        """Create a list row for a project."""
        row = Adw.ActionRow()
        row.set_title(escape_markup(project.name))
        row.set_subtitle(f"{project.session_count} sessions")
        row.set_activatable(True)

        # Store project reference
        row.project = project

        # Add folder icon
        icon = Gtk.Image.new_from_icon_name("folder-symbolic")
        row.add_prefix(icon)

        # Add chevron
        chevron = Gtk.Image.new_from_icon_name("go-next-symbolic")
        row.add_suffix(chevron)

        return row

    def _on_project_selected(self, listbox, row):
        """Handle project selection."""
        if row is None:
            self.current_project = None
            self.sessions_stack.set_visible_child_name("empty")
            return

        if not hasattr(row, "project"):
            return

        self.current_project = row.project

        # Change terminal directory to project path
        if self.current_project.path.exists():
            self.terminal_view.change_directory(str(self.current_project.path))

        # Load sessions for history tab
        self._load_sessions(self.current_project)
        self.sessions_stack.set_visible_child_name("sessions")

        # Pop back to sessions list if we were viewing a session detail
        self.history_nav.pop_to_tag("sessions")

    def _on_tab_changed(self, stack, param):
        """Handle tab switch - start/stop auto-refresh for history."""
        visible = stack.get_visible_child_name()

        if visible == "history":
            # Refresh immediately and start auto-refresh timer
            if self.current_project:
                self._load_sessions(self.current_project)
            self._start_history_refresh()
        else:
            # Stop auto-refresh when leaving history tab
            self._stop_history_refresh()

    def _start_history_refresh(self):
        """Start periodic history refresh timer."""
        self._stop_history_refresh()  # Ensure no duplicate timers
        self._refresh_timer_id = GLib.timeout_add(
            self.HISTORY_REFRESH_INTERVAL,
            self._on_history_refresh_tick
        )

    def _stop_history_refresh(self):
        """Stop the history refresh timer."""
        if self._refresh_timer_id is not None:
            GLib.source_remove(self._refresh_timer_id)
            self._refresh_timer_id = None

    def _on_history_refresh_tick(self) -> bool:
        """Called periodically to refresh history."""
        if self.current_project:
            self._load_sessions(self.current_project)
        return True  # Continue timer

    def _load_sessions(self, project: Project):
        """Load sessions for the selected project."""
        sessions = self.history_service.get_sessions(project)

        # Clear existing
        while row := self.session_list.get_first_child():
            self.session_list.remove(row)

        if not sessions:
            label = Gtk.Label(label="No sessions found")
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

        # Store session reference
        row.session = session

        # Add message count badge
        badge = Gtk.Label(label=str(session.message_count))
        badge.add_css_class("dim-label")
        row.add_suffix(badge)

        # Add chevron
        chevron = Gtk.Image.new_from_icon_name("go-next-symbolic")
        row.add_suffix(chevron)

        return row

    def _on_session_activated(self, listbox, row):
        """Handle session selection - show session detail."""
        if not hasattr(row, "session"):
            return

        self.current_session = row.session

        # Create and push session detail page
        detail_page = self._build_session_detail_page(self.current_session)
        self.history_nav.push(detail_page)

    def _on_open_terminal_clicked(self, button):
        """Open system terminal in current project directory."""
        self.terminal_view.open_system_terminal()
