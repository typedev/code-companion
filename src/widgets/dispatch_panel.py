"""Local-dispatch panel for the Project Manager ("Machines on this network").

Self-contained so the Project Manager only has to embed one widget. It owns the
machine's dispatch roles while ``dispatch.enabled``:

* **server** (only the ManagerLock owner): the broker + zeroconf advertiser, and
  the incoming "Allow this device?" pairing dialog.
* **client**: the zeroconf browser + the list of discovered peers; expanding a
  peer pairs (if needed) and lists its free sessions; activating a free session
  launches a ``RemoteSessionWindow`` via ``main.py --remote``.

All network work runs on worker/zeroconf threads and marshals to the GTK main
loop with ``GLib.idle_add``.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, GLib, Gtk

from ..services import dispatch_api
from ..services.device_identity import get_device_id, get_device_name
from ..services.dispatch_broker import DispatchBroker
from ..services.dispatch_discovery import DispatchAdvertiser, DispatchBrowser
from ..services.paired_devices import PairedDevices
from ..services.remote_tokens import RemoteTokens
from ..services.settings_service import SettingsService
from ..services.toast_service import ToastService


class DispatchPanel(Gtk.Box):
    def __init__(self, is_manager_owner: bool = True):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self._is_owner = is_manager_owner
        self.settings = SettingsService.get_instance()
        self.device_id = get_device_id()
        self.device_name = get_device_name()
        self.paired = PairedDevices()
        self.tokens = RemoteTokens()

        self._broker: DispatchBroker | None = None
        self._advertiser: DispatchAdvertiser | None = None
        self._browser: DispatchBrowser | None = None
        self._peers: list[dict] = []

        header = Gtk.Label(label="Machines on this network")
        header.add_css_class("title-4")
        header.set_xalign(0)
        self.append(header)

        self._list = Gtk.ListBox()
        self._list.add_css_class("boxed-list")
        self._list.set_selection_mode(Gtk.SelectionMode.NONE)
        self.append(self._list)

        self._empty = Gtk.Label(label="No machines found yet.")
        self._empty.add_css_class("dim-label")
        self._empty.set_xalign(0)
        self.append(self._empty)

        self.set_visible(False)
        self._settings_handler = self.settings.connect("changed", self._on_setting)
        if self.settings.get("dispatch.enabled", False):
            self.start()

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #
    def start(self) -> None:
        self.set_visible(True)
        port = int(self.settings.get("dispatch.port", 47100))
        if self._is_owner and self._broker is None:
            self._broker = DispatchBroker(port, self._pair_prompt, paired=self.paired)
            self._broker.start()
            if self.settings.get("dispatch.advertise", True):
                self._advertiser = DispatchAdvertiser(
                    port, self.device_id, self.device_name
                )
                self._advertiser.start()
        if self._browser is None:
            self._browser = DispatchBrowser(self._on_peers_bg)
            self._browser.start()
        self._render()

    def stop(self) -> None:
        for component in (self._advertiser, self._browser, self._broker):
            if component is not None:
                try:
                    component.stop()
                except Exception:
                    pass
        self._advertiser = self._browser = self._broker = None
        self._peers = []
        self.set_visible(False)

    def _on_setting(self, _settings, key: str, value) -> None:
        if key == "dispatch.enabled":
            self.start() if value else self.stop()

    # ------------------------------------------------------------------ #
    # Discovery -> peer rows
    # ------------------------------------------------------------------ #
    def _on_peers_bg(self, peers: list[dict]) -> None:
        GLib.idle_add(self._set_peers, peers)

    def _set_peers(self, peers: list[dict]) -> bool:
        # Drop ourselves (we advertise too) and any peer without a reachable host.
        self._peers = [
            p for p in peers
            if p.get("device_id") != self.device_id and p.get("host")
        ]
        self._render()
        return False

    def _render(self) -> None:
        while row := self._list.get_first_child():
            self._list.remove(row)
        for peer in self._peers:
            self._list.append(self._peer_row(peer))
        has = bool(self._peers)
        self._list.set_visible(has)
        self._empty.set_visible(not has)

    def _peer_row(self, peer: dict) -> Adw.ExpanderRow:
        row = Adw.ExpanderRow()
        row.set_title(GLib.markup_escape_text(peer.get("name", "")))
        row.set_subtitle(peer.get("host") or "")
        row._loaded = False
        row._child_rows = []

        refresh = Gtk.Button(icon_name="view-refresh-symbolic")
        refresh.add_css_class("flat")
        refresh.set_valign(Gtk.Align.CENTER)
        refresh.set_tooltip_text("Reload sessions")
        refresh.connect("clicked", lambda _b, p=peer, r=row: self._load_sessions(p, r))
        row.add_suffix(refresh)

        row.connect("notify::expanded", self._on_row_expanded, peer)
        return row

    def _on_row_expanded(self, row: Adw.ExpanderRow, _pspec, peer: dict) -> None:
        if row.get_expanded() and not row._loaded:
            row._loaded = True
            self._load_sessions(peer, row)

    # ------------------------------------------------------------------ #
    # Client: pair + list sessions + launch
    # ------------------------------------------------------------------ #
    def _load_sessions(self, peer: dict, row: Adw.ExpanderRow) -> None:
        row.set_expanded(True)
        row._loaded = True
        self._clear_children(row)
        placeholder = Adw.ActionRow()
        placeholder.set_title("Connecting…")
        row.add_row(placeholder)
        row._child_rows = [placeholder]

        def work() -> None:
            try:
                token = self.tokens.token_for(peer["device_id"])
                if not token:
                    # Blocks until the desktop user clicks Allow/Deny.
                    token = dispatch_api.pair(
                        peer["host"], peer["port"], self.device_id, self.device_name
                    )
                    self.tokens.set(peer["device_id"], peer.get("name", ""), token)
                data = dispatch_api.list_sessions(peer["host"], peer["port"], token)
                GLib.idle_add(self._show_sessions, peer, row, token, data)
            except dispatch_api.DispatchError as exc:
                GLib.idle_add(self._show_error, row, str(exc))

        threading.Thread(target=work, daemon=True).start()

    def _clear_children(self, row: Adw.ExpanderRow) -> None:
        for child in getattr(row, "_child_rows", []):
            row.remove(child)
        row._child_rows = []

    def _show_sessions(self, peer, row, token, data) -> bool:
        self._clear_children(row)
        sessions = data.get("sessions", [])
        pty_port = data.get("pty_port")
        rows = []
        if not sessions:
            r = Adw.ActionRow()
            r.set_title("No sessions")
            r.set_subtitle("Nothing is running on that machine")
            row.add_row(r)
            rows.append(r)
        for session in sessions:
            name = session.get("project_name") or session["name"]
            r = Adw.ActionRow()
            r.set_title(GLib.markup_escape_text(name))
            if session.get("held"):
                r.set_subtitle("busy · attached elsewhere")
                r.set_sensitive(False)
            else:
                r.set_subtitle("free")
                r.set_activatable(True)
                r.add_suffix(Gtk.Image.new_from_icon_name("go-next-symbolic"))
                r.connect(
                    "activated",
                    lambda _r, p=peer, pp=pty_port, sn=session["name"], t=token, nm=name:
                    self._launch(p, pp, t, sn, nm),
                )
            row.add_row(r)
            rows.append(r)
        row._child_rows = rows
        return False

    def _show_error(self, row, message: str) -> bool:
        self._clear_children(row)
        r = Adw.ActionRow()
        r.set_title("Could not connect")
        r.set_subtitle(message)
        row.add_row(r)
        row._child_rows = [r]
        ToastService.show_error(f"Dispatch: {message}")
        return False

    def _launch(self, peer, pty_port, token, session, name) -> None:
        if not pty_port:
            ToastService.show_error("Dispatch: broker did not report a PTY port")
            return
        spec = f"{peer['host']}:{pty_port}:{token}:{session}"
        # Inherit env/cwd like _open_project so `-m src.main` resolves in dev and
        # when packaged.
        subprocess.Popen(
            [sys.executable, "-m", "src.main", "--remote", spec, "--remote-title", name],
            start_new_session=True,
        )
        ToastService.show(f"Opening {name}…")

    # ------------------------------------------------------------------ #
    # Server: incoming "Allow this device?" prompt (runs on the broker loop)
    # ------------------------------------------------------------------ #
    async def _pair_prompt(self, device_id: str, device_name: str) -> bool:
        loop = asyncio.get_running_loop()
        fut = loop.create_future()

        def resolve(allowed: bool) -> None:
            if not fut.done():
                loop.call_soon_threadsafe(fut.set_result, allowed)

        GLib.idle_add(self._show_pair_dialog, device_name, resolve)
        try:
            return await asyncio.wait_for(fut, 120)
        except asyncio.TimeoutError:
            return False

    def _show_pair_dialog(self, device_name: str, resolve) -> bool:
        dialog = Adw.AlertDialog()
        dialog.set_heading("Allow this device?")
        dialog.set_body(
            f"“{device_name}” wants to attach to a session on this machine."
        )
        dialog.add_response("deny", "Deny")
        dialog.add_response("allow", "Allow")
        dialog.set_response_appearance("allow", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("allow")
        dialog.set_close_response("deny")
        dialog.connect("response", lambda _d, resp: resolve(resp == "allow"))
        root = self.get_root()
        if root is not None:
            dialog.present(root)
        else:  # no window yet — fail safe
            resolve(False)
        return False
