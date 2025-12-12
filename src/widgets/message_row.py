"""Message row widget for displaying a single conversation message."""

from gi.repository import Gtk, GLib

from ..models import Message, MessageRole, ContentType
from .thinking_block import ThinkingBlock
from .tool_call_card import ToolCallCard
from .markdown_view import MarkdownView


class MessageRow(Gtk.Box):
    """A widget displaying a single conversation message."""

    def __init__(self, message: Message):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)

        self.message = message
        self._build_ui()

    def _build_ui(self):
        """Build the message row UI."""
        self.add_css_class("card")
        self.set_margin_start(12)
        self.set_margin_end(12)
        self.set_margin_top(6)
        self.set_margin_bottom(6)

        # Inner padding
        inner_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        inner_box.set_margin_start(12)
        inner_box.set_margin_end(12)
        inner_box.set_margin_top(10)
        inner_box.set_margin_bottom(10)

        # Header with role icon/label
        header = self._build_header()
        inner_box.append(header)

        # Content blocks
        for block in self.message.content_blocks:
            widget = self._build_content_block(block)
            if widget:
                inner_box.append(widget)

        self.append(inner_box)

    def _build_header(self) -> Gtk.Box:
        """Build the message header with role indicator."""
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

        if self.message.role == MessageRole.USER:
            icon = Gtk.Label(label="👤")
            role_label = Gtk.Label(label="User")
        else:
            icon = Gtk.Label(label="🤖")
            role_label = Gtk.Label(label="Claude")

        role_label.add_css_class("heading")
        header.append(icon)
        header.append(role_label)

        # Timestamp if available
        if self.message.timestamp:
            time_str = self.message.timestamp.strftime("%H:%M")
            time_label = Gtk.Label(label=time_str)
            time_label.add_css_class("dim-label")
            time_label.set_hexpand(True)
            time_label.set_halign(Gtk.Align.END)
            header.append(time_label)

        return header

    def _build_content_block(self, block) -> Gtk.Widget | None:
        """Build a widget for a content block."""
        if block.type == ContentType.TEXT:
            return self._build_text_block(block.text)
        elif block.type == ContentType.THINKING:
            return ThinkingBlock(block.text)
        elif block.type == ContentType.TOOL_USE:
            return ToolCallCard(
                tool_name=block.tool_name,
                tool_input=block.tool_input,
                tool_id=block.tool_id,
                tool_output=block.tool_output,
                tool_is_error=block.tool_is_error,
            )
        return None

    def _build_text_block(self, text: str) -> Gtk.Widget:
        """Build a text content widget with markdown rendering."""
        return MarkdownView(text)
