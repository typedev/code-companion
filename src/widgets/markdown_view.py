"""Markdown rendering widget using Pango markup and GtkSourceView for code blocks."""

import re
from gi.repository import Gtk, Pango, GLib

from .code_view import CodeView


class MarkdownView(Gtk.Box):
    """A widget for rendering markdown content."""

    # Regex patterns for markdown parsing
    CODE_BLOCK_PATTERN = re.compile(r"```(\w*)\n(.*?)```", re.DOTALL)
    INLINE_CODE_PATTERN = re.compile(r"`([^`]+)`")
    BOLD_PATTERN = re.compile(r"\*\*(.+?)\*\*")
    ITALIC_PATTERN = re.compile(r"\*(.+?)\*")
    HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)
    LINK_PATTERN = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
    LIST_ITEM_PATTERN = re.compile(r"^(\s*)[*\-+]\s+(.+)$", re.MULTILINE)
    NUMBERED_LIST_PATTERN = re.compile(r"^(\s*)\d+\.\s+(.+)$", re.MULTILINE)

    def __init__(self, markdown_text: str):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4)

        self.markdown_text = markdown_text
        self._build_ui()

    def _build_ui(self):
        """Parse markdown and build UI."""
        # Split text by code blocks first
        parts = self._split_by_code_blocks(self.markdown_text)

        for part in parts:
            if part["type"] == "code":
                # Render code block with syntax highlighting
                code_view = CodeView(
                    part["content"],
                    language=part.get("language"),
                    show_line_numbers=True,
                    max_lines=20,
                )
                code_view.set_margin_top(4)
                code_view.set_margin_bottom(4)
                self.append(code_view)
            else:
                # Render text with Pango markup
                text_widget = self._render_text(part["content"])
                if text_widget:
                    self.append(text_widget)

    def _split_by_code_blocks(self, text: str) -> list[dict]:
        """Split markdown text into code blocks and regular text."""
        parts = []
        last_end = 0

        for match in self.CODE_BLOCK_PATTERN.finditer(text):
            # Add text before code block
            if match.start() > last_end:
                text_before = text[last_end : match.start()]
                if text_before.strip():
                    parts.append({"type": "text", "content": text_before})

            # Add code block
            language = match.group(1) or None
            code = match.group(2).strip()
            parts.append({"type": "code", "content": code, "language": language})

            last_end = match.end()

        # Add remaining text
        if last_end < len(text):
            remaining = text[last_end:]
            if remaining.strip():
                parts.append({"type": "text", "content": remaining})

        # If no code blocks found, return whole text
        if not parts:
            parts.append({"type": "text", "content": text})

        return parts

    def _render_text(self, text: str) -> Gtk.Widget | None:
        """Render text portion with Pango markup."""
        if not text.strip():
            return None

        # Process paragraphs
        paragraphs = text.split("\n\n")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)

        for para in paragraphs:
            para = para.strip()
            if not para:
                continue

            # Check for headings
            heading_match = self.HEADING_PATTERN.match(para)
            if heading_match:
                level = len(heading_match.group(1))
                heading_text = heading_match.group(2)
                label = self._create_heading(heading_text, level)
                box.append(label)
                continue

            # Check for list items
            if self._is_list(para):
                list_widget = self._render_list(para)
                box.append(list_widget)
                continue

            # Regular paragraph
            markup = self._text_to_pango(para)
            label = Gtk.Label()
            label.set_markup(markup)
            label.set_wrap(True)
            label.set_xalign(0)
            label.set_selectable(True)
            box.append(label)

        return box

    def _create_heading(self, text: str, level: int) -> Gtk.Label:
        """Create a heading label."""
        label = Gtk.Label()
        label.set_markup(self._text_to_pango(text))
        label.set_wrap(True)
        label.set_xalign(0)
        label.set_selectable(True)

        # Style based on heading level
        if level == 1:
            label.add_css_class("title-1")
        elif level == 2:
            label.add_css_class("title-2")
        elif level == 3:
            label.add_css_class("title-3")
        else:
            label.add_css_class("title-4")

        return label

    def _is_list(self, text: str) -> bool:
        """Check if text is a list."""
        lines = text.split("\n")
        for line in lines:
            if self.LIST_ITEM_PATTERN.match(line) or self.NUMBERED_LIST_PATTERN.match(line):
                return True
        return False

    def _render_list(self, text: str) -> Gtk.Box:
        """Render a list."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        box.set_margin_start(16)

        lines = text.split("\n")
        for line in lines:
            line = line.strip()
            if not line:
                continue

            # Remove list marker
            list_match = self.LIST_ITEM_PATTERN.match(line)
            num_match = self.NUMBERED_LIST_PATTERN.match(line)

            if list_match:
                item_text = list_match.group(2)
                bullet = "•"
            elif num_match:
                item_text = num_match.group(2)
                bullet = "•"  # Use bullet for now
            else:
                item_text = line
                bullet = ""

            item_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

            if bullet:
                bullet_label = Gtk.Label(label=bullet)
                bullet_label.set_valign(Gtk.Align.START)
                item_box.append(bullet_label)

            text_label = Gtk.Label()
            text_label.set_markup(self._text_to_pango(item_text))
            text_label.set_wrap(True)
            text_label.set_xalign(0)
            text_label.set_hexpand(True)
            text_label.set_selectable(True)
            item_box.append(text_label)

            box.append(item_box)

        return box

    def _text_to_pango(self, text: str) -> str:
        """Convert markdown inline formatting to Pango markup."""
        # Escape special characters first
        text = GLib.markup_escape_text(text)

        # Bold: **text** -> <b>text</b>
        text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)

        # Italic: *text* -> <i>text</i> (but not inside bold)
        text = re.sub(r"(?<!\*)\*([^*]+?)\*(?!\*)", r"<i>\1</i>", text)

        # Inline code: `code` -> <tt>code</tt>
        text = re.sub(r"`([^`]+)`", r"<tt>\1</tt>", text)

        # Links: [text](url) -> text (underlined)
        text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"<u>\1</u>", text)

        return text
