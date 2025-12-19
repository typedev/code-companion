"""Editable file view with syntax highlighting and autosave."""

from pathlib import Path

import gi

gi.require_version("GtkSource", "5")

from gi.repository import Gtk, GtkSource, GLib, GObject, Pango, Adw

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

        self._build_ui()
        self._load_file()

    def _build_ui(self):
        """Build the editor UI."""
        # Get settings
        self.settings = SettingsService.get_instance()

        # File toolbar with refresh button (for all files)
        self._build_file_toolbar()

        # Script toolbar for .py/.sh/.md files
        self.script_toolbar = None
        ext = Path(self.file_path).suffix.lower()
        if ext in (".py", ".sh", ".md"):
            self.script_toolbar = ScriptToolbar(self.file_path)
            self.script_toolbar.connect("run-script", self._on_run_script)
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

    def _build_file_toolbar(self):
        """Build file toolbar with refresh button."""
        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        toolbar.add_css_class("toolbar")
        toolbar.set_margin_start(6)
        toolbar.set_margin_end(6)
        toolbar.set_margin_top(2)
        toolbar.set_margin_bottom(2)

        # Spacer to push buttons to the right
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        toolbar.append(spacer)

        # Refresh button
        refresh_btn = Gtk.Button()
        refresh_btn.set_icon_name("view-refresh-symbolic")
        refresh_btn.add_css_class("flat")
        refresh_btn.set_tooltip_text("Reload file from disk (discard changes)")
        refresh_btn.connect("clicked", self._on_refresh_clicked)
        toolbar.append(refresh_btn)

        self.append(toolbar)

    def _on_refresh_clicked(self, button):
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
        new_iter = self.buffer.get_iter_at_line_offset(line, 0)
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
