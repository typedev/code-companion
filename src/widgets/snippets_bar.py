"""Snippets bar widget for quick text insertion."""

from gi.repository import Gtk, Gdk, GObject, Adw, Pango

from ..services import SnippetsService, ToastService


class SnippetsBar(Gtk.Box):
    """Vertical list of snippet rows (styled like the Tasks panel).

    Emits 'snippet-clicked' signal with the snippet text when a row is clicked.
    Right-click on a snippet to delete.
    """

    __gsignals__ = {
        "snippet-clicked": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)

        self.snippets_service = SnippetsService.get_instance()

        self._build_ui()
        self._load_snippets()

        # Listen for snippet changes
        self.snippets_service.connect("changed", self._on_snippets_changed)

    def _build_ui(self):
        """Build the list UI (a vertical column, styled like the Tasks panel)."""
        self.set_margin_start(12)
        self.set_margin_end(12)
        self.set_margin_bottom(12)

        # One row per snippet, stacked vertically so a long list scales.
        self.button_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.append(self.button_box)

    def _load_snippets(self):
        """Load snippets and create one full-width row button each."""
        # Clear existing buttons
        while child := self.button_box.get_first_child():
            self.button_box.remove(child)

        # Add a row button for each snippet (icon + label, like a task row).
        snippets = self.snippets_service.get_all()
        for snippet in snippets:
            btn = Gtk.Button()
            btn.add_css_class("flat")
            btn.set_tooltip_text(
                (snippet["text"][:100] + "..." if len(snippet["text"]) > 100 else snippet["text"])
                + "\n\n(Right-click to delete)"
            )

            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            icon = Gtk.Image.new_from_icon_name("insert-text-symbolic")
            icon.add_css_class("dim-label")
            row.append(icon)
            label = Gtk.Label(label=snippet["label"])
            label.set_xalign(0)
            label.set_hexpand(True)
            label.set_ellipsize(Pango.EllipsizeMode.END)
            row.append(label)
            btn.set_child(row)

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
