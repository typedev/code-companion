"""Collapsible thinking block widget."""

from gi.repository import Adw, Gtk, GLib


class ThinkingBlock(Gtk.Box):
    """A collapsible block for displaying Claude's thinking content."""

    def __init__(self, thinking_text: str):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)

        self.thinking_text = thinking_text
        self._expanded = False

        self._build_ui()

    def _build_ui(self):
        """Build the thinking block UI."""
        # Expander row style button
        self.header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.header.add_css_class("dim-label")

        # Expand icon
        self.expand_icon = Gtk.Image.new_from_icon_name("pan-end-symbolic")
        self.header.append(self.expand_icon)

        # Label
        label = Gtk.Label(label="Thinking...")
        label.set_xalign(0)
        self.header.append(label)

        # Make header clickable
        click = Gtk.GestureClick()
        click.connect("pressed", self._on_header_clicked)
        self.header.add_controller(click)

        # Set cursor to pointer
        self.header.set_cursor_from_name("pointer")

        self.append(self.header)

        # Content (hidden by default)
        self.content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.content_box.set_margin_start(22)
        self.content_box.set_margin_top(6)
        self.content_box.set_visible(False)

        content_label = Gtk.Label()
        content_label.set_text(self.thinking_text)
        content_label.set_wrap(True)
        content_label.set_xalign(0)
        content_label.add_css_class("dim-label")
        content_label.set_selectable(True)

        self.content_box.append(content_label)
        self.append(self.content_box)

    def _on_header_clicked(self, gesture, n_press, x, y):
        """Toggle expanded state."""
        self._expanded = not self._expanded
        self.content_box.set_visible(self._expanded)

        if self._expanded:
            self.expand_icon.set_from_icon_name("pan-down-symbolic")
        else:
            self.expand_icon.set_from_icon_name("pan-end-symbolic")
