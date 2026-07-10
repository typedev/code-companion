"""Snippets bar widget for quick text insertion."""

from gi.repository import Gtk, Gdk, GObject, Adw, Pango

from ..services import SnippetsService, ToastService

# Snippets shown as inline header buttons; the rest go into a "..." popover.
MAX_INLINE = 6


class SnippetsBar(Gtk.Box):
    """Compact horizontal row of snippet buttons for the Query Editor header.

    The first MAX_INLINE snippets render as flat label-only buttons; any
    overflow collapses into a "..." popover with one row per snippet.
    Emits 'snippet-clicked' signal with the snippet text when clicked.
    Right-click on a snippet (inline or in the popover) to delete.
    """

    __gsignals__ = {
        "snippet-clicked": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)

        self.snippets_service = SnippetsService.get_instance()

        self._load_snippets()

        # Listen for snippet changes
        self.snippets_service.connect("changed", self._on_snippets_changed)

    def _load_snippets(self):
        """Rebuild the inline buttons and, if needed, the overflow popover."""
        while child := self.get_first_child():
            self.remove(child)

        snippets = self.snippets_service.get_all()
        for snippet in snippets[:MAX_INLINE]:
            self.append(self._make_button(snippet))

        overflow = snippets[MAX_INLINE:]
        if overflow:
            self.append(self._make_overflow_button(overflow))

    def _make_button(self, snippet: dict, popover: Gtk.Popover | None = None) -> Gtk.Button:
        """A flat snippet button; label-only inline, left-aligned row in the popover."""
        btn = Gtk.Button()
        btn.add_css_class("flat")
        btn.set_tooltip_text(
            (snippet["text"][:100] + "..." if len(snippet["text"]) > 100 else snippet["text"])
            + "\n\n(Right-click to delete)"
        )

        label = Gtk.Label(label=snippet["label"])
        label.set_ellipsize(Pango.EllipsizeMode.END)
        label.set_max_width_chars(20)
        if popover is not None:
            label.set_xalign(0)
        btn.set_child(label)

        btn.connect("clicked", self._on_button_clicked, snippet["text"], popover)

        # Right-click gesture for delete
        gesture = Gtk.GestureClick()
        gesture.set_button(Gdk.BUTTON_SECONDARY)
        gesture.connect("pressed", self._on_right_click, snippet["label"])
        btn.add_controller(gesture)

        return btn

    def _make_overflow_button(self, snippets: list[dict]) -> Gtk.MenuButton:
        """A '...' button holding the snippets that did not fit inline."""
        popover = Gtk.Popover()
        rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        for snippet in snippets:
            rows.append(self._make_button(snippet, popover))
        popover.set_child(rows)

        more_btn = Gtk.MenuButton()
        more_btn.add_css_class("flat")
        more_btn.set_icon_name("view-more-symbolic")
        more_btn.set_tooltip_text("More snippets")
        more_btn.set_popover(popover)
        return more_btn

    def _on_button_clicked(self, button, text: str, popover: Gtk.Popover | None):
        """Handle button click."""
        if popover is not None:
            popover.popdown()
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
