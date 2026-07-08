"""Session view widget for displaying full session content."""

from gi.repository import Gtk, GLib

from ..models import Session, Message, SessionContent
from ..services import HistoryAdapter, run_async
from .message_row import MessageRow

# How many messages to render at once. Large agent sessions hold tens of
# thousands of messages; building a widget for every one freezes the UI, so we
# render only the most recent PAGE_SIZE and let the user page backwards.
PAGE_SIZE = 200


class SessionView(Gtk.Box):
    """A scrollable view of session messages (off-thread load, paginated)."""

    def __init__(self, adapter: HistoryAdapter):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)

        self.adapter = adapter
        self.current_session: Session | None = None

        # Pagination state.
        self._all_messages: list[Message] = []
        self._rendered_from: int = 0  # index of the oldest currently-rendered message
        self._load_earlier_btn: Gtk.Button | None = None

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
        self._scrolled = Gtk.ScrolledWindow()
        self._scrolled.set_vexpand(True)
        self._scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        # Message list container
        self.message_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.message_list.set_margin_top(8)
        self.message_list.set_margin_bottom(8)

        self._scrolled.set_child(self.message_list)
        self.append(self._scrolled)

    def load_session(self, session: Session) -> None:
        """Load and display a session's content (parsed off the UI thread)."""
        self.clear()
        self.current_session = session

        # Lightweight placeholder while the (potentially multi-MB) file parses.
        placeholder = Gtk.Label(label="Loading session…")
        placeholder.add_css_class("dim-label")
        placeholder.set_margin_top(24)
        self.message_list.append(placeholder)

        # key="session" gives a generation token: switching sessions quickly
        # drops the stale load so only the newest render lands.
        run_async(
            self,
            worker=lambda: self.adapter.load_session_content(session),
            on_done=self._render,
            key="session",
        )

    def _render(self, content: SessionContent) -> None:
        """Render the parsed session: last PAGE_SIZE messages + paging affordances."""
        self._clear_children()

        messages = content.messages
        self._all_messages = messages
        total = len(messages)

        if total == 0:
            if content.in_progress:
                self._append_in_progress_indicator()
            else:
                self._show_empty_state()
            return

        start = max(0, total - PAGE_SIZE)
        self._rendered_from = start

        if start > 0:
            self._add_load_earlier_button()

        for message in messages[start:]:
            self.message_list.append(MessageRow(message))

        if content.in_progress:
            self._append_in_progress_indicator()

        # Land on the newest turn (the part a reviewer cares about).
        self._scroll_to_bottom()

    def _add_load_earlier_button(self) -> None:
        """Insert (or refresh) the top 'Load earlier messages' button."""
        remaining = self._rendered_from
        button = Gtk.Button(label=f"Load earlier messages ({remaining} remaining)")
        button.add_css_class("flat")
        button.set_margin_top(4)
        button.set_margin_bottom(4)
        button.connect("clicked", self._on_load_earlier)
        self.message_list.prepend(button)
        self._load_earlier_btn = button

    def _on_load_earlier(self, button: Gtk.Button) -> None:
        """Prepend the previous PAGE_SIZE messages, preserving scroll position."""
        vadj = self._scrolled.get_vadjustment()
        old_value = vadj.get_value()
        old_upper = vadj.get_upper()

        new_start = max(0, self._rendered_from - PAGE_SIZE)
        batch = self._all_messages[new_start:self._rendered_from]

        # Remove the button, prepend the batch above existing rows, then re-add
        # the button at the very top if there are still older messages.
        self.message_list.remove(button)
        self._load_earlier_btn = None
        for message in reversed(batch):
            self.message_list.prepend(MessageRow(message))

        self._rendered_from = new_start
        if new_start > 0:
            self._add_load_earlier_button()

        # Keep the viewport anchored on the same content: the newly prepended
        # rows grow `upper`, so shift the value by that delta once laid out.
        def restore_scroll() -> bool:
            vadj.set_value(old_value + (vadj.get_upper() - old_upper))
            return False

        GLib.idle_add(restore_scroll)

    def _append_in_progress_indicator(self) -> None:
        """Footer shown when the session's tail is still being written."""
        label = Gtk.Label(label="⏳ Session in progress")
        label.add_css_class("dim-label")
        label.set_margin_top(8)
        label.set_margin_bottom(8)
        self.message_list.append(label)

    def _scroll_to_bottom(self) -> None:
        """Scroll to the newest message once the list has been laid out."""
        def do_scroll() -> bool:
            vadj = self._scrolled.get_vadjustment()
            vadj.set_value(vadj.get_upper() - vadj.get_page_size())
            return False

        GLib.idle_add(do_scroll)

    def _show_empty_state(self) -> None:
        """Show empty state when no messages."""
        label = Gtk.Label(label="No messages in this session")
        label.add_css_class("dim-label")
        label.set_margin_top(24)
        self.message_list.append(label)

    def _clear_children(self) -> None:
        """Remove all rows from the message list."""
        # Collect children first to avoid modification during iteration.
        children = []
        child = self.message_list.get_first_child()
        while child:
            children.append(child)
            child = child.get_next_sibling()
        for child in children:
            self.message_list.remove(child)

    def clear(self) -> None:
        """Clear the session view and reset pagination state."""
        self.current_session = None
        self._all_messages = []
        self._rendered_from = 0
        self._load_earlier_btn = None
        self._clear_children()
