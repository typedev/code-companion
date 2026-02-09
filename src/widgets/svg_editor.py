"""SVG editor with live preview: code editor (left) + rendered preview (right)."""

from pathlib import Path

import gi

gi.require_version("GtkSource", "5")

from gi.repository import Gtk, GtkSource, GLib, GObject, Pango, Adw, Gdk, GdkPixbuf, Gio

from .code_view import get_language_for_file
from .script_toolbar import ScriptToolbar
from .image_viewer import PixelImage, _pixbuf_to_cairo_surface, _get_display_scale
from ..services import ToastService, SettingsService


# Checkerboard CSS for transparency visualization
CHECKERBOARD_CSS = """
.checkerboard {
    background-color: #cccccc;
    background-image:
        linear-gradient(45deg, #999 25%, transparent 25%, transparent 75%, #999 75%),
        linear-gradient(45deg, #999 25%, transparent 25%, transparent 75%, #999 75%);
    background-size: 16px 16px;
    background-position: 0 0, 8px 8px;
}
"""


class SvgEditor(Gtk.Box):
    """A widget for editing SVG files with live preview."""

    __gsignals__ = {
        "modified-changed": (GObject.SignalFlags.RUN_FIRST, None, (bool,)),
        "run-requested": (GObject.SignalFlags.RUN_FIRST, None, (str, str)),
    }

    def __init__(self, file_path: str):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.set_vexpand(True)
        self.set_hexpand(True)

        self.file_path = file_path
        self._modified = False
        self._preview_debounce_id = None
        self._last_valid_surface = None  # Cairo ImageSurface for preview
        self._last_valid_pixbuf = None
        self._zoom = 1.0
        self._fit_mode = True

        # Search state
        self._search_context = None
        self._search_settings = None

        self._setup_css()
        self._build_ui()
        self._load_file()
        self._setup_search()

    def _setup_css(self):
        """Setup checkerboard CSS."""
        css_provider = Gtk.CssProvider()
        css_provider.load_from_string(CHECKERBOARD_CSS)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

    def _build_ui(self):
        """Build the editor UI."""
        self.settings = SettingsService.get_instance()

        # Script toolbar (save + refresh for SVG)
        self.script_toolbar = ScriptToolbar(self.file_path)
        self.script_toolbar.connect("refresh-requested", self._on_refresh_requested)
        self.script_toolbar.connect("save-requested", self._on_save_requested)
        self.append(self.script_toolbar)

        # Paned: editor (left) + preview (right)
        self._paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        self._paned.set_vexpand(True)
        self._paned.set_hexpand(True)
        self._paned.set_shrink_start_child(False)
        self._paned.set_shrink_end_child(False)

        # --- Left: Code editor ---
        self.buffer = GtkSource.Buffer()
        self.source_view = GtkSource.View(buffer=self.buffer)

        self.source_view.set_editable(True)
        self.source_view.set_cursor_visible(True)
        self.source_view.set_show_line_numbers(True)
        self.source_view.set_monospace(True)
        self.source_view.set_auto_indent(True)
        self.source_view.set_indent_on_tab(True)
        self.source_view.set_highlight_current_line(True)

        self._apply_settings()

        self.buffer.set_max_undo_levels(10000)

        # Set XML language for SVG
        lang_manager = GtkSource.LanguageManager.get_default()
        language = lang_manager.get_language("xml")
        if language:
            self.buffer.set_language(language)

        self.settings.connect("changed", self._on_setting_changed)
        self.buffer.connect("changed", self._on_buffer_changed)

        editor_scrolled = Gtk.ScrolledWindow()
        editor_scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        editor_scrolled.set_vexpand(True)
        editor_scrolled.set_hexpand(True)
        editor_scrolled.set_child(self.source_view)

        self._paned.set_start_child(editor_scrolled)

        # --- Right: Preview ---
        preview_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        preview_box.set_hexpand(True)
        preview_box.set_vexpand(True)

        # Preview toolbar
        preview_toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        preview_toolbar.set_margin_start(4)
        preview_toolbar.set_margin_end(4)
        preview_toolbar.set_margin_top(2)
        preview_toolbar.set_margin_bottom(2)

        preview_label = Gtk.Label(label="Preview")
        preview_label.add_css_class("dim-label")
        preview_toolbar.append(preview_label)

        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        preview_toolbar.append(spacer)

        fit_btn = Gtk.Button(label="Fit")
        fit_btn.add_css_class("flat")
        fit_btn.set_tooltip_text("Fit to panel")
        fit_btn.connect("clicked", lambda b: self._set_fit_mode())
        preview_toolbar.append(fit_btn)

        one_btn = Gtk.Button(label="1:1")
        one_btn.add_css_class("flat")
        one_btn.set_tooltip_text("Original size")
        one_btn.connect("clicked", lambda b: self._set_preview_zoom(1.0))
        preview_toolbar.append(one_btn)

        zoom_out_btn = Gtk.Button()
        zoom_out_btn.set_icon_name("zoom-out-symbolic")
        zoom_out_btn.add_css_class("flat")
        zoom_out_btn.connect("clicked", lambda b: self._preview_zoom_step(-1))
        preview_toolbar.append(zoom_out_btn)

        zoom_in_btn = Gtk.Button()
        zoom_in_btn.set_icon_name("zoom-in-symbolic")
        zoom_in_btn.add_css_class("flat")
        zoom_in_btn.connect("clicked", lambda b: self._preview_zoom_step(1))
        preview_toolbar.append(zoom_in_btn)

        self._zoom_label = Gtk.Label(label="Fit")
        self._zoom_label.set_width_chars(6)
        preview_toolbar.append(self._zoom_label)

        preview_box.append(preview_toolbar)

        # Preview area with overlay for error message
        self._preview_overlay = Gtk.Overlay()
        self._preview_overlay.set_vexpand(True)
        self._preview_overlay.set_hexpand(True)

        # Scrolled window for preview
        self._preview_scrolled = Gtk.ScrolledWindow()
        self._preview_scrolled.set_vexpand(True)
        self._preview_scrolled.set_hexpand(True)

        # Image container with checkerboard
        self._image_box = Gtk.Box()
        self._image_box.add_css_class("checkerboard")
        self._image_box.set_halign(Gtk.Align.CENTER)
        self._image_box.set_valign(Gtk.Align.CENTER)

        self._picture = PixelImage()
        self._picture.set_hexpand(True)
        self._picture.set_vexpand(True)
        self._image_box.append(self._picture)

        self._preview_scrolled.set_child(self._image_box)
        self._preview_overlay.set_child(self._preview_scrolled)

        # Error label overlay
        self._error_label = Gtk.Label(label="")
        self._error_label.add_css_class("error")
        self._error_label.set_wrap(True)
        self._error_label.set_halign(Gtk.Align.CENTER)
        self._error_label.set_valign(Gtk.Align.END)
        self._error_label.set_margin_bottom(8)
        self._error_label.set_margin_start(8)
        self._error_label.set_margin_end(8)
        self._error_label.set_visible(False)

        # Wrap error label in a styled box
        error_box = Gtk.Box()
        error_box.set_halign(Gtk.Align.CENTER)
        error_box.set_valign(Gtk.Align.END)
        error_box.set_margin_bottom(8)
        error_box.add_css_class("card")
        error_box.set_margin_start(16)
        error_box.set_margin_end(16)
        self._error_label.set_margin_bottom(4)
        self._error_label.set_margin_top(4)
        self._error_label.set_margin_start(8)
        self._error_label.set_margin_end(8)
        error_box.append(self._error_label)
        self._error_box = error_box
        self._error_box.set_visible(False)
        self._preview_overlay.add_overlay(self._error_box)

        preview_box.append(self._preview_overlay)
        self._apply_preview_fit_mode()

        self._paned.set_end_child(preview_box)

        self.append(self._paned)

        # Set initial pane position after realization
        self._paned.connect("realize", self._on_paned_realize)

        # Search bar (hidden by default)
        self._build_search_bar()

        # Keyboard shortcuts
        self._setup_shortcuts()

    def _on_paned_realize(self, paned):
        """Set initial pane position to 50%."""
        GLib.idle_add(self._set_initial_position)

    def _set_initial_position(self):
        """Set the paned position to half the allocated width."""
        width = self._paned.get_allocated_width()
        if width > 0:
            self._paned.set_position(width // 2)
        return False

    # --- Preview zoom ---

    def _set_fit_mode(self):
        """Switch preview to fit mode."""
        self._fit_mode = True
        self._apply_preview_fit_mode()
        self._zoom_label.set_text("Fit")

    def _apply_preview_fit_mode(self):
        """Apply fit mode to preview."""
        self._picture.set_fit_mode()
        self._picture.set_hexpand(True)
        self._picture.set_vexpand(True)
        self._image_box.set_halign(Gtk.Align.FILL)
        self._image_box.set_valign(Gtk.Align.FILL)
        self._image_box.set_hexpand(True)
        self._image_box.set_vexpand(True)
        self._preview_scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.NEVER)

    def _set_preview_zoom(self, zoom: float):
        """Set a specific preview zoom level."""
        zoom = max(0.10, min(10.0, zoom))
        self._zoom = zoom
        self._fit_mode = False
        self._apply_preview_zoom()
        self._zoom_label.set_text(f"{int(zoom * 100)}%")

    def _apply_preview_zoom(self):
        """Apply current zoom to preview."""
        if not self._last_valid_surface:
            return
        scale = _get_display_scale(self)
        src_w = self._last_valid_surface.get_width()
        src_h = self._last_valid_surface.get_height()
        dev_w = round(src_w * self._zoom)
        dev_h = round(src_h * self._zoom)
        css_w = dev_w / scale
        css_h = dev_h / scale
        self._picture.set_zoom(self._zoom, css_w, css_h)
        self._picture.set_hexpand(False)
        self._picture.set_vexpand(False)
        self._image_box.set_halign(Gtk.Align.CENTER)
        self._image_box.set_valign(Gtk.Align.CENTER)
        self._image_box.set_hexpand(False)
        self._image_box.set_vexpand(False)
        self._preview_scrolled.set_policy(
            Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC
        )

    def _preview_zoom_step(self, direction: int):
        """Zoom preview in or out by a step."""
        if self._fit_mode:
            self._zoom = 1.0
        step = 0.25
        self._set_preview_zoom(self._zoom + direction * step)

    # --- Search bar (reused from FileEditor pattern) ---

    def _build_search_bar(self):
        """Build the search/replace bar."""
        self.search_bar = Gtk.Revealer()
        self.search_bar.set_reveal_child(False)

        search_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        search_box.add_css_class("search-bar")
        search_box.set_margin_start(8)
        search_box.set_margin_end(8)
        search_box.set_margin_top(4)
        search_box.set_margin_bottom(4)

        # Search row
        search_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)

        self.search_entry = Gtk.SearchEntry()
        self.search_entry.set_hexpand(True)
        self.search_entry.set_placeholder_text("Find...")
        self.search_entry.connect("search-changed", self._on_search_changed)
        self.search_entry.connect("activate", self._on_search_next)
        self.search_entry.connect("next-match", self._on_search_next)
        self.search_entry.connect("previous-match", self._on_search_prev)
        search_row.append(self.search_entry)

        self.match_label = Gtk.Label(label="")
        self.match_label.add_css_class("dim-label")
        self.match_label.set_width_chars(8)
        search_row.append(self.match_label)

        prev_btn = Gtk.Button()
        prev_btn.set_icon_name("go-up-symbolic")
        prev_btn.set_tooltip_text("Previous match (Shift+Enter)")
        prev_btn.add_css_class("flat")
        prev_btn.connect("clicked", lambda b: self._on_search_prev())
        search_row.append(prev_btn)

        next_btn = Gtk.Button()
        next_btn.set_icon_name("go-down-symbolic")
        next_btn.set_tooltip_text("Next match (Enter)")
        next_btn.add_css_class("flat")
        next_btn.connect("clicked", lambda b: self._on_search_next())
        search_row.append(next_btn)

        self.case_btn = Gtk.ToggleButton()
        self.case_btn.set_icon_name("font-x-generic-symbolic")
        self.case_btn.set_tooltip_text("Match case")
        self.case_btn.add_css_class("flat")
        self.case_btn.connect("toggled", self._on_search_options_changed)
        search_row.append(self.case_btn)

        self.regex_btn = Gtk.ToggleButton()
        self.regex_btn.set_label(".*")
        self.regex_btn.set_tooltip_text("Regular expression")
        self.regex_btn.add_css_class("flat")
        self.regex_btn.connect("toggled", self._on_search_options_changed)
        search_row.append(self.regex_btn)

        # Replace toggle
        self.replace_toggle = Gtk.ToggleButton()
        self.replace_toggle.set_icon_name("edit-find-replace-symbolic")
        self.replace_toggle.set_tooltip_text("Show replace")
        self.replace_toggle.add_css_class("flat")
        self.replace_toggle.connect("toggled", self._on_replace_toggled)
        search_row.append(self.replace_toggle)

        close_btn = Gtk.Button()
        close_btn.set_icon_name("window-close-symbolic")
        close_btn.set_tooltip_text("Close (Escape)")
        close_btn.add_css_class("flat")
        close_btn.connect("clicked", lambda b: self.hide_search())
        search_row.append(close_btn)

        search_box.append(search_row)

        # Replace row
        self.replace_revealer = Gtk.Revealer()
        self.replace_revealer.set_reveal_child(False)

        replace_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)

        self.replace_entry = Gtk.Entry()
        self.replace_entry.set_hexpand(True)
        self.replace_entry.set_placeholder_text("Replace with...")
        replace_row.append(self.replace_entry)

        replace_btn = Gtk.Button(label="Replace")
        replace_btn.add_css_class("flat")
        replace_btn.connect("clicked", lambda b: self._on_replace())
        replace_row.append(replace_btn)

        replace_all_btn = Gtk.Button(label="All")
        replace_all_btn.add_css_class("flat")
        replace_all_btn.connect("clicked", lambda b: self._on_replace_all())
        replace_row.append(replace_all_btn)

        self.replace_revealer.set_child(replace_row)
        search_box.append(self.replace_revealer)

        self.search_bar.set_child(search_box)
        self.append(self.search_bar)

    def _setup_shortcuts(self):
        """Set up keyboard shortcuts."""
        key_controller = Gtk.EventControllerKey()
        key_controller.connect("key-pressed", self._on_key_pressed)
        self.source_view.add_controller(key_controller)

    def _on_key_pressed(self, controller, keyval, keycode, state):
        """Handle keyboard shortcuts."""
        ctrl = state & Gdk.ModifierType.CONTROL_MASK
        shift = state & Gdk.ModifierType.SHIFT_MASK

        if ctrl and keyval == Gdk.KEY_f:
            self.show_search()
            return True
        elif ctrl and keyval == Gdk.KEY_s:
            self.save()
            return True
        elif ctrl and keyval == Gdk.KEY_z:
            if shift:
                self.redo()
            else:
                self.undo()
            return True
        elif ctrl and keyval == Gdk.KEY_y:
            self.redo()
            return True
        elif keyval == Gdk.KEY_Escape:
            if self.search_bar.get_reveal_child():
                self.hide_search()
                return True
        return False

    # --- Search methods ---

    def show_search(self, replace: bool = False):
        """Show the search bar."""
        self.search_bar.set_reveal_child(True)
        self.search_entry.grab_focus()
        if replace:
            self.replace_toggle.set_active(True)
        if not self.buffer.get_has_selection():
            self._select_current_word()
        if self.buffer.get_has_selection():
            start, end = self.buffer.get_selection_bounds()
            text = self.buffer.get_text(start, end, False)
            if "\n" not in text:
                self.search_entry.set_text(text)
                self.search_entry.select_region(0, -1)

    def hide_search(self):
        """Hide the search bar."""
        self.search_bar.set_reveal_child(False)
        if self._search_context:
            self._search_settings.set_search_text("")
        self.source_view.grab_focus()

    def _select_current_word(self):
        """Select the word at cursor."""
        mark = self.buffer.get_insert()
        iter_at_cursor = self.buffer.get_iter_at_mark(mark)
        start = iter_at_cursor.copy()
        if not start.starts_word():
            start.backward_word_start()
        end = iter_at_cursor.copy()
        if not end.ends_word():
            end.forward_word_end()
        if start.compare(end) < 0:
            self.buffer.select_range(start, end)

    def _on_replace_toggled(self, button):
        """Toggle replace row visibility."""
        self.replace_revealer.set_reveal_child(button.get_active())

    def _setup_search(self):
        """Set up search context."""
        self._search_settings = GtkSource.SearchSettings()
        self._search_settings.set_wrap_around(True)
        self._search_context = GtkSource.SearchContext(
            buffer=self.buffer, settings=self._search_settings
        )
        self._search_context.set_highlight(True)

    def _on_search_changed(self, entry):
        """Handle search text changes."""
        text = entry.get_text()
        self._search_settings.set_search_text(text)
        self._update_match_count()
        if text:
            self._on_search_next()

    def _on_search_options_changed(self, button):
        """Handle search option toggle."""
        self._search_settings.set_case_sensitive(self.case_btn.get_active())
        self._search_settings.set_regex_enabled(self.regex_btn.get_active())
        self._update_match_count()

    def _on_search_next(self, *args):
        """Find next match."""
        if not self._search_context:
            return
        mark = self.buffer.get_insert()
        start_iter = self.buffer.get_iter_at_mark(mark)
        found, match_start, match_end, wrapped = self._search_context.forward(start_iter)
        if found:
            self.buffer.select_range(match_start, match_end)
            self.source_view.scroll_to_mark(self.buffer.get_insert(), 0.2, False, 0, 0)
        self._update_match_count()

    def _on_search_prev(self, *args):
        """Find previous match."""
        if not self._search_context:
            return
        mark = self.buffer.get_insert()
        start_iter = self.buffer.get_iter_at_mark(mark)
        found, match_start, match_end, wrapped = self._search_context.backward(start_iter)
        if found:
            self.buffer.select_range(match_start, match_end)
            self.source_view.scroll_to_mark(self.buffer.get_insert(), 0.2, False, 0, 0)
        self._update_match_count()

    def _update_match_count(self):
        """Update the match count label."""
        count = self._search_context.get_occurrences_count()
        if count == -1:
            self.match_label.set_text("...")
        elif count == 0:
            self.match_label.set_text("No results")
            self.search_entry.add_css_class("error")
        else:
            self.match_label.set_text(f"{count} found")
            self.search_entry.remove_css_class("error")

    def _on_replace(self):
        """Replace current match."""
        if not self.buffer.get_has_selection():
            self._on_search_next()
            return
        replace_text = self.replace_entry.get_text()
        start, end = self.buffer.get_selection_bounds()
        selected = self.buffer.get_text(start, end, False)
        search_text = self.search_entry.get_text()
        if self.regex_btn.get_active():
            import re
            flags = 0 if self.case_btn.get_active() else re.IGNORECASE
            if re.fullmatch(search_text, selected, flags):
                self._search_context.replace(start, end, replace_text, len(replace_text))
                self._on_search_next()
        else:
            if self.case_btn.get_active():
                matches = selected == search_text
            else:
                matches = selected.lower() == search_text.lower()
            if matches:
                self._search_context.replace(start, end, replace_text, len(replace_text))
                self._on_search_next()

    def _on_replace_all(self):
        """Replace all matches."""
        replace_text = self.replace_entry.get_text()
        count = self._search_context.replace_all(replace_text, len(replace_text))
        if count > 0:
            ToastService.show(f"Replaced {count} occurrences")
        self._update_match_count()

    # --- Settings ---

    def _apply_settings(self):
        """Apply all settings to the editor."""
        scheme_id = self.settings.get("appearance.syntax_scheme", "Adwaita-dark")
        style_manager = GtkSource.StyleSchemeManager.get_default()
        scheme = style_manager.get_scheme(scheme_id)
        if not scheme:
            scheme = style_manager.get_scheme("Adwaita-dark")
        if not scheme:
            scheme = style_manager.get_scheme("classic")
        if scheme:
            self.buffer.set_style_scheme(scheme)

        font_family = self.settings.get("editor.font_family", "Monospace")
        font_size = self.settings.get("editor.font_size", 12)
        line_height = self.settings.get("editor.line_height", 1.4)

        css_provider = Gtk.CssProvider()
        css = f"""
            textview {{
                font-family: "{font_family}";
                font-size: {font_size}pt;
                line-height: {line_height};
            }}
        """
        css_provider.load_from_string(css)
        self.source_view.get_style_context().add_provider(
            css_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        self._css_provider = css_provider

        tab_size = self.settings.get("editor.tab_size", 4)
        insert_spaces = self.settings.get("editor.insert_spaces", True)
        self.source_view.set_tab_width(tab_size)
        self.source_view.set_insert_spaces_instead_of_tabs(insert_spaces)

        word_wrap = self.settings.get("editor.word_wrap", True)
        wrap_mode = Gtk.WrapMode.WORD_CHAR if word_wrap else Gtk.WrapMode.NONE
        self.source_view.set_wrap_mode(wrap_mode)

    def _on_setting_changed(self, settings, key, value):
        """Handle settings changes."""
        if key.startswith("appearance.") or key.startswith("editor."):
            self._apply_settings()

    # --- File operations ---

    def _load_file(self):
        """Load file content into buffer."""
        try:
            with open(self.file_path, "r", encoding="utf-8") as f:
                content = f.read()
            self.buffer.set_text(content)
            self.buffer.set_modified(False)
            self._modified = False
            self.buffer.place_cursor(self.buffer.get_start_iter())
            # Initial preview
            self._update_preview()
        except (OSError, UnicodeDecodeError) as e:
            self.buffer.set_text(f"Error loading file: {e}")
            self.source_view.set_editable(False)

    def _on_buffer_changed(self, buffer):
        """Handle buffer changes."""
        is_modified = buffer.get_modified()
        if is_modified != self._modified:
            self._modified = is_modified
            self.script_toolbar.set_modified(is_modified)
            self.emit("modified-changed", is_modified)

        # Debounce preview update
        if self._preview_debounce_id:
            GLib.source_remove(self._preview_debounce_id)
        self._preview_debounce_id = GLib.timeout_add(500, self._update_preview)

    def _update_preview(self) -> bool:
        """Update the SVG preview from buffer content."""
        self._preview_debounce_id = None

        start = self.buffer.get_start_iter()
        end = self.buffer.get_end_iter()
        svg_text = self.buffer.get_text(start, end, True)

        if not svg_text.strip():
            return False

        try:
            # Load SVG via GdkPixbuf from memory
            svg_bytes = svg_text.encode("utf-8")
            stream = Gio.MemoryInputStream.new_from_data(svg_bytes)
            pixbuf = GdkPixbuf.Pixbuf.new_from_stream(stream, None)
            surface = _pixbuf_to_cairo_surface(pixbuf)

            self._last_valid_pixbuf = pixbuf
            self._last_valid_surface = surface
            self._picture.set_surface(surface)

            # Hide error
            self._error_label.set_visible(False)
            self._error_box.set_visible(False)

            # Reapply zoom if not in fit mode
            if not self._fit_mode:
                self._apply_preview_zoom()

        except Exception as e:
            # Show error but keep last valid preview
            error_msg = str(e)
            # Truncate long error messages
            if len(error_msg) > 120:
                error_msg = error_msg[:120] + "..."
            self._error_label.set_text(f"SVG Error: {error_msg}")
            self._error_label.set_visible(True)
            self._error_box.set_visible(True)

        return False  # Don't repeat

    def _on_refresh_requested(self, toolbar=None):
        """Handle refresh button click."""
        if self._modified:
            dialog = Adw.AlertDialog()
            dialog.set_heading("Discard Changes?")
            dialog.set_body("The file has unsaved changes. Reload from disk and discard them?")
            dialog.add_response("cancel", "Cancel")
            dialog.add_response("discard", "Discard & Reload")
            dialog.set_response_appearance("discard", Adw.ResponseAppearance.DESTRUCTIVE)
            dialog.connect("response", self._on_reload_response)
            dialog.present(self.get_root())
        else:
            self.reload()

    def _on_reload_response(self, dialog, response):
        """Handle reload confirmation."""
        if response == "discard":
            self.reload()

    def reload(self):
        """Reload file from disk."""
        mark = self.buffer.get_insert()
        iter_at_cursor = self.buffer.get_iter_at_mark(mark)
        line = iter_at_cursor.get_line()
        offset = iter_at_cursor.get_line_offset()

        self._load_file()

        success, new_iter = self.buffer.get_iter_at_line_offset(line, 0)
        if success:
            line_end = new_iter.copy()
            if not line_end.ends_line():
                line_end.forward_to_line_end()
            max_offset = line_end.get_line_offset()
            if offset <= max_offset:
                new_iter.set_line_offset(offset)
            else:
                new_iter = line_end
            self.buffer.place_cursor(new_iter)

        ToastService.show("File reloaded")

    def _on_save_requested(self, toolbar):
        """Handle save request from toolbar."""
        self.save()

    def save(self) -> bool:
        """Save the file. Returns True on success."""
        try:
            start = self.buffer.get_start_iter()
            end = self.buffer.get_end_iter()
            content = self.buffer.get_text(start, end, True)

            with open(self.file_path, "w", encoding="utf-8") as f:
                f.write(content)

            self.buffer.set_modified(False)
            self._modified = False
            self.script_toolbar.set_modified(False)
            self.emit("modified-changed", False)
            ToastService.show("File saved")
            return True

        except OSError as e:
            ToastService.show_error(f"Error saving file: {e}")
            return False

    def undo(self):
        """Undo last change."""
        if self.buffer.can_undo():
            self.buffer.undo()

    def redo(self):
        """Redo last undone change."""
        if self.buffer.can_redo():
            self.buffer.redo()

    @property
    def is_modified(self) -> bool:
        """Check if buffer has unsaved changes."""
        return self._modified

    def grab_focus(self):
        """Focus the editor."""
        self.source_view.grab_focus()

    def go_to_line(self, line_number: int, search_term: str = None):
        """Go to specific line number."""
        success, line_iter = self.buffer.get_iter_at_line(line_number - 1)
        if success:
            if search_term:
                line_end = line_iter.copy()
                line_end.forward_to_line_end()
                line_text = self.buffer.get_text(line_iter, line_end, False)
                idx = line_text.lower().find(search_term.lower())
                if idx >= 0:
                    start = line_iter.copy()
                    start.forward_chars(idx)
                    end = start.copy()
                    end.forward_chars(len(search_term))
                    self.buffer.select_range(start, end)
                else:
                    self.buffer.place_cursor(line_iter)
            else:
                self.buffer.place_cursor(line_iter)
            GLib.idle_add(self._scroll_to_cursor)

    def _scroll_to_cursor(self) -> bool:
        """Scroll view to cursor position."""
        self.source_view.scroll_to_mark(
            self.buffer.get_insert(), 0.2, True, 0.5, 0.3
        )
        return False
