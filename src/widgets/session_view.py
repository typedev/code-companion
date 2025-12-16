"""Session view widget for displaying full session content."""

from gi.repository import Gtk, GLib

from ..models import Session, Message
from ..services import HistoryAdapter
from .message_row import MessageRow


class SessionView(Gtk.Box):
    """A scrollable view of session messages."""

    def __init__(self, adapter: HistoryAdapter):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)

        self.adapter = adapter
        self.current_session: Session | None = None

        self._setup_css()
        self._build_ui()

    def _setup_css(self):
        """Set up CSS for session view."""
        css = b"""
        .system-message {
            background: alpha(@warning_color, 0.1);
            border: 1px dashed alpha(@warning_color, 0.4);
        }
        .system-message .heading {
            color: @warning_color;
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
        """Build the session view UI."""
        # Scrolled window for messages
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        # Message list container
        self.message_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.message_list.set_margin_top(8)
        self.message_list.set_margin_bottom(8)

        scrolled.set_child(self.message_list)
        self.append(scrolled)

    def load_session(self, session: Session) -> None:
        """Load and display a session's content."""
        self.current_session = session

        # Clear existing messages - collect children first to avoid modification during iteration
        children = []
        child = self.message_list.get_first_child()
        while child:
            children.append(child)
            child = child.get_next_sibling()
        for child in children:
            self.message_list.remove(child)

        # Load session content
        messages = self.adapter.load_session_content(session)

        if not messages:
            self._show_empty_state()
            return

        # Add message widgets
        for message in messages:
            row = MessageRow(message)
            self.message_list.append(row)

    def _show_empty_state(self) -> None:
        """Show empty state when no messages."""
        label = Gtk.Label(label="No messages in this session")
        label.add_css_class("dim-label")
        label.set_margin_top(24)
        self.message_list.append(label)

    def clear(self) -> None:
        """Clear the session view."""
        self.current_session = None
        # Collect children first to avoid modification during iteration
        children = []
        child = self.message_list.get_first_child()
        while child:
            children.append(child)
            child = child.get_next_sibling()
        for child in children:
            self.message_list.remove(child)
