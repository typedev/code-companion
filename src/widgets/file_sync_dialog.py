"""'Sync files' dialog: pick an online paired peer, preview the mirror, and run
a directional file-sync (Get from peer / Give to peer) with a progress bar.

Discovery + tokens reuse the local-dispatch layer; the actual transfer runs on a
worker thread and marshals progress back with ``GLib.idle_add``. Destroyed local
files land in ``<project>/.deleted/`` (see ``file_sync_service``), so a
wrong-direction Get is recoverable.
"""

from __future__ import annotations

import threading
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from ..services import file_sync_service as svc  # noqa: E402
from ..services.device_identity import get_device_id  # noqa: E402
from ..services.dispatch_discovery import DispatchBrowser  # noqa: E402
from ..services.remote_tokens import RemoteTokens  # noqa: E402
from ..services.toast_service import ToastService  # noqa: E402
from ..utils.project_identity import resolve_project_identity  # noqa: E402

_DISCOVERY_MS = 1500


class FileSyncDialog:
    """Controller for the Sync-files flow (not a widget subclass)."""

    def __init__(self, parent: Gtk.Widget, project_path: Path, on_synced=None):
        self._parent = parent
        self._project_path = Path(project_path)
        self._on_synced = on_synced  # called on the main thread after a Get applies

        self._device_id = get_device_id()
        self._tokens = RemoteTokens()

        self._project_id: str | None = None
        self._browser: DispatchBrowser | None = None
        self._peers: list[svc.Peer] = []
        self._preview: svc.SyncPreview | None = None

        self._dialog: Adw.AlertDialog | None = None
        self._dropdown: Gtk.DropDown | None = None
        self._counts_label: Gtk.Label | None = None
        self._warn_label: Gtk.Label | None = None

        self._progress_dialog: Adw.AlertDialog | None = None
        self._progress_bar: Gtk.ProgressBar | None = None
        self._cancelled = False

    # -- entry ---------------------------------------------------------------
    def present(self) -> None:
        dialog = Adw.AlertDialog()
        dialog.set_heading("Sync files")
        dialog.set_body("Searching for paired devices on the network…")
        dialog.add_response("cancel", "Cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", self._on_response)
        self._dialog = dialog

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_top(4)
        spinner = Gtk.Spinner()
        spinner.start()
        box.append(spinner)
        dialog.set_extra_child(box)
        dialog.present(self._parent)

        self._browser = DispatchBrowser(lambda peers: None)  # snapshot-polled below
        self._browser.start()
        GLib.timeout_add(_DISCOVERY_MS, self._discovery_done)

    # -- discovery -----------------------------------------------------------
    def _discovery_done(self) -> bool:
        raw = self._browser.peers() if self._browser else []
        if self._browser:
            self._browser.stop()
            self._browser = None

        identity = resolve_project_identity(self._project_path)
        self._project_id = identity.project_id if identity else None

        self._peers = self._paired_online(raw)
        self._render_selection()
        return False

    def _paired_online(self, raw: list[dict]) -> list[svc.Peer]:
        peers = []
        for p in raw:
            did = p.get("device_id")
            if not did or did == self._device_id or not p.get("host"):
                continue
            token = self._tokens.token_for(did)
            if not token:
                continue  # discovered but not paired -> skip
            peers.append(svc.Peer(did, p.get("name", did), p["host"], int(p["port"]), token))
        return peers

    # -- selection UI --------------------------------------------------------
    def _render_selection(self) -> None:
        dialog = self._dialog
        if dialog is None:
            return

        if self._project_id is None:
            dialog.set_body(
                "This project has no sync identity (needs a git remote or a commit)."
            )
            dialog.set_extra_child(None)
            return
        if not self._peers:
            dialog.set_body(
                "No paired device is online on this network.\n"
                "Open Code Companion on the other machine (dispatch enabled) and pair it first."
            )
            dialog.set_extra_child(None)
            return

        dialog.set_body("Choose a device and a direction. A preview is shown below.")

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_margin_top(4)

        labels = [f"{p.name}  ({p.host})" for p in self._peers]
        self._dropdown = Gtk.DropDown.new_from_strings(labels)
        self._dropdown.connect("notify::selected", lambda *_: self._refresh_preview())
        box.append(self._dropdown)

        self._counts_label = Gtk.Label(xalign=0)
        self._counts_label.set_wrap(True)
        box.append(self._counts_label)

        self._warn_label = Gtk.Label(xalign=0)
        self._warn_label.set_wrap(True)
        self._warn_label.add_css_class("error")
        box.append(self._warn_label)

        dialog.set_extra_child(box)
        dialog.add_response("get", "Get files")
        dialog.set_response_appearance("get", Adw.ResponseAppearance.SUGGESTED)
        self._set_actions_enabled(False)
        self._refresh_preview()

    def _set_actions_enabled(self, enabled: bool) -> None:
        if self._dialog is not None:
            self._dialog.set_response_enabled("get", enabled)

    def _selected_peer(self) -> svc.Peer | None:
        if self._dropdown is None:
            return None
        idx = self._dropdown.get_selected()
        if 0 <= idx < len(self._peers):
            return self._peers[idx]
        return None

    def _refresh_preview(self) -> None:
        peer = self._selected_peer()
        if peer is None or self._project_id is None:
            return
        self._preview = None
        self._set_actions_enabled(False)
        if self._counts_label:
            self._counts_label.set_text("Comparing with device…")
        if self._warn_label:
            self._warn_label.set_text("")

        project_id = self._project_id
        path = str(self._project_path)

        def worker():
            try:
                preview = svc.build_preview(path, project_id, peer)
                GLib.idle_add(self._preview_ready, peer, preview, None)
            except Exception as exc:  # network / broker error
                GLib.idle_add(self._preview_ready, peer, None, str(exc))

        threading.Thread(target=worker, daemon=True).start()

    def _preview_ready(self, peer, preview, error) -> bool:
        if self._dialog is None or self._selected_peer() is not peer:
            return False  # dialog closed or selection moved on
        if error is not None or preview is None:
            if self._counts_label:
                self._counts_label.set_text(f"Could not reach the device: {error}")
            return False
        self._preview = preview
        if preview.diff.identical:
            self._counts_label.set_text("Already in sync — nothing to transfer.")
            self._warn_label.set_text("")
            self._set_actions_enabled(False)
            return False

        get = preview.get
        self._counts_label.set_text(f"Get will fetch {len(get.fetch)} file(s) from this device.")
        if get.destructive_count:
            self._warn_label.set_text(
                f"Get will overwrite/remove {get.destructive_count} local file(s) "
                f"(recoverable in .deleted/)."
            )
        else:
            self._warn_label.set_text("")
        self._set_actions_enabled(True)
        return False

    # -- responses -----------------------------------------------------------
    def _on_response(self, dialog, response) -> None:
        if response == "get":
            self._start_get()
        # any response closes the selection dialog
        if self._browser:
            self._browser.stop()
            self._browser = None

    # -- Get (with progress) -------------------------------------------------
    def _start_get(self) -> None:
        peer = self._selected_peer()
        if peer is None or self._project_id is None:
            return
        self._cancelled = False
        self._present_progress(f"Getting files from {peer.name}…")

        project_id = self._project_id
        path = str(self._project_path)

        def progress(done, total, rel):
            if self._cancelled:
                raise RuntimeError("cancelled")
            GLib.idle_add(self._update_progress, done, total)

        def worker():
            try:
                result = svc.run_get(path, project_id, peer, progress=progress)
                GLib.idle_add(self._get_done, result, None)
            except Exception as exc:
                GLib.idle_add(self._get_done, None, str(exc))

        threading.Thread(target=worker, daemon=True).start()

    def _present_progress(self, heading: str) -> None:
        dlg = Adw.AlertDialog()
        dlg.set_heading(heading)
        dlg.add_response("cancel", "Cancel")
        dlg.set_close_response("cancel")
        dlg.connect("response", lambda *_: setattr(self, "_cancelled", True))
        bar = Gtk.ProgressBar()
        bar.set_show_text(True)
        bar.set_text("Preparing…")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.set_margin_top(4)
        box.append(bar)
        dlg.set_extra_child(box)
        self._progress_dialog = dlg
        self._progress_bar = bar
        dlg.present(self._parent)

    def _update_progress(self, done, total) -> bool:
        if self._progress_bar is None:
            return False
        frac = (done / total) if total else 1.0
        self._progress_bar.set_fraction(frac)
        self._progress_bar.set_text(f"{done} / {total}")
        return False

    def _get_done(self, result, error) -> bool:
        if self._progress_dialog is not None:
            self._progress_dialog.close()
            self._progress_dialog = None
            self._progress_bar = None
        if error and not self._cancelled:
            ToastService.show(f"Sync failed: {error}")
        elif self._cancelled:
            ToastService.show("Sync cancelled")
        elif result is not None:
            ToastService.show(
                f"Got {result.fetched} file(s) "
                f"({result.overwritten} replaced, {result.removed} removed → .deleted/)"
            )
            if self._on_synced is not None:
                self._on_synced()
        return False
