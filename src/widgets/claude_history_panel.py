"""Claude history panel widget for viewing sessions in sidebar."""

import threading
from pathlib import Path

from gi.repository import Gtk, GLib, GObject

from ..models import Session
from ..services import HistoryService


class ClaudeHistoryPanel(Gtk.Box):
    """Panel displaying Claude session history in sidebar.

    Uses lazy loading - sessions are only parsed when the tab is shown.
    """

    __gsignals__ = {
        "session-activated": (GObject.SignalFlags.RUN_FIRST, None, (object,)),  # Session object
    }

    def __init__(self, project_path: Path, history_service: HistoryService):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)

        self.project_path = project_path
        self.history_service = history_service
        self._all_sessions = []  # Cache sessions for filtering
        self._filter_text = ""
        self._loaded = False  # Lazy loading flag
        self._loading = False  # Currently loading flag

        self._setup_css()
        self._build_ui()
        # Don't load on init - wait for tab to be shown

    def _setup_css(self):
        """Set up CSS for the panel."""
        css = b"""
        .session-preview {
            font-size: 0.9em;
        }
        .session-date {
            font-weight: bold;
        }
        .session-count {
            font-family: monospace;
            font-size: 0.85em;
            padding: 2px 6px;
            border-radius: 4px;
            background: alpha(@accent_color, 0.2);
        }
        """
        provider = Gtk.CssProvider()
        provider.load_from_data(css)
        Gtk.StyleContext.add_provider_for_display(
            self.get_display(),
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

    def _build_ui(self):
        """Build the panel UI."""
        # Header
        header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        header_box.set_margin_start(12)
        header_box.set_margin_end(12)
        header_box.set_margin_top(12)
        header_box.set_margin_bottom(6)

        label = Gtk.Label(label="Sessions")
        label.set_xalign(0)
        label.set_hexpand(True)
        label.add_css_class("heading")
        header_box.append(label)

        # Refresh button
        self.refresh_btn = Gtk.Button()
        self.refresh_btn.set_icon_name("view-refresh-symbolic")
        self.refresh_btn.add_css_class("flat")
        self.refresh_btn.set_tooltip_text("Refresh")
        self.refresh_btn.connect("clicked", lambda b: self.refresh())
        header_box.append(self.refresh_btn)

        self.append(header_box)

        # Search entry
        self.search_entry = Gtk.SearchEntry()
        self.search_entry.set_placeholder_text("Filter sessions...")
        self.search_entry.set_margin_start(12)
        self.search_entry.set_margin_end(12)
        self.search_entry.set_margin_bottom(6)
        self.search_entry.connect("search-changed", self._on_search_changed)
        self.append(self.search_entry)

        # Stack for content (loading spinner vs sessions list)
        self.content_stack = Gtk.Stack()
        self.content_stack.set_vexpand(True)

        # Loading view with spinner
        loading_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        loading_box.set_valign(Gtk.Align.CENTER)
        loading_box.set_halign(Gtk.Align.CENTER)

        self.spinner = Gtk.Spinner()
        self.spinner.set_size_request(32, 32)
        loading_box.append(self.spinner)

        loading_label = Gtk.Label(label="Loading sessions...")
        loading_label.add_css_class("dim-label")
        loading_box.append(loading_label)

        self.content_stack.add_named(loading_box, "loading")

        # Sessions list view
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        self.sessions_list = Gtk.ListBox()
        self.sessions_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.sessions_list.add_css_class("boxed-list")
        self.sessions_list.set_margin_start(12)
        self.sessions_list.set_margin_end(12)
        self.sessions_list.set_margin_bottom(12)
        self.sessions_list.connect("row-activated", self._on_row_activated)

        scrolled.set_child(self.sessions_list)
        self.content_stack.add_named(scrolled, "sessions")

        # Initial state - show empty sessions view
        self.content_stack.set_visible_child_name("sessions")
        self.append(self.content_stack)

    def load_if_needed(self):
        """Load sessions if not already loaded. Called when tab is shown."""
        if not self._loaded and not self._loading:
            self.refresh()

    def refresh(self):
        """Refresh the sessions list (loads in background thread)."""
        if self._loading:
            return  # Already loading

        # Save current selection
        self._selected_session_id = None
        selected_row = self.sessions_list.get_selected_row()
        if selected_row and hasattr(selected_row, "session"):
            self._selected_session_id = selected_row.session.id

        # Show loading state
        self._loading = True
        self.refresh_btn.set_sensitive(False)
        self.spinner.start()
        self.content_stack.set_visible_child_name("loading")

        # Load in background thread
        def load_sessions():
            try:
                sessions = self.history_service.get_sessions_for_path(self.project_path)
                GLib.idle_add(self._on_sessions_loaded, sessions, None)
            except Exception as e:
                GLib.idle_add(self._on_sessions_loaded, [], str(e))

        thread = threading.Thread(target=load_sessions, daemon=True)
        thread.start()

    def _on_sessions_loaded(self, sessions: list, error: str | None):
        """Called when sessions are loaded (on main thread)."""
        self._loading = False
        self._loaded = True
        self.refresh_btn.set_sensitive(True)
        self.spinner.stop()
        self.content_stack.set_visible_child_name("sessions")

        # Clear existing
        self.sessions_list.remove_all()

        if error:
            label = Gtk.Label(label=f"Error: {error}")
            label.add_css_class("dim-label")
            self.sessions_list.append(label)
            return

        self._all_sessions = sessions
        self._display_sessions()

    def _on_search_changed(self, entry):
        """Handle search entry changes."""
        self._filter_text = entry.get_text().strip().lower()
        self._display_sessions()

    def _display_sessions(self):
        """Display sessions with current filter."""
        self.sessions_list.remove_all()

        if not self._all_sessions:
            label = Gtk.Label(label="No sessions yet")
            label.add_css_class("dim-label")
            label.set_margin_top(24)
            self.sessions_list.append(label)
            return

        # Filter sessions
        if self._filter_text:
            filtered = [
                s for s in self._all_sessions
                if self._filter_text in (s.short_preview or "").lower()
                or self._filter_text in s.display_date.lower()
            ]
        else:
            filtered = self._all_sessions

        if not filtered:
            label = Gtk.Label(label="No matching sessions")
            label.add_css_class("dim-label")
            label.set_margin_top(24)
            self.sessions_list.append(label)
            return

        # Rebuild list and restore selection
        row_to_select = None
        selected_id = getattr(self, "_selected_session_id", None)
        for session in filtered:
            row = self._create_session_row(session)
            self.sessions_list.append(row)
            if selected_id and session.id == selected_id:
                row_to_select = row

        # Restore selection without emitting signal
        if row_to_select:
            self.sessions_list.select_row(row_to_select)

    def _create_session_row(self, session: Session) -> Gtk.ListBoxRow:
        """Create a row for a session."""
        row = Gtk.ListBoxRow()
        row.session = session

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        box.set_margin_start(8)
        box.set_margin_end(8)
        box.set_margin_top(8)
        box.set_margin_bottom(8)

        # Top line: date + message count
        top_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)

        # Date
        date_label = Gtk.Label(label=session.display_date)
        date_label.add_css_class("session-date")
        date_label.set_xalign(0)
        top_box.append(date_label)

        # Spacer
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        top_box.append(spacer)

        # Message count badge
        count_label = Gtk.Label(label=str(session.message_count))
        count_label.add_css_class("session-count")
        top_box.append(count_label)

        box.append(top_box)

        # Preview
        if session.short_preview:
            preview_label = Gtk.Label(label=session.short_preview)
            preview_label.set_xalign(0)
            preview_label.set_ellipsize(2)  # MIDDLE
            preview_label.add_css_class("session-preview")
            preview_label.add_css_class("dim-label")
            box.append(preview_label)

        row.set_child(box)
        return row

    def _on_row_activated(self, list_box, row):
        """Handle session activation."""
        if row and hasattr(row, "session"):
            self.emit("session-activated", row.session)
