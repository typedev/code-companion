"""Editable file view with syntax highlighting."""

from pathlib import Path

import gi

gi.require_version("GtkSource", "5")

from gi.repository import Gtk, GtkSource, GLib, GObject, Adw, Gdk

from .code_view import get_language_for_file
from .script_toolbar import ScriptToolbar
from .markdown_preview import MarkdownPreview
from .disk_sync import DiskSyncController
from ..services import ToastService, SettingsService
from ..utils.atomic_write import atomic_write_text
from ..utils.text_files import read_text_file


class FileEditor(Gtk.Box):
    """A widget for editing files with syntax highlighting.

    Saving is explicit (Ctrl+S or the toolbar). There is no autosave.
    """

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
        self._baseline_text = ""  # content at open / last save, for "diff since save"
        self._is_markdown = Path(file_path).suffix.lower() == ".md"
        self._preview_active = False

        # Disk-safety state
        self._line_ending = "\n"  # detected on load, preserved on save
        self._load_failed = False  # True if the file couldn't be decoded; blocks save

        # Search state
        self._search_context = None
        self._search_settings = None

        # External-change detection (roadmap 1.1/1.2)
        self._disk_sync = DiskSyncController(self)

        self._build_ui()
        self._load_file()
        self._disk_sync.note_loaded()
        self._setup_search()

    def _build_ui(self):
        """Build the editor UI."""
        # Get settings
        self.settings = SettingsService.get_instance()

        # Disk-change banner sits at the very top (roadmap 1.1)
        self.append(self._disk_sync.banner)

        # Script toolbar (for all files - has refresh, save, run/outline for scripts)
        ext = Path(self.file_path).suffix.lower()
        self.script_toolbar = ScriptToolbar(self.file_path)
        self.script_toolbar.connect("refresh-requested", self._on_refresh_requested)
        self.script_toolbar.connect("save-requested", self._on_save_requested)
        self.script_toolbar.connect("diff-requested", self._on_diff_requested)
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
        # Track the modified flag via "modified-changed", NOT "changed": on the
        # first edit after a load, "changed" is emitted while get_modified() is
        # still False (the flag flips just afterwards), so a "changed" handler
        # would miss the first edit and leave _modified stale — dangerous for
        # the disk-sync guard, which would then silently reload a dirty buffer.
        self.buffer.connect("modified-changed", self._on_modified_changed)

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
        self.match_label.set_width_chars(10)
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

        self.word_btn = Gtk.ToggleButton()
        self.word_btn.set_label("W")
        self.word_btn.set_tooltip_text("Match whole word")
        self.word_btn.add_css_class("flat")
        self.word_btn.connect("toggled", self._on_search_options_changed)
        search_row.append(self.word_btn)

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
        shift = state & Gdk.ModifierType.SHIFT_MASK

        if ctrl and keyval == Gdk.KEY_f:
            self.show_search()
            return True
        elif ctrl and keyval == Gdk.KEY_h:
            self.show_search(replace=True)
            return True
        elif ctrl and keyval == Gdk.KEY_g:
            self._show_go_to_line_dialog()
            return True
        elif ctrl and keyval == Gdk.KEY_s:
            self.request_save()
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
        # Whole-word is meaningless with a regex; let regex win.
        self._search_settings.set_at_word_boundaries(
            self.word_btn.get_active() and not self.regex_btn.get_active()
        )
        self.word_btn.set_sensitive(not self.regex_btn.get_active())
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
        """Update the match count label ("k of N"), flagging an invalid regex."""
        # An invalid regex must say so, not read as a silent "No results".
        if self.regex_btn.get_active():
            err = self._search_context.get_regex_error()
            if err is not None:
                self.match_label.set_text("Bad regex")
                self.match_label.set_tooltip_text(err.message)
                self.search_entry.add_css_class("error")
                return
        self.match_label.set_tooltip_text("")

        count = self._search_context.get_occurrences_count()
        if count == -1:
            # Still counting.
            self.match_label.set_text("…")
            return
        if count == 0:
            if self.search_entry.get_text():
                self.match_label.set_text("No results")
                self.search_entry.add_css_class("error")
            else:
                self.match_label.set_text("")
                self.search_entry.remove_css_class("error")
            return

        self.search_entry.remove_css_class("error")
        # Position of the current selection among occurrences (0 if the
        # selection isn't itself a match, e.g. right after opening the bar).
        pos = 0
        if self.buffer.get_has_selection():
            sel_start, sel_end = self.buffer.get_selection_bounds()
            pos = self._search_context.get_occurrence_position(sel_start, sel_end)
        self.match_label.set_text(f"{pos} of {count}" if pos > 0 else f"{count} found")

    def _on_replace(self):
        """Replace the current match, then advance to the next."""
        if not self.buffer.get_has_selection():
            self._on_search_next()
            return

        start, end = self.buffer.get_selection_bounds()
        # Validate the selection with the SAME engine that runs the search
        # (get_occurrence_position), instead of re-checking with Python `re`,
        # which mismatches GtkSource's PCRE for regex patterns.
        if self._search_context.get_occurrence_position(start, end) > 0:
            replace_text = self.replace_entry.get_text()
            self._search_context.replace(start, end, replace_text, -1)
        self._on_search_next()

    def _on_replace_all(self):
        """Replace all matches as a single undoable action."""
        replace_text = self.replace_entry.get_text()
        self.buffer.begin_user_action()
        try:
            count = self._search_context.replace_all(replace_text, -1)
        finally:
            self.buffer.end_user_action()
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
            result = read_text_file(self.file_path)
        except OSError as e:
            self._set_buffer_text(f"Error loading file: {e}")
            self._load_failed = True
            self.source_view.set_editable(False)
            return

        if not result.ok:
            # Non-UTF-8 content: never dump raw bytes into an editable buffer.
            self._set_buffer_text(
                "This file could not be decoded as UTF-8 and is shown read-only "
                "to avoid corrupting it on save."
            )
            self._load_failed = True
            self.source_view.set_editable(False)
            return

        self._load_failed = False
        self._line_ending = result.line_ending
        self._set_buffer_text(result.text)
        self._baseline_text = result.text  # baseline for "diff since save"
        self.buffer.set_modified(False)
        self._modified = False
        # A successful load always restores editability (a prior failed load
        # may have disabled it).
        self.source_view.set_editable(True)
        # Place cursor at start
        self.buffer.place_cursor(self.buffer.get_start_iter())
        # Update outline for Python files
        self._update_outline()

    def _set_buffer_text(self, text: str):
        """Replace buffer content without polluting the undo stack.

        Loading/reloading a file is not a user edit, so it must not be undoable
        (otherwise Ctrl+Z right after opening would wipe the buffer to empty).
        """
        self.buffer.begin_irreversible_action()
        try:
            self.buffer.set_text(text)
        finally:
            self.buffer.end_irreversible_action()

    def _on_modified_changed(self, buffer):
        """Handle the buffer's modified flag flipping."""
        is_modified = buffer.get_modified()
        if is_modified != self._modified:
            self._modified = is_modified
            self.script_toolbar.set_modified(is_modified)
            self.emit("modified-changed", is_modified)

    def _on_save_requested(self, toolbar):
        """Handle save request from toolbar."""
        self.request_save()

    def _on_diff_requested(self, toolbar):
        """Show a diff of the unsaved buffer against the last-saved content."""
        root = self.get_root()
        if root is None or not hasattr(root, "open_text_diff"):
            return
        start = self.buffer.get_start_iter()
        end = self.buffer.get_end_iter()
        current = self.buffer.get_text(start, end, True)
        if current == self._baseline_text:
            ToastService.show("No unsaved changes")
            return
        root.open_text_diff(
            self.file_path,
            self._baseline_text,
            current,
            f"Changes: {Path(self.file_path).name}",
        )

    def _write_now(self) -> bool:
        """Atomically write the buffer to disk (no conflict check). Returns success."""
        if self._load_failed:
            ToastService.show_error("File was not loaded correctly; refusing to save over it.")
            return False
        try:
            start = self.buffer.get_start_iter()
            end = self.buffer.get_end_iter()
            content = self.buffer.get_text(start, end, True)

            atomic_write_text(self.file_path, content, newline=self._line_ending)

            self._baseline_text = content  # new baseline for "diff since save"
            self.buffer.set_modified(False)
            self._modified = False
            self.script_toolbar.set_modified(False)
            self.emit("modified-changed", False)
            self._disk_sync.note_saved()
            ToastService.show("File saved")
            return True

        except OSError as e:
            self._disk_sync.show_error_banner(f"Could not save '{Path(self.file_path).name}': {e}")
            return False

    def save(self) -> bool:
        """Synchronous save without the interactive conflict dialog.

        Kept for programmatic callers that need an immediate boolean (save-before-run,
        Save-As). Interactive and close/rename/delete paths use ``request_save()`` so
        an external change surfaces a choice first.
        """
        return self._write_now()

    def request_save(self, on_result=None):
        """Save, surfacing a conflict dialog if the file changed on disk (roadmap 1.2).

        ``on_result(success: bool)`` is invoked when the operation settles.
        Reload / Show Diff / Cancel all abort the save and report ``False``.
        """
        def done(ok: bool):
            if on_result is not None:
                on_result(ok)

        if self._load_failed:
            ToastService.show_error("File was not loaded correctly; refusing to save over it.")
            done(False)
            return

        if self._disk_sync.has_conflict():
            self._present_conflict_dialog(done)
            return

        done(self._write_now())

    def _present_conflict_dialog(self, done):
        name = Path(self.file_path).name
        dialog = Adw.AlertDialog()
        dialog.set_heading("File Changed on Disk")
        dialog.set_body(
            f"'{name}' was modified on disk since it was opened here. Overwrite it "
            "with your version, reload the disk version (losing your edits), or view "
            "the differences?"
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("diff", "Show Diff")
        dialog.add_response("reload", "Reload")
        dialog.add_response("overwrite", "Overwrite")
        dialog.set_response_appearance("overwrite", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", self._on_conflict_response, done)
        dialog.present(self.get_root())

    def _on_conflict_response(self, dialog, response, done):
        if response == "overwrite":
            done(self._write_now())
        elif response == "reload":
            self._disk_sync.reload_from_disk()
            done(False)
        elif response == "diff":
            self._disk_sync.show_diff()
            done(False)
        else:
            done(False)

    def set_file_path(self, new_path: str):
        """Repoint the editor at a new path after an in-app rename/move (roadmap 1.8).

        Updates the language, toolbar, and re-arms the disk monitor on the new
        path. Does not touch the modified flag or buffer content.
        """
        self.file_path = new_path
        self._is_markdown = Path(new_path).suffix.lower() == ".md"
        if hasattr(self, "script_toolbar"):
            self.script_toolbar.file_path = new_path
        lang_id = get_language_for_file(new_path)
        language = None
        if lang_id:
            language = GtkSource.LanguageManager.get_default().get_language(lang_id)
        self.buffer.set_language(language)
        self._disk_sync.note_loaded()

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

    def _show_go_to_line_dialog(self):
        """Ctrl+G: prompt for a line number and jump to it."""
        line_count = self.buffer.get_line_count()

        dialog = Adw.AlertDialog()
        dialog.set_heading("Go to Line")
        dialog.set_body(f"Enter a line number (1–{line_count})")

        entry = Gtk.Entry()
        entry.set_input_purpose(Gtk.InputPurpose.DIGITS)
        entry.set_activates_default(True)
        # Prefill with the current line for quick nudging.
        cursor = self.buffer.get_iter_at_mark(self.buffer.get_insert())
        entry.set_text(str(cursor.get_line() + 1))
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(entry)
        dialog.set_extra_child(box)

        dialog.add_response("cancel", "Cancel")
        dialog.add_response("go", "Go")
        dialog.set_default_response("go")
        dialog.set_close_response("cancel")

        def on_response(dlg, response):
            if response != "go":
                return
            try:
                line = int(entry.get_text().strip())
            except ValueError:
                return
            line = max(1, min(line, line_count))
            self.go_to_line(line)
            self.grab_focus()

        dialog.connect("response", on_response)
        dialog.present(self.get_root())

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

    def select_line_range(self, start_line: int, end_line: int):
        """Select whole lines from start_line to end_line (1-based) and scroll there.

        The cursor (insert) is placed at the start of the range so the scroll lands
        on its beginning. When end_line is past the file, the selection extends to the
        end of the buffer.
        """
        ok_start, start_iter = self.buffer.get_iter_at_line(max(start_line - 1, 0))
        if not ok_start:
            return
        ok_end, end_iter = self.buffer.get_iter_at_line(max(end_line - 1, 0))
        if not ok_end:
            end_iter = self.buffer.get_end_iter()
        else:
            end_iter.forward_to_line_end()
        # First arg becomes the insert mark -> _scroll_to_cursor lands on the start.
        self.buffer.select_range(start_iter, end_iter)
        GLib.idle_add(self._scroll_to_cursor)

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
