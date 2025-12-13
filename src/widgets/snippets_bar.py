"""Snippets bar widget for quick text insertion."""

from gi.repository import Gtk, Gdk, GObject, Adw

from ..services import SnippetsService, ToastService


class SnippetsBar(Gtk.Box):
    """Horizontal scrollable bar with snippet buttons.

    Emits 'snippet-clicked' signal with the snippet text when a button is clicked.
    Right-click on snippet to delete.
    """

    __gsignals__ = {
        "snippet-clicked": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL)

        self.snippets_service = SnippetsService.get_instance()

        self._build_ui()
        self._load_snippets()

        # Listen for snippet changes
        self.snippets_service.connect("changed", self._on_snippets_changed)

    def _build_ui(self):
        """Build the bar UI."""
        # Set fixed height
        self.set_size_request(-1, 36)

        # Add some padding
        self.set_margin_start(4)
        self.set_margin_end(4)
        self.set_margin_top(4)
        self.set_margin_bottom(4)

        # Scrolled window for horizontal scrolling
        self.scrolled = Gtk.ScrolledWindow()
        self.scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        self.scrolled.set_hexpand(True)

        # Container for buttons
        self.button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)

        self.scrolled.set_child(self.button_box)
        self.append(self.scrolled)

    def _load_snippets(self):
        """Load snippets and create buttons."""
        # Clear existing buttons
        while child := self.button_box.get_first_child():
            self.button_box.remove(child)

        # Add buttons for each snippet
        snippets = self.snippets_service.get_all()
        for snippet in snippets:
            # Create button with delete suffix on long press / right click
            btn = Gtk.Button(label=snippet["label"])
            btn.add_css_class("flat")
            btn.set_tooltip_text(
                (snippet["text"][:100] + "..." if len(snippet["text"]) > 100 else snippet["text"])
                + "\n\n(Right-click to delete)"
            )
            btn.connect("clicked", self._on_button_clicked, snippet["text"])

            # Add right-click gesture for delete
            gesture = Gtk.GestureClick()
            gesture.set_button(Gdk.BUTTON_SECONDARY)  # Right click
            gesture.connect("pressed", self._on_right_click, snippet["label"])
            btn.add_controller(gesture)

            self.button_box.append(btn)

    def _on_button_clicked(self, button, text: str):
        """Handle button click."""
        self.emit("snippet-clicked", text)

    def _on_right_click(self, gesture, n_press, x, y, label: str):
        """Handle right-click on snippet button - show delete confirmation directly."""
        # Find parent window for dialog
        widget = self
        while widget and not isinstance(widget, Gtk.Window):
            widget = widget.get_parent()

        if widget:
            self._show_delete_confirmation(widget, label)

    def _show_delete_confirmation(self, parent: Gtk.Window, label: str):
        """Show delete confirmation dialog."""
        dialog = Adw.AlertDialog()
        dialog.set_heading("Delete Snippet?")
        dialog.set_body(f"Delete snippet \"{label}\"?")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("delete", "Delete")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        dialog.connect("response", self._on_delete_response, label)
        dialog.present(parent)

    def _on_delete_response(self, dialog, response: str, label: str):
        """Handle delete confirmation response."""
        if response == "delete":
            if self.snippets_service.delete(label):
                ToastService.show(f"Deleted: {label}")
            else:
                ToastService.show_error(f"Failed to delete: {label}")

    def _on_snippets_changed(self, service):
        """Handle snippets service change."""
        self._load_snippets()
