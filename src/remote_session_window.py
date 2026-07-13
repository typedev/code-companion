"""Lightweight window for a session attached from another machine (local dispatch).

This is deliberately *not* a ``ProjectWindow`` (no ProjectLock, GitService,
FileTree, registry or file monitor — those all assume a real local project
path). Layout mirrors the workspace: a left sidebar of read-only navigator
panels (Changes / Files / Problems) and a main tab area whose first, pinned tab
is the terminal relaying the desktop's tmux session; activating a file or change
opens it as a tab beside the terminal. Closing the window just detaches the
relay client — the desktop session keeps running.
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GtkSource", "5")

from gi.repository import Adw, GLib, GtkSource, Gtk

from .services import dispatch_api
from .services.icon_cache import IconCache
from .services.settings_service import SettingsService
from .services.toast_service import ToastService
from .widgets import TerminalView
from .widgets.code_view import DiffView, get_language_for_file
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
        self._panels = RemotePanels(
            host, self._http_port, token, session,
            on_open_file=self.open_file_tab,
            on_open_diff=self.open_diff_tab,
        )
        self._file_page = None  # reused viewer tab (like the workspace git-diff tab)
        self._diff_page = None

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

        # Main area: a tab view like the workspace. The terminal is the first,
        # pinned tab (lives in _content, rebuilt on reconnect); files/diffs open
        # as tabs beside it.
        self._tab_view = Adw.TabView()
        self._tab_view.set_vexpand(True)
        self._tab_view.connect("close-page", self._on_tab_close)
        self._tab_bar = Adw.TabBar()
        self._tab_bar.set_autohide(False)
        self._tab_bar.set_view(self._tab_view)
        main_area = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        main_area.append(self._tab_bar)
        main_area.append(self._tab_view)

        self._terminal_page = self._tab_view.append(self._content)
        self._terminal_page.set_title("Terminal")
        self._tab_view.set_page_pinned(self._terminal_page, True)
        tgicon = IconCache().get_provider_gicon("claude")
        if tgicon is not None:
            self._terminal_page.set_icon(tgicon)

        # Left sidebar (panels) + main tab area in a resizable Gtk.Paned.
        self._paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        self._paned.set_shrink_start_child(False)
        self._paned.set_shrink_end_child(False)
        self._paned.set_resize_start_child(False)
        self._paned.set_resize_end_child(True)
        self._paned.set_start_child(self._panels)  # panels on the LEFT
        self._paned.set_end_child(main_area)        # tabs on the right
        self._paned.set_position(320)
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

    def _on_tab_close(self, tab_view, page) -> bool:
        # Drop our reused-tab references so a later open re-creates them; let the
        # default handler actually close the page.
        if page is self._file_page:
            self._file_page = None
        if page is self._diff_page:
            self._diff_page = None
        return False

    # ------------------------------------------------------------------ #
    # Open content as tabs (panels call these; fetch off the GTK thread)
    # ------------------------------------------------------------------ #
    def open_file_tab(self, path: str) -> None:
        def work() -> None:
            try:
                data = dispatch_api.read_file(self._host, self._http_port, self._token, self._session, path)
            except dispatch_api.DispatchError as exc:
                data = {"ok": False, "error": str(exc)}
            GLib.idle_add(self._show_file_tab, path, data)

        threading.Thread(target=work, daemon=True).start()

    def _show_file_tab(self, path: str, data: dict) -> bool:
        if not data.get("ok"):
            ToastService.show_error(f"Dispatch: {data.get('error', 'could not read file')}")
            return False
        if data.get("binary"):
            ToastService.show(f"{path} — binary file")
            return False
        view = self._make_source_view(data.get("content", ""), path, bool(data.get("truncated")))
        if self._file_page is not None:
            self._tab_view.close_page(self._file_page)
        self._file_page = self._tab_view.append(view)
        self._file_page.set_title(Path(path).name)
        self._file_page.set_tooltip(path)
        self._tab_view.set_selected_page(self._file_page)
        return False

    def open_diff_tab(self, path: str, staged: bool) -> None:
        def work() -> None:
            try:
                data = dispatch_api.get_file_diff(self._host, self._http_port, self._token, self._session, path, staged)
            except dispatch_api.DispatchError as exc:
                data = {"ok": False, "error": str(exc)}
            GLib.idle_add(self._show_diff_tab, path, staged, data)

        threading.Thread(target=work, daemon=True).start()

    def _show_diff_tab(self, path: str, staged: bool, data: dict) -> bool:
        if not data.get("ok"):
            ToastService.show_error(f"Dispatch: {data.get('error', 'could not load diff')}")
            return False
        view = DiffView(data.get("old", ""), data.get("new", ""), file_path=path)
        view.set_vexpand(True)
        if self._diff_page is not None:
            self._tab_view.close_page(self._diff_page)
        self._diff_page = self._tab_view.append(view)
        self._diff_page.set_title(("[staged] " if staged else "") + Path(path).name)
        self._diff_page.set_tooltip(path)
        self._tab_view.set_selected_page(self._diff_page)
        return False

    def _make_source_view(self, content: str, path: str, truncated: bool) -> Gtk.Widget:
        buf = GtkSource.Buffer()
        lang_id = get_language_for_file(path)
        if lang_id:
            lang = GtkSource.LanguageManager.get_default().get_language(lang_id)
            if lang:
                buf.set_language(lang)
        scheme_id = SettingsService.get_instance().get("appearance.syntax_scheme", "Adwaita-dark")
        scheme = GtkSource.StyleSchemeManager.get_default().get_scheme(scheme_id)
        if scheme:
            buf.set_style_scheme(scheme)
        buf.set_text(content + ("\n\n… (truncated)" if truncated else ""))
        view = GtkSource.View(buffer=buf)
        view.set_editable(False)
        view.set_cursor_visible(False)
        view.set_monospace(True)
        view.set_show_line_numbers(True)
        scroll = Gtk.ScrolledWindow()
        scroll.set_vexpand(True)
        scroll.set_child(view)
        return scroll

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
