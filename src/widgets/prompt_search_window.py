"""Cross-project prompt search window (Phase 8.5).

A standalone search surface (opened from the Project Manager) that queries every
Claude session's user prompts across ``~/.claude/projects/`` and lets you jump to
the project a prompt came from. Search runs off the GTK thread via ``run_async``.
"""
from __future__ import annotations

from collections.abc import Callable

from gi.repository import Adw, Gtk, GLib

from ..services import run_async
from ..services import prompt_search
from ..utils.relative_time import humanize_relative

_DEBOUNCE_MS = 250


class PromptSearchWindow(Adw.Window):
    """Search user prompts across all projects; activating a result opens it."""

    def __init__(self, parent: Gtk.Window, on_open: Callable[[str], None]):
        super().__init__(transient_for=parent, modal=False)
        self._on_open = on_open
        self._debounce_id: int = 0

        self.set_title("Search Prompts")
        self.set_default_size(760, 580)

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(Adw.HeaderBar())

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        content.set_margin_top(12)
        content.set_margin_bottom(12)
        content.set_margin_start(12)
        content.set_margin_end(12)

        self.search_entry = Gtk.SearchEntry()
        self.search_entry.set_placeholder_text("Search your prompts across all projects…")
        self.search_entry.set_key_capture_widget(None)  # per CLAUDE.md dialog gotcha
        self.search_entry.connect("search-changed", self._on_search_changed)
        content.append(self.search_entry)

        self.status_label = Gtk.Label(xalign=0)
        self.status_label.add_css_class("dim-label")
        self.status_label.set_text("Type at least 2 characters to search.")
        content.append(self.status_label)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.results = Gtk.ListBox()
        self.results.set_selection_mode(Gtk.SelectionMode.NONE)
        self.results.add_css_class("boxed-list")
        self.results.connect("row-activated", self._on_row_activated)
        scrolled.set_child(self.results)
        content.append(scrolled)

        toolbar.set_content(content)
        self.set_content(toolbar)

    # -- search ----------------------------------------------------------

    def _on_search_changed(self, entry: Gtk.SearchEntry):
        if self._debounce_id:
            GLib.source_remove(self._debounce_id)
            self._debounce_id = 0
        query = entry.get_text().strip()
        if len(query) < 2:
            self._clear_results()
            self.status_label.set_text("Type at least 2 characters to search.")
            return
        self.status_label.set_text("Searching…")
        self._debounce_id = GLib.timeout_add(_DEBOUNCE_MS, self._run_search, query)

    def _run_search(self, query: str) -> bool:
        self._debounce_id = 0
        run_async(
            self,
            worker=lambda: prompt_search.search_prompts(query),
            on_done=self._render,
            key="prompt-search",
        )
        return False  # one-shot timeout

    def _render(self, hits: list):
        self._clear_results()
        if not hits:
            self.status_label.set_text("No matching prompts.")
            return
        self.status_label.set_text(f"{len(hits)} prompt(s) across your projects")

        current_project = None
        for hit in hits:
            if hit.project_path != current_project:
                current_project = hit.project_path
                self.results.append(self._group_header(hit))
            self.results.append(self._result_row(hit))

    def _group_header(self, hit) -> Gtk.ListBoxRow:
        row = Gtk.ListBoxRow()
        row.set_activatable(False)
        row.set_selectable(False)
        label = Gtk.Label(label=hit.project_name or hit.project_path, xalign=0)
        label.add_css_class("heading")
        label.set_margin_top(8)
        label.set_margin_start(8)
        label.set_margin_bottom(2)
        label.set_ellipsize(3)
        row.set_child(label)
        return row

    def _result_row(self, hit) -> Gtk.ListBoxRow:
        row = Gtk.ListBoxRow()
        row.project_path = hit.project_path  # what activation opens

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        box.set_margin_top(6)
        box.set_margin_bottom(6)
        box.set_margin_start(10)
        box.set_margin_end(8)

        snippet = Gtk.Label(label=hit.snippet, xalign=0)
        snippet.set_wrap(True)
        snippet.set_lines(2)
        snippet.set_ellipsize(3)
        box.append(snippet)

        when = humanize_relative(hit.timestamp) if hit.timestamp else ""
        meta = Gtk.Label(label=when, xalign=0)
        meta.add_css_class("dim-label")
        meta.add_css_class("caption")
        box.append(meta)

        row.set_child(box)
        return row

    def _on_row_activated(self, _list_box, row: Gtk.ListBoxRow):
        path = getattr(row, "project_path", None)
        if path:
            self._on_open(path)
            self.close()

    def _clear_results(self):
        child = self.results.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.results.remove(child)
            child = nxt
