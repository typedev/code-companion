"""Editable file view with syntax highlighting and autosave."""

from pathlib import Path

import gi

gi.require_version("GtkSource", "5")

from gi.repository import Gtk, GtkSource, GLib, GObject

from .code_view import get_language_for_file
from ..services import ToastService


class FileEditor(Gtk.Box):
    """A widget for editing files with syntax highlighting and autosave."""

    __gsignals__ = {
        "modified-changed": (GObject.SignalFlags.RUN_FIRST, None, (bool,)),
    }

    def __init__(self, file_path: str):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)

        self.file_path = file_path
        self._modified = False
        self._save_timeout_id: int | None = None

        self._build_ui()
        self._load_file()

    def _build_ui(self):
        """Build the editor UI."""
        # Create source buffer and view
        self.buffer = GtkSource.Buffer()
        self.source_view = GtkSource.View(buffer=self.buffer)

        # Configure source view
        self.source_view.set_editable(True)
        self.source_view.set_cursor_visible(True)
        self.source_view.set_show_line_numbers(True)
        self.source_view.set_monospace(True)
        self.source_view.set_wrap_mode(Gtk.WrapMode.NONE)
        self.source_view.set_auto_indent(True)
        self.source_view.set_indent_on_tab(True)
        self.source_view.set_tab_width(4)
        self.source_view.set_insert_spaces_instead_of_tabs(True)
        self.source_view.set_highlight_current_line(True)

        # Enable undo/redo (large but not unlimited to avoid the -1 range error)
        self.buffer.set_max_undo_levels(10000)

        # Set up language highlighting
        lang_id = get_language_for_file(self.file_path)
        if lang_id:
            lang_manager = GtkSource.LanguageManager.get_default()
            language = lang_manager.get_language(lang_id)
            if language:
                self.buffer.set_language(language)

        # Set up style scheme (Dracula to match terminal)
        style_manager = GtkSource.StyleSchemeManager.get_default()
        scheme = style_manager.get_scheme("dracula")
        if not scheme:
            scheme = style_manager.get_scheme("Adwaita-dark")
        if not scheme:
            scheme = style_manager.get_scheme("classic")
        if scheme:
            self.buffer.set_style_scheme(scheme)

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

        self.append(scrolled)

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
