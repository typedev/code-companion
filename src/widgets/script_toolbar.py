"""Script toolbar for Python/Shell files with Run and Outline buttons."""

from pathlib import Path

from gi.repository import Adw, Gtk, Gio, GObject, GLib

from ..services.python_outline import parse_python_outline, OutlineItem
from ..services.markdown_outline import parse_markdown_outline, MarkdownHeading


# CSS for outline color coding using Adwaita semantic colors
OUTLINE_CSS = """
.outline-class {
    color: @accent_color;
}
.outline-method {
    color: @success_color;
}
.outline-function {
    color: @warning_color;
}
.outline-h1 {
    color: @accent_color;
    font-weight: bold;
}
.outline-h2 {
    color: @accent_color;
}
.outline-h3 {
    color: @success_color;
}
.outline-h4, .outline-h5, .outline-h6 {
    color: @warning_color;
}
"""


class ScriptToolbar(Gtk.Box):
    """Toolbar for script files with Run button and Python outline."""

    __gsignals__ = {
        "run-script": (GObject.SignalFlags.RUN_FIRST, None, (str,)),  # args string
        "go-to-line": (GObject.SignalFlags.RUN_FIRST, None, (int,)),  # line number
        "toggle-preview": (GObject.SignalFlags.RUN_FIRST, None, (bool,)),  # is_preview_active
        "refresh-requested": (GObject.SignalFlags.RUN_FIRST, None, ()),  # reload file
        "save-requested": (GObject.SignalFlags.RUN_FIRST, None, ()),  # save file
    }

    def __init__(self, file_path: str):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL)
        self.file_path = file_path
        self._outline_items: list[OutlineItem] | list[MarkdownHeading] = []
        self._get_cursor_line_func = None  # Callback to get current cursor line
        self._file_ext = Path(file_path).suffix.lower()

        self.set_spacing(8)
        self.set_margin_start(8)
        self.set_margin_end(8)
        self.set_margin_top(4)
        self.set_margin_bottom(4)
        self.add_css_class("toolbar")

        self._setup_css()
        self._build_ui()

    def _setup_css(self):
        """Setup CSS for outline color coding."""
        css_provider = Gtk.CssProvider()
        css_provider.load_from_string(OUTLINE_CSS)
        Gtk.StyleContext.add_provider_for_display(
            self.get_display() or Gtk.Settings.get_default().get_display(),
            css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

    def _build_ui(self):
        """Build toolbar UI."""
        # Run button (only for scripts, not markdown)
        if self._file_ext in (".py", ".sh"):
            run_menu = Gio.Menu()
            run_menu.append("Run with arguments...", "toolbar.run-with-args")

            self.run_button = Adw.SplitButton()
            self.run_button.set_label("Run")
            self.run_button.set_icon_name("media-playback-start-symbolic")
            self.run_button.set_menu_model(run_menu)
            self.run_button.connect("clicked", self._on_run_clicked)

            # Action for menu item
            action_group = Gio.SimpleActionGroup()
            run_with_args_action = Gio.SimpleAction.new("run-with-args", None)
            run_with_args_action.connect("activate", self._on_run_with_args_clicked)
            action_group.add_action(run_with_args_action)
            self.insert_action_group("toolbar", action_group)

            self.append(self.run_button)

        # Outline button (for Python and Markdown files)
        if self._file_ext in (".py", ".md"):
            self.outline_button = Gtk.MenuButton()
            self.outline_button.set_label("Outline")
            self.outline_button.set_icon_name("view-list-symbolic")

            # Create popover for outline
            self.outline_popover = Gtk.Popover()
            self.outline_popover.set_has_arrow(True)

            # Scrolled window for outline list
            scrolled = Gtk.ScrolledWindow()
            scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
            scrolled.set_min_content_width(300)
            scrolled.set_max_content_width(500)
            scrolled.set_max_content_height(400)
            scrolled.set_propagate_natural_height(True)
            scrolled.set_propagate_natural_width(True)

            self.outline_list = Gtk.ListBox()
            self.outline_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
            self.outline_list.add_css_class("boxed-list")
            self.outline_list.connect("row-activated", self._on_outline_row_activated)

            self._scrolled = scrolled
            scrolled.set_child(self.outline_list)
            self.outline_popover.set_child(scrolled)

            # Sync selection when popover is shown
            self.outline_popover.connect("show", self._on_popover_show)

            self.outline_button.set_popover(self.outline_popover)
            self.append(self.outline_button)

        # Preview toggle button (only for Markdown files)
        if self._file_ext == ".md":
            self.preview_button = Gtk.ToggleButton()
            self.preview_button.set_label("Preview")
            self.preview_button.set_icon_name("view-reveal-symbolic")
            self.preview_button.connect("toggled", self._on_preview_toggled)
            self.append(self.preview_button)

        # Spacer to push filename to the right
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        self.append(spacer)

        # Filename label
        filename = Path(self.file_path).name
        self.filename_label = Gtk.Label(label=filename)
        self.filename_label.add_css_class("dim-label")
        self.append(self.filename_label)

        # Save button
        self.save_btn = Gtk.Button()
        self.save_btn.set_icon_name("document-save-symbolic")
        self.save_btn.add_css_class("flat")
        self.save_btn.set_tooltip_text("Save (Ctrl+S)")
        self.save_btn.set_sensitive(False)  # Disabled until modified
        self.save_btn.connect("clicked", lambda b: self.emit("save-requested"))
        self.append(self.save_btn)

        # Refresh button
        refresh_btn = Gtk.Button()
        refresh_btn.set_icon_name("view-refresh-symbolic")
        refresh_btn.add_css_class("flat")
        refresh_btn.set_tooltip_text("Reload file from disk")
        refresh_btn.connect("clicked", lambda b: self.emit("refresh-requested"))
        self.append(refresh_btn)

    def _on_run_clicked(self, button):
        """Handle Run button click - run without arguments."""
        self.emit("run-script", "")

    def _on_run_with_args_clicked(self, action, param):
        """Handle 'Run with arguments' menu item."""
        dialog = Adw.AlertDialog()
        dialog.set_heading("Run with Arguments")
        dialog.set_body(f"Enter command line arguments for {Path(self.file_path).name}:")

        # Entry for arguments
        entry = Gtk.Entry()
        entry.set_placeholder_text("arg1 arg2 --flag value")
        entry.set_hexpand(True)

        # Wrap in box with margins
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.set_margin_start(12)
        box.set_margin_end(12)
        box.append(entry)

        dialog.set_extra_child(box)

        dialog.add_response("cancel", "Cancel")
        dialog.add_response("run", "Run")
        dialog.set_response_appearance("run", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("run")

        # Connect Enter key in entry to run
        entry.connect("activate", lambda e: dialog.response("run"))

        dialog.connect("response", self._on_args_dialog_response, entry)

        # Get toplevel window
        toplevel = self.get_root()
        dialog.present(toplevel)

    def _on_args_dialog_response(self, dialog, response, entry):
        """Handle arguments dialog response."""
        if response == "run":
            args = entry.get_text().strip()
            self.emit("run-script", args)

    def _on_preview_toggled(self, button):
        """Handle preview toggle button."""
        is_active = button.get_active()
        self.emit("toggle-preview", is_active)

    def update_outline(self, source: str):
        """Update the outline from source code."""
        if not hasattr(self, "outline_list"):
            return

        # Parse outline based on file type
        if self._file_ext == ".py":
            self._outline_items = parse_python_outline(source)
            empty_message = "No classes or functions found"
        elif self._file_ext == ".md":
            self._outline_items = parse_markdown_outline(source)
            empty_message = "No headings found"
        else:
            return

        # Clear and rebuild list
        self.outline_list.remove_all()

        if not self._outline_items:
            # Show empty state
            label = Gtk.Label(label=empty_message)
            label.add_css_class("dim-label")
            label.set_margin_top(12)
            label.set_margin_bottom(12)
            label.set_margin_start(12)
            label.set_margin_end(12)
            self.outline_list.append(label)
            return

        for item in self._outline_items:
            if self._file_ext == ".py":
                row = self._create_python_row(item)
            else:
                row = self._create_markdown_row(item)
            self.outline_list.append(row)

    def _create_python_row(self, item: OutlineItem) -> Gtk.ListBoxRow:
        """Create a row for a Python outline item."""
        row = Gtk.ListBoxRow()
        row.item = item  # Store reference

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        box.set_margin_start(8)
        box.set_margin_end(8)
        box.set_margin_top(6)
        box.set_margin_bottom(6)

        # Indent methods
        if item.kind == "method":
            box.set_margin_start(24)

        # Name label with color coding
        label = Gtk.Label(label=item.display_name)
        label.set_xalign(0)

        # Add CSS class based on item kind
        if item.kind == "class":
            label.add_css_class("outline-class")
        elif item.kind == "method":
            label.add_css_class("outline-method")
        else:  # function
            label.add_css_class("outline-function")

        box.append(label)

        # Line number
        line_label = Gtk.Label(label=f":{item.line}")
        line_label.add_css_class("dim-label")
        line_label.set_hexpand(True)
        line_label.set_xalign(1)
        box.append(line_label)

        row.set_child(box)
        return row

    def _create_markdown_row(self, item: MarkdownHeading) -> Gtk.ListBoxRow:
        """Create a row for a Markdown heading."""
        row = Gtk.ListBoxRow()
        row.item = item  # Store reference

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        box.set_margin_end(8)
        box.set_margin_top(6)
        box.set_margin_bottom(6)

        # Indent based on heading level (h1=8, h2=16, h3=24, etc.)
        indent = 8 + (item.level - 1) * 12
        box.set_margin_start(indent)

        # Heading text with color coding
        label = Gtk.Label(label=item.display_name)
        label.set_xalign(0)
        label.set_ellipsize(3)  # PANGO_ELLIPSIZE_END
        label.set_max_width_chars(40)

        # Add CSS class based on heading level
        label.add_css_class(f"outline-h{item.level}")

        box.append(label)

        # Line number
        line_label = Gtk.Label(label=f":{item.line}")
        line_label.add_css_class("dim-label")
        line_label.set_hexpand(True)
        line_label.set_xalign(1)
        box.append(line_label)

        row.set_child(box)
        return row

    def _on_outline_row_activated(self, listbox, row):
        """Handle outline row activation - go to line."""
        if hasattr(row, "item"):
            self.emit("go-to-line", row.item.line)
            self.outline_popover.popdown()

    def _on_popover_show(self, popover):
        """Handle popover show - sync selection with cursor position."""
        if self._get_cursor_line_func:
            cursor_line = self._get_cursor_line_func()
            if cursor_line > 0:
                self._select_item_at_line(cursor_line)

    def set_cursor_line_callback(self, func):
        """Set callback function to get current cursor line."""
        self._get_cursor_line_func = func

    def _select_item_at_line(self, cursor_line: int):
        """Select the outline item containing the cursor line."""
        if not self._outline_items:
            return

        # Find the item that contains this line
        # (the last item whose line <= cursor_line)
        selected_item = None
        selected_index = -1

        for i, item in enumerate(self._outline_items):
            if item.line <= cursor_line:
                selected_item = item
                selected_index = i
            else:
                break

        if selected_index >= 0:
            row = self.outline_list.get_row_at_index(selected_index)
            if row:
                self.outline_list.select_row(row)
                # Scroll to the selected row after a brief delay
                GLib.idle_add(self._scroll_to_row, row)

    def _scroll_to_row(self, row) -> bool:
        """Scroll the outline list to show the given row."""
        # Get the row's allocation within the list
        adj = self._scrolled.get_vadjustment()
        if adj:
            # Get row position
            _, row_y = row.translate_coordinates(self.outline_list, 0, 0)
            row_height = row.get_height()

            # Calculate visible area
            visible_start = adj.get_value()
            visible_height = adj.get_page_size()
            visible_end = visible_start + visible_height

            # Scroll if row is not fully visible
            if row_y < visible_start:
                adj.set_value(row_y)
            elif row_y + row_height > visible_end:
                adj.set_value(row_y + row_height - visible_height)

        return False  # Don't repeat

    def set_modified(self, is_modified: bool):
        """Update toolbar to reflect modified state."""
        filename = Path(self.file_path).name
        if is_modified:
            self.filename_label.set_text(f"● {filename}")
            self.filename_label.remove_css_class("dim-label")
        else:
            self.filename_label.set_text(filename)
            self.filename_label.add_css_class("dim-label")
        self.save_btn.set_sensitive(is_modified)
