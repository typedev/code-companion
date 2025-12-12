"""Tool call card widget with expandable content."""

from gi.repository import Gtk, GLib

from ..models import TOOL_ICONS, DEFAULT_TOOL_ICON
from .code_view import CodeView, DiffView


class ToolCallCard(Gtk.Box):
    """A card displaying a tool call with expandable content."""

    # Maximum length of output to show initially
    MAX_OUTPUT_PREVIEW = 5000

    def __init__(
        self,
        tool_name: str,
        tool_input: dict,
        tool_id: str = "",
        tool_output: str = "",
        tool_is_error: bool = False,
    ):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        self.tool_name = tool_name
        self.tool_input = tool_input
        self.tool_id = tool_id
        self.tool_output = tool_output
        self.tool_is_error = tool_is_error
        self._expanded = False

        self._build_ui()

    def _build_ui(self):
        """Build the tool call card UI."""
        self.add_css_class("card")
        self.set_margin_top(4)
        self.set_margin_bottom(4)

        # Header (always visible, clickable)
        self.header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.header.set_margin_start(12)
        self.header.set_margin_end(12)
        self.header.set_margin_top(8)
        self.header.set_margin_bottom(8)

        # Make header clickable if we have content to show
        if self._has_expandable_content():
            click = Gtk.GestureClick()
            click.connect("pressed", self._on_header_clicked)
            self.header.add_controller(click)
            self.header.set_cursor_from_name("pointer")

        # Expand indicator
        self.expand_icon = Gtk.Image.new_from_icon_name("pan-end-symbolic")
        self.expand_icon.add_css_class("dim-label")
        if self._has_expandable_content():
            self.header.append(self.expand_icon)
        else:
            # Add spacer for alignment
            spacer = Gtk.Box()
            spacer.set_size_request(16, -1)
            self.header.append(spacer)

        # Tool icon
        icon_name = TOOL_ICONS.get(self.tool_name, DEFAULT_TOOL_ICON)
        icon = Gtk.Image.new_from_icon_name(icon_name)
        if self.tool_is_error:
            icon.add_css_class("error")
        else:
            icon.add_css_class("dim-label")
        self.header.append(icon)

        # Text content
        text_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        text_box.set_hexpand(True)

        # Tool name
        name_label = Gtk.Label(label=self.tool_name)
        name_label.set_xalign(0)
        name_label.add_css_class("heading")
        if self.tool_is_error:
            name_label.add_css_class("error")
        text_box.append(name_label)

        # Subtitle (file path, command, etc.)
        subtitle = self._get_subtitle()
        if subtitle:
            subtitle_label = Gtk.Label()
            subtitle_label.set_text(subtitle)
            subtitle_label.set_xalign(0)
            subtitle_label.add_css_class("dim-label")
            subtitle_label.set_ellipsize(3)  # PANGO_ELLIPSIZE_END
            subtitle_label.set_max_width_chars(60)
            text_box.append(subtitle_label)

        self.header.append(text_box)
        self.append(self.header)

        # Content area (hidden by default)
        self.content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.content_box.set_margin_start(12)
        self.content_box.set_margin_end(12)
        self.content_box.set_margin_bottom(12)
        self.content_box.set_visible(False)
        self.append(self.content_box)

    def _has_expandable_content(self) -> bool:
        """Check if this tool call has content worth expanding."""
        # Edit has diff
        if self.tool_name == "Edit":
            return bool(self.tool_input.get("old_string") or self.tool_input.get("new_string"))
        # Read, Bash, Grep, Glob have output
        if self.tool_name in ("Read", "Bash", "Grep", "Glob"):
            return bool(self.tool_output)
        # Write has content
        if self.tool_name == "Write":
            return bool(self.tool_input.get("content"))
        return False

    def _on_header_clicked(self, gesture, n_press, x, y):
        """Toggle expanded state."""
        self._expanded = not self._expanded

        if self._expanded:
            self.expand_icon.set_from_icon_name("pan-down-symbolic")
            self._build_content()
            self.content_box.set_visible(True)
        else:
            self.expand_icon.set_from_icon_name("pan-end-symbolic")
            # Clear content to save memory - collect children first
            children = []
            child = self.content_box.get_first_child()
            while child:
                children.append(child)
                child = child.get_next_sibling()
            for child in children:
                self.content_box.remove(child)
            self.content_box.set_visible(False)

    def _build_content(self):
        """Build the expandable content area."""
        # Clear existing content - collect children first
        children = []
        child = self.content_box.get_first_child()
        while child:
            children.append(child)
            child = child.get_next_sibling()
        for child in children:
            self.content_box.remove(child)

        if self.tool_name == "Edit":
            self._build_edit_content()
        elif self.tool_name == "Read":
            self._build_read_content()
        elif self.tool_name == "Write":
            self._build_write_content()
        elif self.tool_name == "Bash":
            self._build_bash_content()
        elif self.tool_name in ("Grep", "Glob"):
            self._build_search_content()

    def _build_edit_content(self):
        """Build content for Edit tool showing diff."""
        old_string = self.tool_input.get("old_string", "")
        new_string = self.tool_input.get("new_string", "")
        file_path = self.tool_input.get("file_path", "")

        diff_view = DiffView(old_string, new_string, file_path)
        self.content_box.append(diff_view)

    def _build_read_content(self):
        """Build content for Read tool showing file contents."""
        file_path = self.tool_input.get("file_path", "")
        output = self._truncate_output(self.tool_output)

        code_view = CodeView(
            output,
            file_path=file_path,
            show_line_numbers=True,
            max_lines=25,
        )
        self.content_box.append(code_view)

    def _build_write_content(self):
        """Build content for Write tool showing new file contents."""
        file_path = self.tool_input.get("file_path", "")
        content = self.tool_input.get("content", "")
        output = self._truncate_output(content)

        label = Gtk.Label(label="New content:")
        label.set_xalign(0)
        label.add_css_class("dim-label")
        self.content_box.append(label)

        code_view = CodeView(
            output,
            file_path=file_path,
            show_line_numbers=True,
            max_lines=25,
        )
        self.content_box.append(code_view)

    def _build_bash_content(self):
        """Build content for Bash tool showing command output."""
        output = self._truncate_output(self.tool_output)

        code_view = CodeView(
            output,
            language="sh",
            show_line_numbers=False,
            max_lines=20,
        )
        self.content_box.append(code_view)

    def _build_search_content(self):
        """Build content for Grep/Glob showing results."""
        output = self._truncate_output(self.tool_output)

        # Show as plain text with monospace
        label = Gtk.Label()
        label.set_text(output)
        label.set_wrap(True)
        label.set_xalign(0)
        label.set_selectable(True)
        label.add_css_class("monospace")

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_max_content_height(300)
        scrolled.set_propagate_natural_height(True)
        scrolled.set_child(label)

        self.content_box.append(scrolled)

    def _truncate_output(self, text: str) -> str:
        """Truncate long output with indicator."""
        if len(text) > self.MAX_OUTPUT_PREVIEW:
            return text[: self.MAX_OUTPUT_PREVIEW] + "\n\n... (truncated)"
        return text

    def _get_subtitle(self) -> str:
        """Get subtitle text based on tool type."""
        if self.tool_name == "Read":
            return self.tool_input.get("file_path", "")
        elif self.tool_name in ("Edit", "Write"):
            return self.tool_input.get("file_path", "")
        elif self.tool_name == "Bash":
            cmd = self.tool_input.get("command", "")
            # Truncate long commands
            if len(cmd) > 60:
                return cmd[:57] + "..."
            return cmd
        elif self.tool_name == "Glob":
            return self.tool_input.get("pattern", "")
        elif self.tool_name == "Grep":
            pattern = self.tool_input.get("pattern", "")
            path = self.tool_input.get("path", "")
            if path:
                return f"{pattern} in {path}"
            return pattern
        elif self.tool_name == "Task":
            return self.tool_input.get("description", "")
        elif self.tool_name == "WebFetch":
            return self.tool_input.get("url", "")
        elif self.tool_name == "WebSearch":
            return self.tool_input.get("query", "")
        return ""
