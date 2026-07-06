"""Placeholder shown instead of routing a binary file into the text editor."""

from pathlib import Path

from gi.repository import Adw, Gtk

from ..utils.text_files import capture_stat, human_size


class BinaryFileView(Gtk.Box):
    """A read-only placeholder for binary files.

    Carries a ``file_path`` attribute so the tab-dedup logic in ``ProjectWindow``
    treats it like any other open file.
    """

    def __init__(self, file_path: str):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.set_vexpand(True)
        self.set_hexpand(True)

        self.file_path = file_path

        stat = capture_stat(file_path)
        size_text = human_size(stat[1]) if stat else "unknown size"

        status = Adw.StatusPage()
        status.set_icon_name("application-x-executable-symbolic")
        status.set_title("Binary file")
        status.set_description(f"{Path(file_path).name} · {size_text}\nNot shown to avoid corruption.")
        status.set_vexpand(True)
        self.append(status)
