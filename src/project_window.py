"""Project workspace window."""

from pathlib import Path

from gi.repository import Adw, Gtk, GLib, Gio, Gdk

from .models import Session
from .services import HistoryService, ProjectLock, ProjectRegistry, GitService, IconCache, ToastService
from .widgets import SessionView, TerminalView, FileTree, FileEditor, TasksPanel, GitChangesPanel, GitHistoryPanel, DiffView, CommitDetailView


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
        self.commit_detail_page: Adw.TabPage | None = None
        self.commit_detail_view: CommitDetailView | None = None
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

        # Wrap in toast overlay for notifications
        self.toast_overlay = Adw.ToastOverlay()
        self.toast_overlay.set_child(toolbar_view)

        # Initialize toast service
        ToastService.init(self.toast_overlay)

        self.set_content(self.toast_overlay)

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

        # Claude button with Material Design icon
        self.claude_btn = Gtk.Button()
        self.claude_btn.set_tooltip_text("Start Claude session")
        self.claude_btn.add_css_class("suggested-action")
        self.claude_btn.connect("clicked", self._on_claude_clicked)

        claude_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)

        # Use Claude icon from cache
        icon_cache = IconCache()
        claude_texture = icon_cache.get_claude_texture()
        if claude_texture:
            claude_icon = Gtk.Image.new_from_paintable(claude_texture)
            claude_icon.set_pixel_size(16)
        else:
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
        """Build the sidebar with Files/Git tabs."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        # Check if this is a git repo
        self.git_service = GitService(self.project_path)
        self._is_git_repo = self.git_service.is_git_repo()
        if self._is_git_repo:
            self.git_service.open()

        # Top-level stack: Files / Git
        if self._is_git_repo:
            self.sidebar_stack = Gtk.Stack()
            self.sidebar_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
            self.sidebar_stack.set_vexpand(True)

            # Top-level stack switcher
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

            # Git page (with nested Changes/History tabs)
            git_page = self._build_git_page()
            self.sidebar_stack.add_titled(git_page, "git", "Git")

            box.append(self.sidebar_stack)
        else:
            # No git - just show files
            files_page = self._build_files_page()
            box.append(files_page)

        return box

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

        # Spacer for alignment
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        header_box.append(spacer)

        # Show ignored files toggle
        self.show_ignored_btn = Gtk.ToggleButton()
        self.show_ignored_btn.set_icon_name("view-reveal-symbolic")
        self.show_ignored_btn.add_css_class("flat")
        self.show_ignored_btn.set_tooltip_text("Show ignored files")
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

        # File tree
        self.file_tree = FileTree(str(self.project_path))
        self.file_tree.connect("file-activated", self._on_file_activated)
        box.append(self.file_tree)

        # Tasks panel (below file tree)
        self.tasks_panel = TasksPanel(str(self.project_path))
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
        self.git_changes_panel = GitChangesPanel(str(self.project_path))
        self.git_changes_panel.connect("file-clicked", self._on_git_file_clicked)
        self.git_stack.add_titled(self.git_changes_panel, "changes", "Changes")

        # History panel
        self.git_history_panel = GitHistoryPanel(self.git_service)
        self.git_history_panel.connect("commit-view-diff", self._on_commit_view_diff)
        self.git_stack.add_titled(self.git_history_panel, "history", "History")

        box.append(self.git_stack)

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
        self.session_list.remove_all()

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

        # Add tab with Claude icon
        page = self.tab_view.append(terminal)
        page.set_title("Claude")

        # Use Claude icon from cache
        icon_cache = IconCache()
        claude_gicon = icon_cache.get_claude_gicon()
        page.set_icon(claude_gicon or Gio.ThemedIcon.new("utilities-terminal-symbolic"))

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
        selected_paths = self.file_tree._get_selected_paths()
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

        # Commit detail tab - clear references when closed
        if page == self.commit_detail_page:
            self.commit_detail_page = None
            self.commit_detail_view = None

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
