"""External-change detection for editors (roadmap 1.1 / 1.2).

A ``DiskSyncController`` watches the editor's own file with a ``Gio.FileMonitor``
and keeps a baseline ``(mtime, size)``. When the file changes underneath the
editor it either silently reloads (clean buffer) or raises a persistent,
non-focus-stealing banner offering the user a choice (dirty buffer). It also
answers ``has_conflict()`` for the pre-save guard.

The controller drives the host editor entirely through a small set of attributes
common to both ``FileEditor`` and ``SvgEditor`` (``file_path``, ``buffer``,
``_modified``, ``reload()``, ``_tab_page``, ``get_root()``), so it needs no
editor-specific glue.
"""

from __future__ import annotations

import os
from pathlib import Path

from gi.repository import Gdk, Gio, GLib, Gtk

from ..services import ToastService
from ..utils.text_files import capture_stat, read_text_file, stat_differs

_BANNER_CSS = b"""
.disk-change-banner {
    background-color: @warning_bg_color;
    color: @warning_fg_color;
    padding: 4px 8px;
}
"""
_css_installed = False


def _install_css():
    global _css_installed
    if _css_installed:
        return
    display = Gdk.Display.get_default()
    if display is None:
        return
    provider = Gtk.CssProvider()
    provider.load_from_data(_BANNER_CSS)
    Gtk.StyleContext.add_provider_for_display(
        display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )
    _css_installed = True


class ChangeBanner(Gtk.Revealer):
    """A slim, persistent banner with a message and up to two flat buttons.

    Used instead of ``Adw.Banner`` because that widget supports only a single
    action button, whereas the disk-change states need two (Reload/Diff,
    Save As/Close).
    """

    def __init__(self):
        super().__init__()
        self.set_reveal_child(False)
        _install_css()

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        box.add_css_class("disk-change-banner")
        box.set_margin_start(0)

        self._label = Gtk.Label()
        self._label.set_xalign(0)
        self._label.set_hexpand(True)
        self._label.set_wrap(True)
        box.append(self._label)

        self._btn_a = Gtk.Button()
        self._btn_a.add_css_class("flat")
        self._btn_a.connect("clicked", self._on_a)
        box.append(self._btn_a)

        self._btn_b = Gtk.Button()
        self._btn_b.add_css_class("flat")
        self._btn_b.connect("clicked", self._on_b)
        box.append(self._btn_b)

        self.set_child(box)
        self._cb_a = None
        self._cb_b = None

    def show_message(self, text: str, actions: list[tuple[str, object]]):
        """Show ``text`` with up to two ``(label, callback)`` action buttons."""
        self._label.set_text(text)

        self._cb_a = self._cb_b = None
        self._btn_a.set_visible(False)
        self._btn_b.set_visible(False)

        if len(actions) >= 1:
            self._btn_a.set_label(actions[0][0])
            self._cb_a = actions[0][1]
            self._btn_a.set_visible(True)
        if len(actions) >= 2:
            self._btn_b.set_label(actions[1][0])
            self._cb_b = actions[1][1]
            self._btn_b.set_visible(True)

        self.set_reveal_child(True)

    def hide(self):
        self.set_reveal_child(False)

    def _on_a(self, _button):
        if self._cb_a:
            self._cb_a()

    def _on_b(self, _button):
        if self._cb_b:
            self._cb_b()


class DiskSyncController:
    """Watches an editor's file and reconciles external changes."""

    # Re-check debounce: collapses the DELETED+CREATED pair emitted by an
    # atomic replace into a single evaluation of the final on-disk state.
    _RECHECK_MS = 150

    def __init__(self, editor):
        self._editor = editor
        self.banner = ChangeBanner()

        self._mtime: int | None = None
        self._size: int | None = None

        self._monitor: Gio.FileMonitor | None = None
        self._monitor_handler = 0
        self._recheck_id = 0
        self._disposed = False

    # --- baseline / lifecycle -------------------------------------------------

    def note_loaded(self):
        """Record the current on-disk stat as the baseline and (re)arm the monitor."""
        self._capture()
        self._arm_monitor()
        self.banner.hide()

    def note_saved(self):
        """Update the baseline after the editor itself wrote the file."""
        self._capture()
        self.banner.hide()

    def has_conflict(self) -> bool:
        """True if the file changed on disk since the last load/save."""
        return stat_differs(self._editor.file_path, self._mtime, self._size)

    def dispose(self):
        """Cancel the monitor and any pending re-check. Idempotent."""
        if self._disposed:
            return
        self._disposed = True
        if self._recheck_id:
            GLib.source_remove(self._recheck_id)
            self._recheck_id = 0
        self._cancel_monitor()

    # --- internals ------------------------------------------------------------

    def _capture(self):
        stat = capture_stat(self._editor.file_path)
        if stat is None:
            self._mtime = self._size = None
        else:
            self._mtime, self._size = stat

    def _cancel_monitor(self):
        if self._monitor is not None:
            if self._monitor_handler:
                self._monitor.disconnect(self._monitor_handler)
                self._monitor_handler = 0
            self._monitor.cancel()
            self._monitor = None

    def _arm_monitor(self):
        self._cancel_monitor()
        gfile = Gio.File.new_for_path(self._editor.file_path)
        try:
            self._monitor = gfile.monitor_file(Gio.FileMonitorFlags.WATCH_MOVES, None)
        except GLib.Error:
            self._monitor = None
            return
        self._monitor.set_rate_limit(500)
        self._monitor_handler = self._monitor.connect("changed", self._on_changed)

    def _on_changed(self, _monitor, _file, _other, _event):
        if self._disposed:
            return
        # Debounce: coalesce bursts (and the replace's delete+create) into one check.
        if self._recheck_id:
            GLib.source_remove(self._recheck_id)
        self._recheck_id = GLib.timeout_add(self._RECHECK_MS, self._recheck)

    def _recheck(self):
        self._recheck_id = 0
        if self._disposed:
            return False

        path = self._editor.file_path
        if not os.path.exists(path):
            self._show_deleted()
            return False

        if not stat_differs(path, self._mtime, self._size):
            return False  # our own write, or no real change

        if getattr(self._editor, "_modified", False):
            self._show_changed()
        else:
            self.reload_from_disk()
        return False

    # --- banner states --------------------------------------------------------

    def _show_changed(self):
        self.banner.show_message(
            "File changed on disk.",
            [("Reload", self.reload_from_disk), ("Diff", self.show_diff)],
        )

    def _show_deleted(self):
        self.banner.show_message(
            "File was deleted or moved on disk.",
            [("Save As…", self._save_as), ("Close", self._force_close)],
        )

    def show_error_banner(self, text: str):
        """Show a persistent error banner (e.g. a save failure) with a Dismiss action.

        A toast would vanish and leave a mystery open tab, so save failures stay
        visible until the user dismisses them (roadmap 1.4).
        """
        self.banner.show_message(text, [("Dismiss", self.banner.hide)])

    # --- actions (also reused by the editor's pre-save conflict dialog) --------

    def reload_from_disk(self):
        """Reload the editor from disk and re-baseline; used by the banner and dialog."""
        self._editor.reload()
        self.note_loaded()
        ToastService.show("File reloaded from disk")

    def _buffer_text(self) -> str:
        buf = self._editor.buffer
        return buf.get_text(buf.get_start_iter(), buf.get_end_iter(), True)

    def show_diff(self):
        root = self._editor.get_root()
        if root is None or not hasattr(root, "open_text_diff"):
            return
        path = self._editor.file_path
        try:
            disk_text = read_text_file(path).text
        except OSError:
            disk_text = ""
        root.open_text_diff(path, disk_text, self._buffer_text(), f"Diff: {Path(path).name}")

    def _save_as(self):
        root = self._editor.get_root()
        dialog = Gtk.FileDialog()
        dialog.set_initial_name(Path(self._editor.file_path).name)

        def on_done(dlg, result):
            try:
                gfile = dlg.save_finish(result)
            except GLib.Error:
                return
            if gfile is None:
                return
            new_path = gfile.get_path()
            if hasattr(self._editor, "set_file_path"):
                self._editor.set_file_path(new_path)
            if self._editor.save():
                self.banner.hide()

        dialog.save(root, None, on_done)

    def _force_close(self):
        editor = self._editor
        # Clear the modified flag so closing does not prompt to save a ghost path.
        editor._modified = False
        editor.buffer.set_modified(False)
        page = getattr(editor, "_tab_page", None)
        root = editor.get_root()
        if page is not None and root is not None and hasattr(root, "tab_view"):
            root.tab_view.close_page(page)
