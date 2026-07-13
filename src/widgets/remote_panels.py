"""Read-only navigator panels for a remote (dispatched) session.

Just the *lists* — Changes / Files / Problems — pulled as plain JSON from the
broker (which computes them from the session's project path). Activating a row
calls back into the window, which opens the content as a TAB in the main area
(the app's standard scheme), never inside this sidebar.
"""

from __future__ import annotations

import threading
from typing import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, GLib, Gtk

from ..services import dispatch_api


class RemotePanels(Gtk.Box):
    # (page name, Material icon file stem, tooltip). The switcher lives in the
    # window header; this widget is just the stack of lists.
    TABS = [
        ("changes", "git", "Changes"),
        ("files", "folder-open", "Files"),
        ("problems", "problems", "Problems"),
    ]

    def __init__(
        self,
        host: str,
        http_port: int,
        token: str,
        session: str,
        *,
        on_open_file: Callable[[str], None],
        on_open_diff: Callable[[str, bool], None],
    ):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self._host = host
        self._port = http_port
        self._token = token
        self._session = session
        self._on_open_file = on_open_file
        self._on_open_diff = on_open_diff
        self._loaded: set[str] = set()
        self._all_files: list[str] = []
        self.set_size_request(280, -1)

        self._stack = Gtk.Stack()
        self._stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self._stack.set_hexpand(True)
        self._stack.set_vexpand(True)
        self._stack.add_named(self._build_changes_page(), "changes")
        self._stack.add_named(self._build_files_page(), "files")
        self._stack.add_named(self._build_problems_page(), "problems")
        self.append(self._stack)

        self._stack.connect("notify::visible-child-name", self._on_page_changed)
        GLib.idle_add(self._ensure_loaded, "changes")

    def select(self, name: str) -> None:
        self._stack.set_visible_child_name(name)

    def current(self) -> str | None:
        return self._stack.get_visible_child_name()

    # ------------------------------------------------------------------ #
    # Fetch plumbing
    # ------------------------------------------------------------------ #
    def _fetch(self, fn, on_ok) -> None:
        def work() -> None:
            try:
                data = fn()
            except dispatch_api.DispatchError as exc:
                GLib.idle_add(on_ok, {"ok": False, "error": str(exc)})
                return
            GLib.idle_add(on_ok, data)

        threading.Thread(target=work, daemon=True).start()

    def _on_page_changed(self, *_a) -> None:
        self._ensure_loaded(self._stack.get_visible_child_name())

    def _ensure_loaded(self, name: str) -> bool:
        if name and name not in self._loaded:
            self._loaded.add(name)
            self._reload(name)
        return False

    def refresh(self) -> None:
        name = self._stack.get_visible_child_name()
        if name:
            self._reload(name)

    def _reload(self, name: str) -> None:
        if name == "changes":
            self._fetch(
                lambda: dispatch_api.get_changes(self._host, self._port, self._token, self._session),
                self._render_changes,
            )
        elif name == "files":
            self._fetch(
                lambda: dispatch_api.list_files(self._host, self._port, self._token, self._session),
                self._render_files,
            )
        elif name == "problems":
            self._fetch(
                lambda: dispatch_api.get_problems(self._host, self._port, self._token, self._session),
                self._render_problems,
            )

    @staticmethod
    def _clear(listbox: Gtk.ListBox) -> None:
        while row := listbox.get_first_child():
            listbox.remove(row)

    @staticmethod
    def _placeholder(text: str) -> Gtk.Widget:
        lbl = Gtk.Label(label=text)
        lbl.add_css_class("dim-label")
        lbl.set_wrap(True)
        lbl.set_margin_top(12)
        lbl.set_margin_bottom(12)
        lbl.set_margin_start(8)
        lbl.set_margin_end(8)
        return Gtk.ListBoxRow(child=lbl, activatable=False, selectable=False)

    @staticmethod
    def _scrolled(child: Gtk.Widget) -> Gtk.ScrolledWindow:
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        scroll.set_child(child)
        return scroll

    # ------------------------------------------------------------------ #
    # Changes
    # ------------------------------------------------------------------ #
    def _build_changes_page(self) -> Gtk.Widget:
        self._changes_list = Gtk.ListBox()
        self._changes_list.add_css_class("boxed-list")
        self._changes_list.set_selection_mode(Gtk.SelectionMode.NONE)
        self._changes_list.set_margin_start(6)
        self._changes_list.set_margin_end(6)
        self._changes_list.set_margin_top(6)
        return self._scrolled(self._changes_list)

    def _render_changes(self, data: dict) -> bool:
        self._clear(self._changes_list)
        if not data.get("ok", True):
            self._changes_list.append(self._placeholder(data.get("error", "error")))
            return False
        changes = data.get("changes", [])
        if not changes:
            self._changes_list.append(self._placeholder("No uncommitted changes"))
            return False
        for c in changes:
            row = Adw.ActionRow()
            row.set_title(GLib.markup_escape_text(c["path"]))
            row.set_subtitle("staged" if c.get("staged") else "")
            badge = Gtk.Label(label=c.get("status", "?"))
            badge.add_css_class("dim-label")
            badge.set_valign(Gtk.Align.CENTER)
            row.add_prefix(badge)
            row.set_activatable(True)
            row.connect("activated", lambda _r, p=c["path"], s=bool(c.get("staged")): self._on_open_diff(p, s))
            self._changes_list.append(row)
        return False

    # ------------------------------------------------------------------ #
    # Files (quick-open list)
    # ------------------------------------------------------------------ #
    def _build_files_page(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_margin_start(6)
        box.set_margin_end(6)
        box.set_margin_top(6)
        self._files_search = Gtk.SearchEntry()
        self._files_search.set_placeholder_text("Open file…")
        self._files_search.set_key_capture_widget(None)
        self._files_search.connect("search-changed", lambda _e: self._filter_files())
        box.append(self._files_search)

        self._files_list = Gtk.ListBox()
        self._files_list.add_css_class("boxed-list")
        self._files_list.set_selection_mode(Gtk.SelectionMode.NONE)
        box.append(self._scrolled(self._files_list))
        return box

    def _render_files(self, data: dict) -> bool:
        self._all_files = data.get("files", []) if data.get("ok", True) else []
        self._filter_files()
        return False

    def _filter_files(self) -> None:
        query = self._files_search.get_text().strip().lower()
        self._clear(self._files_list)
        matches = [f for f in self._all_files if query in f.lower()] if query else self._all_files
        for path in matches[:300]:
            row = Adw.ActionRow()
            row.set_title(GLib.markup_escape_text(path))
            row.set_activatable(True)
            row.connect("activated", lambda _r, p=path: self._on_open_file(p))
            self._files_list.append(row)
        if not matches:
            self._files_list.append(self._placeholder("No matching files"))
        elif len(matches) > 300:
            self._files_list.append(self._placeholder(f"…{len(matches) - 300} more — refine search"))

    # ------------------------------------------------------------------ #
    # Problems
    # ------------------------------------------------------------------ #
    def _build_problems_page(self) -> Gtk.Widget:
        self._problems_list = Gtk.ListBox()
        self._problems_list.add_css_class("boxed-list")
        self._problems_list.set_selection_mode(Gtk.SelectionMode.NONE)
        self._problems_list.set_margin_start(6)
        self._problems_list.set_margin_end(6)
        self._problems_list.set_margin_top(6)
        return self._scrolled(self._problems_list)

    def _render_problems(self, data: dict) -> bool:
        self._clear(self._problems_list)
        if not data.get("ok", True):
            self._problems_list.append(self._placeholder(data.get("error", "error")))
            return False
        problems = data.get("problems", [])
        if not problems:
            note = "No problems" if data.get("has_run", True) else "No problems yet"
            self._problems_list.append(self._placeholder(note))
            return False
        for p in problems:
            row = Adw.ActionRow()
            loc = f"{p.get('file', '')}:{p.get('line', '')}"
            row.set_title(GLib.markup_escape_text(p.get("message", "")))
            row.set_subtitle(GLib.markup_escape_text(f"{loc}  {p.get('code', '')}".strip()))
            sev = Gtk.Label(label=(p.get("severity", "") or "")[:1].upper())
            sev.add_css_class("dim-label")
            sev.set_valign(Gtk.Align.CENTER)
            row.add_prefix(sev)
            row.set_activatable(True)
            row.connect("activated", lambda _r, f=p.get("file", ""): self._on_open_file(f))
            self._problems_list.append(row)
        return False
