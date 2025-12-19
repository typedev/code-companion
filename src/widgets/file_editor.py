"""Editable file view with syntax highlighting and autosave."""

from pathlib import Path

import gi

gi.require_version("GtkSource", "5")

from gi.repository import Gtk, GtkSource, GLib, GObject, Pango, Adw, Gdk

from .code_view import get_language_for_file
from .script_toolbar import ScriptToolbar
from .markdown_preview import MarkdownPreview
from ..services import ToastService, SettingsService


class FileEditor(Gtk.Box):
    """A widget for editing files with syntax highlighting and autosave."""

    __gsignals__ = {
        "modified-changed": (GObject.SignalFlags.RUN_FIRST, None, (bool,)),
        "run-requested": (GObject.SignalFlags.RUN_FIRST, None, (str, str)),  # file_path, args
    }

    def __init__(self, file_path: str):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.set_vexpand(True)
        self.set_hexpand(True)

        self.file_path = file_path
        self._modified = False
        self._save_timeout_id: int | None = None
        self._is_markdown = Path(file_path).suffix.lower() == ".md"
        self._preview_active = False

        # Search state
        self._search_context = None
        self._search_settings = None

        self._build_ui()
        self._load_file()
        self._setup_search()

    def _build_ui(self):
        """Build the editor UI."""
        # Get settings
        self.settings = SettingsService.get_instance()

        # Script toolbar (for all files - has refresh, run/outline for scripts)
        ext = Path(self.file_path).suffix.lower()
        self.script_toolbar = ScriptToolbar(self.file_path)
        self.script_toolbar.connect("refresh-requested", self._on_refresh_requested)
        if ext in (".py", ".sh"):
            self.script_toolbar.connect("run-script", self._on_run_script)
        if ext in (".py", ".md"):
            self.script_toolbar.connect("go-to-line", self._on_go_to_line)
            self.script_toolbar.set_cursor_line_callback(self._get_cursor_line)
        if self._is_markdown:
            self.script_toolbar.connect("toggle-preview", self._on_toggle_preview)
        self.append(self.script_toolbar)

        # Create source buffer and view
        self.buffer = GtkSource.Buffer()
        self.source_view = GtkSource.View(buffer=self.buffer)

        # Configure source view
        self.source_view.set_editable(True)
        self.source_view.set_cursor_visible(True)
        self.source_view.set_show_line_numbers(True)
        self.source_view.set_monospace(True)
        self.source_view.set_auto_indent(True)
        self.source_view.set_indent_on_tab(True)
        self.source_view.set_highlight_current_line(True)

        # Apply settings
        self._apply_settings()

        # Enable undo/redo (large but not unlimited to avoid the -1 range error)
        self.buffer.set_max_undo_levels(10000)

        # Set up language highlighting
        lang_id = get_language_for_file(self.file_path)
        if lang_id:
            lang_manager = GtkSource.LanguageManager.get_default()
            language = lang_manager.get_language(lang_id)
            if language:
                self.buffer.set_language(language)

        # Listen for settings changes
        self.settings.connect("changed", self._on_setting_changed)

        # Connect signals
        self.buffer.connect("changed", self._on_buffer_changed)

        # Focus controller for autosave
        focus_controller = Gtk.EventControllerFocus()
        focus_controller.connect("leave", self._on_focus_leave)
        self.source_view.add_controller(focus_controller)

        # Wrap in scrolled window
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_vexpand(True)
        scrolled.set_hexpand(True)
        scrolled.set_child(self.source_view)

        # For markdown files, use a Stack to switch between editor and preview
        if self._is_markdown:
            self.stack = Gtk.Stack()
            self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
            self.stack.set_vexpand(True)
            self.stack.set_hexpand(True)

            self.stack.add_named(scrolled, "editor")

            # Create preview widget
            self.markdown_preview = MarkdownPreview()
            self.stack.add_named(self.markdown_preview, "preview")

            self.append(self.stack)
        else:
            self.stack = None
            self.markdown_preview = None
            self.append(scrolled)

        # Search bar (hidden by default)
        self._build_search_bar()

        # Keyboard shortcuts
        self._setup_shortcuts()

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

        # Search entry
        self.search_entry = Gtk.SearchEntry()
        self.search_entry.set_hexpand(True)
        self.search_entry.set_placeholder_text("Find...")
        self.search_entry.connect("search-changed", self._on_search_changed)
        self.search_entry.connect("activate", self._on_search_next)
        self.search_entry.connect("next-match", self._on_search_next)
        self.search_entry.connect("previous-match", self._on_search_prev)
        search_row.append(self.search_entry)

        # Match count label
        self.match_label = Gtk.Label(label="")
        self.match_label.add_css_class("dim-label")
        self.match_label.set_width_chars(8)
        search_row.append(self.match_label)

        # Navigation buttons
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

        # Toggle buttons
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

        # Expand/collapse replace
        self.replace_toggle = Gtk.ToggleButton()
        self.replace_toggle.set_icon_name("edit-find-replace-symbolic")
        self.replace_toggle.set_tooltip_text("Show replace")
        self.replace_toggle.add_css_class("flat")
        self.replace_toggle.connect("toggled", self._on_replace_toggled)
        search_row.append(self.replace_toggle)

        # Close button
        close_btn = Gtk.Button()
        close_btn.set_icon_name("window-close-symbolic")
        close_btn.set_tooltip_text("Close (Escape)")
        close_btn.add_css_class("flat")
        close_btn.connect("clicked", lambda b: self.hide_search())
        search_row.append(close_btn)

        search_box.append(search_row)

        # Replace row (hidden by default)
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
        """Set up keyboard shortcuts for the editor."""
        key_controller = Gtk.EventControllerKey()
        key_controller.connect("key-pressed", self._on_key_pressed)
        self.source_view.add_controller(key_controller)

    def _on_key_pressed(self, controller, keyval, keycode, state):
        """Handle keyboard shortcuts."""
        ctrl = state & Gdk.ModifierType.CONTROL_MASK

        if ctrl and keyval == Gdk.KEY_f:
            self.show_search()
            return True
        elif keyval == Gdk.KEY_Escape:
            if self.search_bar.get_reveal_child():
                self.hide_search()
                return True
        return False

    def show_search(self, replace: bool = False):
        """Show the search bar."""
        self.search_bar.set_reveal_child(True)
        self.search_entry.grab_focus()
        if replace:
            self.replace_toggle.set_active(True)
        # Select current word if no selection
        if not self.buffer.get_has_selection():
            self._select_current_word()
        # Copy selection to search entry
        if self.buffer.get_has_selection():
            start, end = self.buffer.get_selection_bounds()
            text = self.buffer.get_text(start, end, False)
            if "\n" not in text:  # Single line only
                self.search_entry.set_text(text)
                self.search_entry.select_region(0, -1)

    def hide_search(self):
        """Hide the search bar and clear highlights."""
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

    def _on_refresh_requested(self, toolbar=None):
        """Handle refresh button click."""
        if self._modified:
            # Ask user to confirm discarding changes
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
        """Handle reload confirmation dialog response."""
        if response == "discard":
            self.reload()

    def reload(self):
        """Reload file content from disk."""
        # Save cursor position
        mark = self.buffer.get_insert()
        iter_at_cursor = self.buffer.get_iter_at_mark(mark)
        line = iter_at_cursor.get_line()
        offset = iter_at_cursor.get_line_offset()

        # Reload content
        self._load_file()

        # Restore cursor position (if possible)
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

    # --- Search functionality ---

    def _setup_search(self):
        """Set up search context and settings."""
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

        # Jump to first match if text entered
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
            # Still counting
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

        # Verify selection matches search
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

    def _apply_settings(self):
        """Apply all settings to the editor."""
        # Syntax scheme
        scheme_id = self.settings.get("appearance.syntax_scheme", "Adwaita-dark")
        style_manager = GtkSource.StyleSchemeManager.get_default()
        scheme = style_manager.get_scheme(scheme_id)
        if not scheme:
            # Fallback to Adwaita-dark or classic
            scheme = style_manager.get_scheme("Adwaita-dark")
        if not scheme:
            scheme = style_manager.get_scheme("classic")
        if scheme:
            self.buffer.set_style_scheme(scheme)

        # Font
        font_family = self.settings.get("editor.font_family", "Monospace")
        font_size = self.settings.get("editor.font_size", 12)
        font_desc = Pango.FontDescription.from_string(f"{font_family} {font_size}")
        self.source_view.set_monospace(True)

        # Apply font via CSS (more reliable for GtkSourceView)
        css_provider = Gtk.CssProvider()
        line_height = self.settings.get("editor.line_height", 1.4)
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
        self._css_provider = css_provider  # Keep reference

        # Tab settings
        tab_size = self.settings.get("editor.tab_size", 4)
        insert_spaces = self.settings.get("editor.insert_spaces", True)
        self.source_view.set_tab_width(tab_size)
        self.source_view.set_insert_spaces_instead_of_tabs(insert_spaces)

        # Word wrap
        word_wrap = self.settings.get("editor.word_wrap", True)
        wrap_mode = Gtk.WrapMode.WORD_CHAR if word_wrap else Gtk.WrapMode.NONE
        self.source_view.set_wrap_mode(wrap_mode)

    def _on_setting_changed(self, settings, key, value):
        """Handle settings changes."""
        if key.startswith("appearance.") or key.startswith("editor."):
            self._apply_settings()

    def _load_file(self):
        """Load file content into buffer."""
        try:
            with open(self.file_path, "r", encoding="utf-8") as f:
                content = f.read()
            self.buffer.set_text(content)
            self.buffer.set_modified(False)
            self._modified = False
            # Place cursor at start
            self.buffer.place_cursor(self.buffer.get_start_iter())
            # Update outline for Python files
            self._update_outline()
        except (OSError, UnicodeDecodeError) as e:
            self.buffer.set_text(f"Error loading file: {e}")
            self.source_view.set_editable(False)

    def _on_buffer_changed(self, buffer):
        """Handle buffer changes."""
        is_modified = buffer.get_modified()
        if is_modified != self._modified:
            self._modified = is_modified
            self.emit("modified-changed", is_modified)

    def _on_focus_leave(self, controller):
        """Handle focus leave - schedule autosave."""
        if self._modified:
            # Cancel any pending save
            if self._save_timeout_id:
                GLib.source_remove(self._save_timeout_id)
            # Schedule save with short delay
            self._save_timeout_id = GLib.timeout_add(100, self._do_autosave)

    def _do_autosave(self) -> bool:
        """Perform autosave."""
        self._save_timeout_id = None
        if self._modified:
            self.save()
        return False  # Don't repeat

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
            self.emit("modified-changed", False)
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
        """Go to specific line number and optionally highlight search term."""
        # Get iterator at the line (0-based internally)
        # GTK4 returns (success, iter) tuple
        success, line_iter = self.buffer.get_iter_at_line(line_number - 1)
        if success:
            if search_term:
                # Find and select the search term on this line
                line_end = line_iter.copy()
                line_end.forward_to_line_end()
                line_text = self.buffer.get_text(line_iter, line_end, False)

                # Case-insensitive search
                idx = line_text.lower().find(search_term.lower())
                if idx >= 0:
                    # Select the found text
                    start = line_iter.copy()
                    start.forward_chars(idx)
                    end = start.copy()
                    end.forward_chars(len(search_term))
                    self.buffer.select_range(start, end)
                else:
                    self.buffer.place_cursor(line_iter)
            else:
                self.buffer.place_cursor(line_iter)
            # Use idle_add to ensure UI is ready
            GLib.idle_add(self._scroll_to_cursor)

    def _scroll_to_cursor(self) -> bool:
        """Scroll view to cursor position."""
        self.source_view.scroll_to_mark(
            self.buffer.get_insert(),
            0.2,  # margin
            True,  # use_align
            0.5,   # xalign (center horizontally)
            0.3    # yalign (1/3 from top)
        )
        return False  # Don't repeat

    def _update_outline(self):
        """Update outline in script toolbar."""
        ext = Path(self.file_path).suffix.lower()
        if self.script_toolbar and ext in (".py", ".md"):
            start = self.buffer.get_start_iter()
            end = self.buffer.get_end_iter()
            source = self.buffer.get_text(start, end, True)
            self.script_toolbar.update_outline(source)

    def _on_run_script(self, toolbar, args: str):
        """Handle run script request from toolbar."""
        # Save file before running
        if self._modified:
            self.save()
        self.emit("run-requested", self.file_path, args)

    def _on_go_to_line(self, toolbar, line: int):
        """Handle go to line request from outline."""
        self.go_to_line(line)

    def _on_toggle_preview(self, toolbar, is_preview: bool):
        """Handle preview toggle for markdown files."""
        if not self.stack:
            return

        self._preview_active = is_preview

        if is_preview:
            # Update preview content before showing
            start = self.buffer.get_start_iter()
            end = self.buffer.get_end_iter()
            markdown_text = self.buffer.get_text(start, end, True)
            base_path = f"file://{Path(self.file_path).parent}/"
            self.markdown_preview.update_preview(markdown_text, base_path)
            self.stack.set_visible_child_name("preview")
        else:
            self.stack.set_visible_child_name("editor")
            # Focus editor when switching back
            GLib.idle_add(self.source_view.grab_focus)

    def _get_cursor_line(self) -> int:
        """Return current cursor line number."""
        insert_mark = self.buffer.get_insert()
        cursor_iter = self.buffer.get_iter_at_mark(insert_mark)
        return cursor_iter.get_line() + 1  # 1-based line number
