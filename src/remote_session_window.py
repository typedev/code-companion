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

from .services.icon_cache import IconCache
from .widgets import TerminalView
from .widgets.remote_panels import RemotePanels

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
        http_port: int | None = None,
        **kwargs,
    ):
        super().__init__(**kwargs)

        self._host = host
        self._port = port  # PTY bridge port (terminal)
        self._http_port = http_port or max(1, port - 1)  # broker HTTP port (panels)
        self._token = token
        self._session = session
        self._title = title or session

        self.set_title(f"{self._title} · remote")
        self.set_default_size(1200, 760)

        self._toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        title_widget = Adw.WindowTitle()
        title_widget.set_title(self._title)
        title_widget.set_subtitle(f"remote · {host}")
        header.set_title_widget(title_widget)

        self._content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self._content.set_vexpand(True)
        self._panels = RemotePanels(host, self._http_port, token, session)

        # Header: sidebar toggle + a horizontal linked panel switcher + reload —
        # the same layout the standard workspace uses (switcher in the header).
        self._sidebar_toggle = Gtk.ToggleButton(icon_name="sidebar-show-symbolic")
        self._sidebar_toggle.set_tooltip_text("Show/hide the panels")
        self._sidebar_toggle.set_active(True)
        self._sidebar_toggle.connect("toggled", self._on_sidebar_toggled)
        header.pack_start(self._sidebar_toggle)

        switcher = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        switcher.set_margin_start(6)
        self._panel_buttons: dict[str, Gtk.ToggleButton] = {}
        for name, icon, tip in RemotePanels.TABS:
            btn = self._make_panel_toggle(icon, tip)
            btn.connect("toggled", self._on_panel_toggled, name)
            switcher.append(btn)
            self._panel_buttons[name] = btn
        header.pack_start(switcher)

        reload_btn = Gtk.Button(icon_name="view-refresh-symbolic")
        reload_btn.add_css_class("flat")
        reload_btn.set_tooltip_text("Reload panels")
        reload_btn.connect("clicked", lambda _b: self._panels.refresh())
        header.pack_end(reload_btn)

        self._toolbar.add_top_bar(header)

        # Left sidebar (panels) + terminal in a resizable Gtk.Paned — like the
        # workspace. The terminal lives in _content (rebuilt on reconnect); the
        # panels are a sibling, so the terminal lifecycle never touches them.
        self._paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        self._paned.set_shrink_start_child(False)
        self._paned.set_shrink_end_child(False)
        self._paned.set_resize_start_child(False)
        self._paned.set_resize_end_child(True)
        self._paned.set_start_child(self._panels)  # panels on the LEFT
        self._paned.set_end_child(self._content)    # terminal on the right
        self._paned.set_position(380)
        self._toolbar.set_content(self._paned)
        self.set_content(self._toolbar)

        self._panel_buttons["changes"].set_active(True)  # default page

        self._terminal: TerminalView | None = None
        self._connect()

    def _make_panel_toggle(self, icon_name: str, tooltip: str) -> Gtk.ToggleButton:
        # Same as project_window._create_toolbar_button: flat, 36x36, 20px icon.
        btn = Gtk.ToggleButton()
        btn.set_tooltip_text(tooltip)
        btn.add_css_class("flat")
        btn.set_size_request(36, 36)
        gicon = IconCache().get_provider_gicon(icon_name)
        if gicon is not None:
            image = Gtk.Image.new_from_gicon(gicon)
            image.set_pixel_size(20)
            btn.set_child(image)
        return btn

    def _on_panel_toggled(self, button: Gtk.ToggleButton, name: str) -> None:
        if button.get_active():
            if not self._sidebar_toggle.get_active():
                self._sidebar_toggle.set_active(True)  # reveal the sidebar
            self._panels.select(name)
            for other, btn in self._panel_buttons.items():
                if other != name:
                    btn.set_active(False)
        elif not any(b.get_active() for b in self._panel_buttons.values()):
            button.set_active(True)

    def _on_sidebar_toggled(self, button: Gtk.ToggleButton) -> None:
        self._panels.set_visible(button.get_active())

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
