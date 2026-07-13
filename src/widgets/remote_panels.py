"""Read-only side panels for a remote (dispatched) session.

Shows the *live desktop working state* of the attached session — pulled as plain
JSON from the broker (which proxies the session's MCP tools):

* **Changes** — uncommitted files → click → `DiffView`.
* **Files**   — quick-open any project file → read-only source view.
* **Problems**— the session's linter findings.

All fetches run on a worker thread and marshal back with `GLib.idle_add`.
History / memory / messages / issues are NOT here — the laptop's normal app shows
those via sync/cloud.
"""

from __future__ import annotations

import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GtkSource", "5")

from gi.repository import Adw, GLib, GtkSource, Gtk

from ..services import dispatch_api
from ..services.settings_service import SettingsService
from .code_view import DiffView, get_language_for_file


class RemotePanels(Gtk.Box):
    # (page name, Material icon file stem, tooltip). The switcher toggles live in
    # the window header (like the workspace); this widget is just the stack.
    TABS = [
        ("changes", "git", "Changes"),
        ("files", "folder-open", "Files"),
        ("problems", "problems", "Problems"),
    ]

    def __init__(self, host: str, http_port: int, token: str, session: str):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self._host = host
        self._port = http_port
        self._token = token
        self._session = session
        self._loaded: set[str] = set()
        self._all_files: list[str] = []
        self.set_size_request(300, -1)

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
        """Show a page by name (driven by the header switcher)."""
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
            self._changes_stack.set_visible_child_name("list")
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
    def _placeholder(text: str) -> Gtk.Label:
        lbl = Gtk.Label(label=text)
        lbl.add_css_class("dim-label")
        lbl.set_margin_top(12)
        lbl.set_margin_bottom(12)
        return lbl

    # ------------------------------------------------------------------ #
    # Changes page (master/detail)
    # ------------------------------------------------------------------ #
    def _build_changes_page(self) -> Gtk.Widget:
        self._changes_stack = Gtk.Stack()

        self._changes_list = Gtk.ListBox()
        self._changes_list.add_css_class("boxed-list")
        self._changes_list.set_selection_mode(Gtk.SelectionMode.NONE)
        list_scroll = Gtk.ScrolledWindow()
        list_scroll.set_vexpand(True)
        list_scroll.set_child(self._changes_list)
        list_scroll.set_margin_start(6)
        list_scroll.set_margin_end(6)
        list_scroll.set_margin_bottom(6)
        self._changes_stack.add_named(list_scroll, "list")

        detail = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        back = Gtk.Button(label="← Changes")
        back.add_css_class("flat")
        back.set_halign(Gtk.Align.START)
        back.set_margin_start(6)
        back.set_margin_top(6)
        back.connect("clicked", lambda _b: self._changes_stack.set_visible_child_name("list"))
        detail.append(back)
        self._changes_detail = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self._changes_detail.set_vexpand(True)
        detail.append(self._changes_detail)
        self._changes_stack.add_named(detail, "detail")

        self._changes_stack.set_visible_child_name("list")
        return self._changes_stack

    def _render_changes(self, data: dict) -> bool:
        self._clear(self._changes_list)
        if not data.get("ok", True):
            self._changes_list.append(Gtk.ListBoxRow(child=self._placeholder(data.get("error", "error"))))
            return False
        changes = data.get("changes", [])
        if not changes:
            self._changes_list.append(Gtk.ListBoxRow(child=self._placeholder("No uncommitted changes")))
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
            row.connect("activated", lambda _r, p=c["path"], s=bool(c.get("staged")): self._open_diff(p, s))
            self._changes_list.append(row)
        return False

    def _open_diff(self, path: str, staged: bool) -> None:
        while child := self._changes_detail.get_first_child():
            self._changes_detail.remove(child)
        self._changes_detail.append(self._placeholder(f"Loading {path}…"))
        self._changes_stack.set_visible_child_name("detail")
        self._fetch(
            lambda: dispatch_api.get_file_diff(self._host, self._port, self._token, self._session, path, staged),
            lambda d: self._render_diff(path, d),
        )

    def _render_diff(self, path: str, data: dict) -> bool:
        while child := self._changes_detail.get_first_child():
            self._changes_detail.remove(child)
        if not data.get("ok"):
            self._changes_detail.append(self._placeholder(data.get("error", "could not load diff")))
            return False
        view = DiffView(data.get("old", ""), data.get("new", ""), file_path=path)
        view.set_vexpand(True)
        self._changes_detail.append(view)
        return False

    # ------------------------------------------------------------------ #
    # Files page (quick-open + read-only view)
    # ------------------------------------------------------------------ #
    def _build_files_page(self) -> Gtk.Widget:
        self._files_stack = Gtk.Stack()

        list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        list_box.set_margin_start(6)
        list_box.set_margin_end(6)
        list_box.set_margin_bottom(6)
        self._files_search = Gtk.SearchEntry()
        self._files_search.set_placeholder_text("Open file…")
        self._files_search.set_key_capture_widget(None)
        self._files_search.connect("search-changed", lambda _e: self._filter_files())
        list_box.append(self._files_search)

        self._files_list = Gtk.ListBox()
        self._files_list.add_css_class("boxed-list")
        self._files_list.set_selection_mode(Gtk.SelectionMode.NONE)
        files_scroll = Gtk.ScrolledWindow()
        files_scroll.set_vexpand(True)
        files_scroll.set_child(self._files_list)
        list_box.append(files_scroll)
        self._files_stack.add_named(list_box, "list")

        detail = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        back = Gtk.Button(label="← Files")
        back.add_css_class("flat")
        back.set_halign(Gtk.Align.START)
        back.set_margin_start(6)
        back.set_margin_top(6)
        back.connect("clicked", lambda _b: self._files_stack.set_visible_child_name("list"))
        detail.append(back)
        self._files_detail = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self._files_detail.set_vexpand(True)
        detail.append(self._files_detail)
        self._files_stack.add_named(detail, "detail")

        self._files_stack.set_visible_child_name("list")
        return self._files_stack

    def _render_files(self, data: dict) -> bool:
        if not data.get("ok", True):
            self._all_files = []
        else:
            self._all_files = data.get("files", [])
        self._filter_files()
        return False

    def _filter_files(self) -> None:
        query = self._files_search.get_text().strip().lower()
        self._clear(self._files_list)
        matches = [f for f in self._all_files if query in f.lower()] if query else self._all_files
        for path in matches[:300]:  # cap rendered rows; refine the search to narrow
            row = Adw.ActionRow()
            row.set_title(GLib.markup_escape_text(path))
            row.set_activatable(True)
            row.connect("activated", lambda _r, p=path: self._open_file(p))
            self._files_list.append(row)
        if not matches:
            self._files_list.append(Gtk.ListBoxRow(child=self._placeholder("No matching files")))
        elif len(matches) > 300:
            self._files_list.append(Gtk.ListBoxRow(child=self._placeholder(f"…{len(matches) - 300} more — refine search")))

    def _open_file(self, path: str) -> None:
        while child := self._files_detail.get_first_child():
            self._files_detail.remove(child)
        self._files_detail.append(self._placeholder(f"Loading {path}…"))
        self._files_stack.set_visible_child_name("detail")
        self._fetch(
            lambda: dispatch_api.read_file(self._host, self._port, self._token, self._session, path),
            lambda d: self._render_file(path, d),
        )

    def _render_file(self, path: str, data: dict) -> bool:
        while child := self._files_detail.get_first_child():
            self._files_detail.remove(child)
        if not data.get("ok"):
            self._files_detail.append(self._placeholder(data.get("error", "could not read file")))
            return False
        if data.get("binary"):
            self._files_detail.append(self._placeholder(f"{path} — binary file"))
            return False
        self._files_detail.append(self._source_view(data.get("content", ""), path, bool(data.get("truncated"))))
        return False

    def _source_view(self, content: str, path: str, truncated: bool) -> Gtk.Widget:
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
    # Problems page
    # ------------------------------------------------------------------ #
    def _build_problems_page(self) -> Gtk.Widget:
        self._problems_list = Gtk.ListBox()
        self._problems_list.add_css_class("boxed-list")
        self._problems_list.set_selection_mode(Gtk.SelectionMode.NONE)
        scroll = Gtk.ScrolledWindow()
        scroll.set_vexpand(True)
        scroll.set_child(self._problems_list)
        scroll.set_margin_start(6)
        scroll.set_margin_end(6)
        scroll.set_margin_bottom(6)
        scroll.set_margin_top(6)
        return scroll

    def _render_problems(self, data: dict) -> bool:
        self._clear(self._problems_list)
        if not data.get("ok", True):
            self._problems_list.append(Gtk.ListBoxRow(child=self._placeholder(data.get("error", "error"))))
            return False
        problems = data.get("problems", [])
        if not problems:
            note = "No problems" if data.get("has_run", True) else "Open the Problems tab on the desktop to populate"
            self._problems_list.append(Gtk.ListBoxRow(child=self._placeholder(note)))
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
            self._problems_list.append(row)
        return False
