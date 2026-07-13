"""Lightweight window for a session attached from another machine (local dispatch).

This is deliberately *not* a ``ProjectWindow`` (no ProjectLock, GitService,
FileTree, registry or file monitor — those all assume a real local project
path). It only hosts a terminal that relays the desktop's tmux session over the
dispatch PTY bridge, via the ``TerminalView(argv=...)`` seam:

    python -m src.dispatch_client <host> <port> <token> <session>

Read-only MCP panels are added in a later phase; for now the terminal is the
whole window. Closing it just detaches the relay client — the desktop session
keeps running.
"""

from __future__ import annotations

import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, GLib, Gtk

from .widgets import TerminalView

_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)


class RemoteSessionWindow(Adw.ApplicationWindow):
    """A terminal attached to a remote desktop's live Claude session."""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        token: str,
        session: str,
        title: str = "",
        **kwargs,
    ):
        super().__init__(**kwargs)

        self._host = host
        self._port = port
        self._token = token
        self._session = session
        self._title = title or session

        self.set_title(f"{self._title} · remote")
        self.set_default_size(1000, 700)

        self._toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        title_widget = Adw.WindowTitle()
        title_widget.set_title(self._title)
        title_widget.set_subtitle(f"remote · {host}")
        header.set_title_widget(title_widget)
        self._toolbar.add_top_bar(header)

        self._content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self._content.set_vexpand(True)
        self._toolbar.set_content(self._content)
        self.set_content(self._toolbar)

        self._terminal: TerminalView | None = None
        self._connect()

    # ------------------------------------------------------------------ #
    # Connection lifecycle
    # ------------------------------------------------------------------ #
    def _client_argv(self) -> list[str]:
        return [
            sys.executable,
            "-m",
            "src.dispatch_client",
            self._host,
            str(self._port),
            self._token,
            self._session,
        ]

    def _connect(self) -> None:
        """(Re)spawn the relay client terminal."""
        self._clear_content()
        self._connected_at = GLib.get_monotonic_time()
        # PYTHONPATH/cwd so `-m src.dispatch_client` resolves both from source
        # (cwd = project root) and when packaged (launcher sets PYTHONPATH).
        terminal = TerminalView(
            working_directory=_PROJECT_ROOT,
            argv=self._client_argv(),
            env={"PYTHONPATH": _PROJECT_ROOT},
        )
        terminal.connect("child-exited", self._on_disconnected)
        self._terminal = terminal
        self._content.append(terminal)

    def _on_disconnected(self, _terminal, _status: int) -> None:
        """The relay client exited (session ended, refused, or connection dropped)."""
        self._terminal = None
        self._clear_content()

        # A near-instant exit means we never really attached (refused/busy/no
        # session) rather than a live session that ended — say so instead of a
        # blank window.
        elapsed_s = (GLib.get_monotonic_time() - getattr(self, "_connected_at", 0)) / 1e6
        quick_fail = elapsed_s < 3

        status = Adw.StatusPage()
        status.set_icon_name("network-offline-symbolic")
        if quick_fail:
            status.set_title("Could not attach")
            status.set_description(
                f"“{self._title}” may be busy (open on another screen) or "
                f"unavailable on {self._host}."
            )
        else:
            status.set_title("Disconnected")
            status.set_description(
                f"The connection to {self._title} on {self._host} ended."
            )

        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        buttons.set_halign(Gtk.Align.CENTER)
        reconnect = Gtk.Button(label="Reconnect")
        reconnect.add_css_class("suggested-action")
        reconnect.add_css_class("pill")
        reconnect.connect("clicked", lambda _b: self._connect())
        close = Gtk.Button(label="Close")
        close.add_css_class("pill")
        close.connect("clicked", lambda _b: self.close())
        buttons.append(reconnect)
        buttons.append(close)
        status.set_child(buttons)

        self._content.append(status)

    def _clear_content(self) -> None:
        while child := self._content.get_first_child():
            self._content.remove(child)
