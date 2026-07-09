"""Project Manager window for selecting and opening projects."""

import json
import shutil
import signal
import subprocess
import sys
import threading
from pathlib import Path

from gi.repository import Adw, Gtk, GLib, Gio, Gdk, GObject

from .services.project_registry import ProjectRegistry
from .services.project_lock import ManagerLock
from .services.project_status_service import (
    ProjectStatusService,
    LocalStatus,
    RemoteStatus,
)
from .services.git_service import AuthenticationRequired, GitService
from .services.issues_service import GitHubError
from .services.sync_service import SyncService
from .services import session_summary_service
from .services import message_store
from .services import worktree_reports
from .services import github_repos
from .services.credential_service import CredentialService
from .services.toast_service import ToastService
from .services.config_path import get_config_dir
from .services.icon_cache import IconCache
from .utils.atomic_write import atomic_write_text
from .utils.project_identity import resolve_project_identity
from .models.sync import ProjectSyncStatus, SyncState
from .services import session_notify
from .utils import claude_session
from .utils.relative_time import humanize_relative, humanize_relative_iso
from .utils.git_worktree import is_linked_worktree, worktree_parent_root, slugify
from .utils.markdown_markup import markdown_to_pango
from .widgets.github_auth import show_github_credentials_dialog
from .widgets.prompt_search_window import PromptSearchWindow
from .version import __version__, get_version_info


# Inline CSS for the larger project cards and their status badges.
_BADGE_CSS = b"""
.project-card {
    min-height: 56px;
}
.project-card-title {
    font-size: 1.1em;
    font-weight: bold;
}
.project-card-path {
    font-size: 0.85em;
}
.cc-worktree-child {
    border-left: 3px solid alpha(@accent_color, 0.4);
}
.cc-badge {
    font-size: 11px;
    font-weight: bold;
    padding: 2px 8px;
    border-radius: 9px;
}
.cc-badge image {
    -gtk-icon-size: 13px;
}
.cc-badge-git    { background: alpha(#2ec27e, 0.18); color: #26a269; }
.cc-badge-norepo { background: alpha(@theme_fg_color, 0.10); color: alpha(@theme_fg_color, 0.55); }
.cc-badge-dirty  { background: alpha(#e5a50a, 0.20); color: #c07f00; }
.cc-badge-ahead  { background: alpha(#3584e4, 0.20); color: #1c71d8; }
.cc-badge-behind { background: alpha(#e66100, 0.22); color: #c64600; }
.cc-badge-pr     { background: alpha(#2ec27e, 0.18); color: #26a269; }
.cc-badge-issue  { background: alpha(@accent_color, 0.20); color: @accent_color; }
.cc-badge-message { background: alpha(#3584e4, 0.20); color: #1c71d8; }
.cc-badge-worktree { background: alpha(#f5a623, 0.22); color: #c46a00; }
.cc-badge-missing { background: alpha(#e01b24, 0.20); color: #c01c28; }
.cc-badge-local  { background: alpha(@theme_fg_color, 0.10); color: alpha(@theme_fg_color, 0.55); }
.cc-badge-synced   { background: alpha(#33d17a, 0.18); color: #26a269; }
.cc-badge-conflict { background: alpha(#e01b24, 0.22); color: #c01c28; }
.cc-badge-syncoff  { background: alpha(@theme_fg_color, 0.10); color: alpha(@theme_fg_color, 0.55); }
.cc-live-dot { color: #2ec27e; -gtk-icon-size: 12px; }
.cc-live-dot-attention { color: #e5a50a; -gtk-icon-size: 12px; }
.cc-orphan-btn { color: #c07f00; font-weight: bold; }
"""


class _RepoItem(GObject.Object):
    """One row in the Clone dialog's GitHub-repo dropdown."""

    __gtype_name__ = "CloneRepoItem"
    label = GObject.Property(type=str, default="")

    def __init__(self, label="", name="", clone_url="", private=False):
        super().__init__(label=label)
        self.name = name
        self.clone_url = clone_url
        self.private = private


class ProjectManagerWindow(Adw.ApplicationWindow):
    """Window for managing and selecting projects."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        self.registry = ProjectRegistry()
        self.status_service = ProjectStatusService.get_instance()
        self.sync_service = SyncService.get_instance()
        self._manager_lock = ManagerLock()
        self._manager_lock.acquire()

        # Maps resolved project path -> its ListBoxRow, for async status updates.
        self._rows_by_path: dict[str, Adw.ActionRow] = {}
        self._refreshing = False
        self._syncing = False
        self._query = ""

        self._setup_css()
        self._setup_signal_handler()
        self._setup_window()
        self._build_ui()
        self._load_projects()

    def _setup_css(self):
        """Install the badge stylesheet once for the default display."""
        provider = Gtk.CssProvider()
        provider.load_from_data(_BADGE_CSS)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

        self.connect("destroy", self._on_destroy)
        # Re-sort MRU when the manager regains focus (a project opened in another
        # process only writes projects.json; this picks up the new open order).
        self.connect("notify::is-active", self._on_active_changed)

    def _setup_signal_handler(self):
        """Setup SIGUSR1 handler to bring window to front."""
        def on_sigusr1(signum, frame):
            # Use GLib.idle_add to safely call GTK from signal handler
            GLib.idle_add(self.present)

        signal.signal(signal.SIGUSR1, on_sigusr1)

    def _on_destroy(self, _widget):
        """Release lock when window is destroyed."""
        self._manager_lock.release()

    def _setup_window(self):
        """Configure window properties."""
        self.set_title("Code Companion")
        self.set_default_size(500, 600)

    def _build_ui(self):
        """Build the UI layout."""
        # Main box
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        # Header bar
        header = Adw.HeaderBar()
        header.set_title_widget(self._create_title_widget())

        # About button
        about_button = Gtk.Button(icon_name="help-about-symbolic")
        about_button.set_tooltip_text("About")
        about_button.connect("clicked", self._on_about_clicked)
        header.pack_end(about_button)

        # Cross-project prompt search (8.5) — search your prompts across all projects.
        prompt_search_button = Gtk.Button(icon_name="system-search-symbolic")
        prompt_search_button.set_tooltip_text("Search your prompts across all projects")
        prompt_search_button.connect("clicked", self._on_prompt_search_clicked)
        header.pack_end(prompt_search_button)

        # Refresh button (git status: behind / PR / issue counts) — git icon.
        self.refresh_button = self._icon_button(
            "git", "view-refresh-symbolic", "Refresh git status (fetch, PRs, issues)"
        )
        self.refresh_button.connect("clicked", self._on_refresh_clicked)
        header.pack_start(self.refresh_button)

        # Sync button (Claude history & memory across machines) — claude icon.
        self.sync_button = self._icon_button(
            "claude",
            "emblem-synchronizing-symbolic",
            "Sync Claude history & memory across machines",
        )
        self.sync_button.connect("clicked", self._on_sync_clicked)
        header.pack_start(self.sync_button)

        # Sync options menu (configure / backup mode / restore).
        self.sync_menu_button = Gtk.MenuButton(icon_name="view-more-symbolic")
        self.sync_menu_button.set_tooltip_text("Sync options")
        self.sync_menu_button.set_menu_model(self._build_sync_menu())
        header.pack_start(self.sync_menu_button)

        # Orphan-session affordance: live Claude sessions with no project in the
        # list (hidden unless there are any; populated by _refresh_live_indicators).
        self.orphan_button = Gtk.MenuButton(label="")
        self.orphan_button.set_tooltip_text("Background Claude sessions not in your list")
        self.orphan_button.add_css_class("cc-orphan-btn")
        self.orphan_button.set_visible(False)
        header.pack_end(self.orphan_button)

        main_box.append(header)

        # Content
        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content_box.set_margin_start(16)
        content_box.set_margin_end(16)
        content_box.set_margin_top(16)
        content_box.set_margin_bottom(16)
        content_box.set_spacing(16)

        # Title row: "Projects" + spinner + "Updated <relative>" label
        title_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

        title_label = Gtk.Label(label="Projects")
        title_label.add_css_class("title-1")
        title_label.set_xalign(0)
        title_label.set_hexpand(True)
        title_row.append(title_label)

        self.refresh_spinner = Gtk.Spinner()
        self.refresh_spinner.set_valign(Gtk.Align.CENTER)
        title_row.append(self.refresh_spinner)

        self.updated_label = Gtk.Label()
        self.updated_label.add_css_class("dim-label")
        self.updated_label.set_valign(Gtk.Align.CENTER)
        title_row.append(self.updated_label)

        content_box.append(title_row)

        # Search box to filter projects by name or path
        self.search_entry = Gtk.SearchEntry()
        self.search_entry.set_placeholder_text("Search projects...")
        self.search_entry.set_key_capture_widget(None)  # avoid global key capture
        self.search_entry.connect("search-changed", self._on_search_changed)
        content_box.append(self.search_entry)

        # Project list in scrolled window
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        self.project_list = Gtk.ListBox()
        self.project_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.project_list.add_css_class("boxed-list")
        self.project_list.set_filter_func(self._filter_row)
        # Group live-session projects on top ("Working"), the rest MRU below.
        self.project_list.set_sort_func(self._sort_rows)
        self.project_list.set_header_func(self._list_header)

        scrolled.set_child(self.project_list)
        content_box.append(scrolled)

        # Double-click gesture for opening projects
        click_gesture = Gtk.GestureClick()
        click_gesture.set_button(1)  # Left mouse button
        click_gesture.connect("released", self._on_list_double_click)
        self.project_list.add_controller(click_gesture)

        # Buttons row (Remove now lives in each card's ⋮ menu).
        buttons_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)

        # New project button (creates a git repo in a chosen folder)
        new_button = Gtk.Button(label="New Project...")
        new_button.connect("clicked", self._on_new_project_clicked)
        buttons_box.append(new_button)

        # Clone button (clone a remote repo into a chosen folder)
        clone_button = Gtk.Button(label="Clone...")
        clone_button.connect("clicked", self._on_clone_clicked)
        buttons_box.append(clone_button)

        # Add project button
        add_button = Gtk.Button(label="Add Project...")
        add_button.add_css_class("suggested-action")
        add_button.set_hexpand(True)
        add_button.connect("clicked", self._on_add_project_clicked)
        buttons_box.append(add_button)

        content_box.append(buttons_box)

        main_box.append(content_box)
        self.set_content(main_box)

        # Keep the live-session dots fresh while the manager is open.
        GLib.timeout_add_seconds(4, self._refresh_live_indicators)

        # Inter-project message state: per-remote canonical-remote cache + the
        # persisted "seen" store (so restarts don't re-notify). A first run with no
        # store bootstraps silently (marks everything seen without notifying).
        self._msg_remote_cache: dict[str, str | None] = {}
        self._msg_bootstrapped = self._msg_seen_path().exists()
        self._msg_seen = self._load_msg_seen()
        GLib.timeout_add_seconds(10, self._scan_messages)

    def _load_projects(self):
        """Load projects from registry and kick off a background status scan."""
        entries = self.registry.get_projects()

        # Clear existing
        self.project_list.remove_all()
        self._rows_by_path = {}

        if not entries:
            self._show_empty_state()
            self._update_latest_refresh_label()
            return

        # Row order is handled by the ListBox sort func (MRU; missing sink to the
        # bottom), so append order here doesn't matter — just stamp each row with
        # its open time and let the sort func place it.
        all_paths = [Path(e["path"]) for e in entries]
        epoch_by_path = {
            str(Path(e["path"]).resolve()): ProjectRegistry.last_opened_epoch(e)
            for e in entries
        }
        present = [p for p in all_paths if p.exists()]
        present_set = set(present)
        for idx, path in enumerate(all_paths):
            row = self._create_project_row(path)
            row.last_opened = epoch_by_path.get(str(path.resolve()), 0.0)
            row._wt_order = idx  # worktrees keep registration (creation) order
            self.project_list.append(row)
            self._rows_by_path[str(path.resolve())] = row
            # Render any cached network status immediately (survives reopen).
            if path in present_set:
                cached = self.status_service.get_cached_remote(str(path))
                if cached:
                    self._render_remote_badges(row, cached)

        self._recompute_units()
        self.project_list.invalidate_sort()
        self.project_list.invalidate_headers()
        self._update_latest_refresh_label()
        self._start_local_scan(present)
        self._refresh_live_indicators()
        if hasattr(self, "_msg_seen"):  # guard: _load_projects may run before init finishes
            self._scan_messages()

    def _recompute_units(self):
        """Aggregate live/attention/MRU per project *unit* (a parent + its worktrees).

        A worktree clusters under its parent, and the whole unit floats up together
        when any member is live. Sets ``unit_*`` on every row; call before a re-sort.
        """
        rows = getattr(self, "_rows_by_path", {})
        units: dict[str, list] = {}
        for row in rows.values():
            units.setdefault(getattr(row, "_parent_path", row.project_path), []).append(row)

        any_working = False
        for parent_path, unit_rows in units.items():
            working = any(getattr(r, "is_live", False) for r in unit_rows)
            attention = any(getattr(r, "is_attention", False) for r in unit_rows)
            mru = max((getattr(r, "last_opened", 0.0) for r in unit_rows), default=0.0)
            parent_row = rows.get(parent_path)
            missing = getattr(parent_row, "is_missing", False) if parent_row else False
            any_working = any_working or working
            for r in unit_rows:
                r.unit_working = working
                r.unit_attention = attention
                r.unit_mru = mru
                r.unit_missing = missing
        self._any_working = any_working

    @staticmethod
    def _sort_key(row):
        """Units (parent + worktrees) sort together: working first, attention within,
        then MRU; a parent sits above its worktrees; missing units sink."""
        return (
            not getattr(row, "unit_working", False),     # working unit → top group
            getattr(row, "unit_missing", False),          # missing → bottom
            not getattr(row, "unit_attention", False),    # "needs you" unit higher
            -getattr(row, "unit_mru", 0.0),               # MRU across units
            getattr(row, "_parent_path", ""),             # keep a unit contiguous
            getattr(row, "_is_wt", False),                # parent above its worktrees
            getattr(row, "_wt_order", 0),                 # worktrees in creation order
        )

    @classmethod
    def _sort_rows(cls, row_a, row_b) -> int:
        key_a, key_b = cls._sort_key(row_a), cls._sort_key(row_b)
        return (key_a > key_b) - (key_a < key_b)

    def _list_header(self, row, before):
        """Section headers: 'Working' (units with a live session) over 'All projects'."""
        if not getattr(self, "_any_working", False):
            row.set_header(None)  # nothing running → a plain flat list
            return
        working = getattr(row, "unit_working", False)
        if before is None or getattr(before, "unit_working", False) != working:
            row.set_header(self._section_header("Working" if working else "All projects"))
        else:
            row.set_header(None)

    def _apply_worktree_ready_badge(self, row):
        """Show "⑂ N ready" on a parent card when its worktrees have completion reports."""
        if getattr(row, "_is_wt", False):
            return
        count = worktree_reports.count_reports(row.project_path)
        badge = getattr(row, "_wt_ready_badge", None)
        if count > 0:
            text = f"⑂ {count} ready"
            if badge is None:
                badge = self._make_badge(
                    "cc-badge-worktree", "Worktrees ready to merge", text=text)
                row.badges_box.append(badge)
                row._wt_ready_badge = badge
            else:
                label = badge.get_last_child()
                if isinstance(label, Gtk.Label):
                    label.set_text(text)
        elif badge is not None:
            row.badges_box.remove(badge)
            row._wt_ready_badge = None

    @staticmethod
    def _section_header(text: str) -> Gtk.Label:
        label = Gtk.Label(label=text, xalign=0)
        label.add_css_class("dim-label")
        label.add_css_class("heading")
        label.set_margin_top(10)
        label.set_margin_bottom(2)
        label.set_margin_start(6)
        return label

    def _on_active_changed(self, *_args):
        """When the manager regains focus, re-read open times and re-sort in place."""
        if not self.get_property("is-active"):
            return
        rows = getattr(self, "_rows_by_path", None)
        if not rows:
            return
        for entry in self.registry.get_projects():
            row = rows.get(str(Path(entry["path"]).resolve()))
            if row is not None:
                row.last_opened = ProjectRegistry.last_opened_epoch(entry)
        self._recompute_units()
        self.project_list.invalidate_sort()
        self.project_list.invalidate_headers()

    def _refresh_live_indicators(self):
        """Reflect running tmux sessions on the cards (dot + kill action) and
        surface orphan sessions in the header.

        Also used as a recurring GLib timeout, so it always returns True.
        """
        live = claude_session.live_session_names()
        markers = session_notify.read_markers()
        known = set()
        working: list[tuple[str, bool]] = []  # (path, attention) — grouping signature
        for row in getattr(self, "_rows_by_path", {}).values():
            name = claude_session.session_name(row.project_path)
            known.add(name)
            is_live = name in live
            marker = markers.get(name)
            row.is_live = is_live
            row.is_attention = bool(is_live and marker)
            if is_live:
                working.append((row.project_path, row.is_attention))
            indicator = getattr(row, "live_indicator", None)
            if indicator is not None:
                indicator.set_visible(is_live)
                self._apply_indicator_state(indicator, is_live, marker)
            kill_btn = getattr(row, "kill_session_btn", None)
            if kill_btn is not None:
                kill_btn.set_sensitive(is_live)
            self._apply_worktree_ready_badge(row)
        # Re-group only when the working set/attention actually changed, so a
        # blinking dot never reshuffles rows mid-glance.
        sig = frozenset(working)
        if sig != getattr(self, "_working_sig", None):
            self._working_sig = sig
            self._recompute_units()  # roll live state up to units, then re-group
            self.project_list.invalidate_sort()
            self.project_list.invalidate_headers()
        # Desktop-notify once per fresh marker on a live session (incl. orphans).
        self._process_notifications(markers, live)
        # Drop stale markers whose session is no longer running.
        for name in markers:
            if name not in live:
                session_notify.clear_marker(name)
        # Orphans: live cc-* sessions with no registered project.
        self._orphan_sessions = sorted(live - known)
        self._update_orphan_affordance()
        return True

    @staticmethod
    def _apply_indicator_state(indicator, is_live: bool, marker: dict | None):
        """Green = live, amber = live + needs attention (has a marker)."""
        indicator.remove_css_class("cc-live-dot")
        indicator.remove_css_class("cc-live-dot-attention")
        if is_live and marker:
            indicator.add_css_class("cc-live-dot-attention")
            indicator.set_tooltip_text(marker.get("message") or "Claude needs your attention")
        else:
            indicator.add_css_class("cc-live-dot")
            indicator.set_tooltip_text("Claude session running")

    def _process_notifications(self, markers: dict, live: set):
        """Raise a desktop notification once per newly-seen marker (live only)."""
        seen = getattr(self, "_notified", None)
        if seen is None:
            seen = self._notified = set()
        for name, marker in markers.items():
            if name not in live:
                continue
            key = (name, marker.get("_mtime"))
            if key in seen:
                continue
            seen.add(key)
            app = self.get_application()
            if app is None:
                continue
            cwd = marker.get("cwd") or ""
            label = Path(cwd).name if cwd else "Claude"
            notification = Gio.Notification.new(f"Claude · {label}")
            notification.set_body(marker.get("message") or "Claude needs your attention")
            app.send_notification(None, notification)

    def _update_orphan_affordance(self):
        """Show/hide the header 'N background session(s)' button.

        Only rebuilds the popover when the orphan set actually changes, so an
        open popover isn't torn down by the 4s poll.
        """
        button = getattr(self, "orphan_button", None)
        if button is None:
            return
        orphans = getattr(self, "_orphan_sessions", [])
        if orphans == getattr(self, "_orphan_rendered", None):
            return
        self._orphan_rendered = list(orphans)
        button.set_visible(bool(orphans))
        if orphans:
            button.set_label(f"⚠ {len(orphans)} background")
            button.set_popover(self._build_orphan_popover(orphans))

    def _build_orphan_popover(self, orphans: list[str]) -> Gtk.Popover:
        """Popover listing orphan sessions (path + Kill) — sessions with no card."""
        popover = Gtk.Popover()
        popover.set_position(Gtk.PositionType.BOTTOM)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_margin_top(8)
        box.set_margin_bottom(8)
        box.set_margin_start(8)
        box.set_margin_end(8)

        heading = Gtk.Label(label="Claude sessions with no project in your list:")
        heading.add_css_class("dim-label")
        heading.set_xalign(0)
        box.append(heading)

        for name in orphans:
            path = claude_session.session_cwd(name) or "(unknown path)"
            line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            lbl = Gtk.Label(label=path)
            lbl.set_xalign(0)
            lbl.set_hexpand(True)
            lbl.set_ellipsize(3)
            line.append(lbl)
            kill = Gtk.Button(label="Kill")
            kill.add_css_class("destructive-action")
            kill.connect(
                "clicked",
                lambda _b, n=name: (
                    popover.popdown(),
                    claude_session.kill_session(n),
                    self._refresh_live_indicators(),
                ),
            )
            line.append(kill)
            box.append(line)

        popover.set_child(box)
        return popover

    def _show_empty_state(self):
        """Show empty state message."""
        label = Gtk.Label(label="No projects yet.\nClick 'Add Project' to get started.")
        label.add_css_class("dim-label")
        label.set_margin_top(48)
        label.set_margin_bottom(48)
        self.project_list.append(label)

    def _create_project_row(self, path: Path) -> Gtk.ListBoxRow:
        """Create a larger, custom project card row."""
        row = Gtk.ListBoxRow()
        row.add_css_class("project-card")
        row.project_path = str(path)
        row.is_missing = not path.exists()
        row.is_live = False       # updated by _refresh_live_indicators (grouping)
        row.is_attention = False
        if row.is_missing:
            row.add_css_class("dim-label")  # dim the whole card

        # Worktree awareness (Stage 4): a linked worktree clusters + indents under
        # its parent project. Both are static — a worktree stays a worktree.
        row._is_wt = is_linked_worktree(path)
        if row._is_wt:
            parent = worktree_parent_root(path)
            row._parent_path = str(parent.resolve()) if parent else str(path.resolve())
            row.add_css_class("cc-worktree-child")
        else:
            row._parent_path = str(path.resolve())

        # Card is two rows: a header (identity + actions) over a git-status row.
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        outer.set_margin_top(10)
        outer.set_margin_bottom(10)
        outer.set_margin_start(38 if row._is_wt else 14)  # indent worktree cards
        outer.set_margin_end(10)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)

        # Folder icon (larger) — a warning glyph when the folder is gone.
        icon = Gtk.Image.new_from_icon_name(
            "dialog-warning-symbolic" if row.is_missing else "folder-symbolic"
        )
        icon.set_pixel_size(32)
        icon.set_valign(Gtk.Align.CENTER)
        header.append(icon)

        # Name + path
        text_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        text_box.set_valign(Gtk.Align.CENTER)
        text_box.set_hexpand(True)

        name_label = Gtk.Label(label=self.registry.get_name(str(path)))
        name_label.set_xalign(0)
        name_label.add_css_class("project-card-title")
        name_label.set_ellipsize(3)  # PANGO_ELLIPSIZE_END
        text_box.append(name_label)
        row.name_label = name_label

        path_label = Gtk.Label(label=str(path))
        path_label.set_xalign(0)
        path_label.add_css_class("dim-label")
        path_label.add_css_class("project-card-path")
        path_label.set_ellipsize(3)
        text_box.append(path_label)

        header.append(text_box)

        # Live-session indicator — a green dot shown when a tmux Claude session
        # is running for this project (toggled by _refresh_live_indicators).
        live_indicator = Gtk.Image.new_from_icon_name("media-record-symbolic")
        live_indicator.add_css_class("cc-live-dot")
        live_indicator.set_valign(Gtk.Align.CENTER)
        live_indicator.set_tooltip_text("Claude session running")
        live_indicator.set_visible(False)
        header.append(live_indicator)
        row.live_indicator = live_indicator

        # Session summary button — shown only when a summary exists for this project.
        summary_button = Gtk.Button(icon_name="cc-file-symbolic")
        summary_button.add_css_class("flat")
        summary_button.set_valign(Gtk.Align.CENTER)
        summary_button.set_tooltip_text("Last session summary")
        summary_button.connect("clicked", self._on_summary_clicked, str(path))
        header.append(summary_button)
        row.summary_button = summary_button
        pid = self._cached_project_id(str(path))
        summary_button.set_visible(
            session_summary_service.load(str(path), project_id=pid) is not None
        )

        # Overflow menu (⋮): rename / remove and future per-project actions.
        menu_button = Gtk.MenuButton(icon_name="view-more-symbolic")
        menu_button.add_css_class("flat")
        menu_button.set_valign(Gtk.Align.CENTER)
        menu_button.set_tooltip_text("More actions")
        menu_button.set_popover(self._build_card_menu(row))
        header.append(menu_button)

        outer.append(header)

        # Badge container (git status markers), one row below, aligned under the
        # text (past the 32px icon + 14px spacing).
        badges = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        badges.set_margin_start(46)
        row.badges_box = badges
        row.local_badges = None
        row.remote_badges = None
        row.sync_badges = None
        row.message_badges = None
        if row.is_missing:
            badges.append(
                self._make_badge("cc-badge-missing", "Folder not found on disk",
                                 text="not found")
            )
        outer.append(badges)

        row.set_child(outer)
        return row

    def _build_card_menu(self, row: Gtk.ListBoxRow) -> Gtk.Popover:
        """Build the ⋮ overflow popover for a project card (rename / remove)."""
        popover = Gtk.Popover()
        popover.set_position(Gtk.PositionType.BOTTOM)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        box.set_margin_top(4)
        box.set_margin_bottom(4)
        box.set_margin_start(4)
        box.set_margin_end(4)

        rename_btn = Gtk.Button()
        rename_btn.add_css_class("flat")
        rename_btn.set_child(self._menu_row("document-edit-symbolic", "Rename…"))
        # The custom child clears the button's accessible name; restore it so
        # screen readers (and the GUI test harness) can identify the action.
        rename_btn.update_property([Gtk.AccessibleProperty.LABEL], ["Rename project"])
        rename_btn.connect(
            "clicked",
            lambda _b: (popover.popdown(), self._on_rename_clicked(_b, row)),
        )
        box.append(rename_btn)

        # Kill the running Claude session — only sensitive while one is live
        # (toggled in _refresh_live_indicators).
        kill_btn = Gtk.Button()
        kill_btn.add_css_class("flat")
        kill_btn.set_child(self._menu_row("process-stop-symbolic", "Kill session"))
        kill_btn.update_property([Gtk.AccessibleProperty.LABEL], ["Kill Claude session"])
        kill_btn.set_sensitive(False)
        kill_btn.connect(
            "clicked",
            lambda _b: (popover.popdown(), self._on_kill_session(row)),
        )
        box.append(kill_btn)
        row.kill_session_btn = kill_btn

        if is_linked_worktree(row.project_path):
            # A worktree card: merge its branch back, or remove the worktree.
            merge_btn = Gtk.Button()
            merge_btn.add_css_class("flat")
            merge_btn.set_child(self._menu_row("object-select-symbolic", "Merge back…"))
            merge_btn.update_property([Gtk.AccessibleProperty.LABEL], ["Merge worktree back"])
            merge_btn.connect(
                "clicked",
                lambda _b: (popover.popdown(), self._on_merge_back(row)),
            )
            box.append(merge_btn)

            remove_wt_btn = Gtk.Button()
            remove_wt_btn.add_css_class("flat")
            remove_wt_btn.set_child(self._menu_row("user-trash-symbolic", "Remove Worktree…"))
            remove_wt_btn.update_property([Gtk.AccessibleProperty.LABEL], ["Remove worktree"])
            remove_wt_btn.connect(
                "clicked",
                lambda _b: (popover.popdown(), self._on_remove_worktree(row)),
            )
            box.append(remove_wt_btn)
        else:
            # A regular project: spin off a worktree, or drop it from the list.
            new_wt_btn = Gtk.Button()
            new_wt_btn.add_css_class("flat")
            new_wt_btn.set_child(self._menu_row("list-add-symbolic", "New Worktree…"))
            new_wt_btn.update_property([Gtk.AccessibleProperty.LABEL], ["New worktree"])
            new_wt_btn.connect(
                "clicked",
                lambda _b: (popover.popdown(), self._on_new_worktree(row)),
            )
            box.append(new_wt_btn)

            remove_btn = Gtk.Button()
            remove_btn.add_css_class("flat")
            remove_btn.set_child(self._menu_row("user-trash-symbolic", "Remove…"))
            remove_btn.update_property([Gtk.AccessibleProperty.LABEL], ["Remove project"])
            remove_btn.connect(
                "clicked",
                lambda _b: (popover.popdown(), self._on_remove_card(row)),
            )
            box.append(remove_btn)

        popover.set_child(box)
        return popover

    def _on_kill_session(self, row: Gtk.ListBoxRow):
        """Confirm, then kill the project's running Claude tmux session."""
        name = claude_session.session_name(row.project_path)
        label = row.name_label.get_text() if hasattr(row, "name_label") else row.project_path
        dialog = Adw.AlertDialog(
            heading="Kill Claude session",
            body=f"Stop the running Claude session for “{label}”?\n\n"
            "Its conversation is saved to disk (resumable), but the live process "
            "and its in-memory context end.",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("kill", "Kill session")
        dialog.set_response_appearance("kill", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", self._on_kill_session_response, name)
        dialog.present(self)

    def _on_kill_session_response(self, _dialog, response: str, name: str):
        if response == "kill":
            claude_session.kill_session(name)
            self._refresh_live_indicators()

    @staticmethod
    def _menu_row(icon_name: str, label: str) -> Gtk.Box:
        """A left-aligned icon+label pair for a flat menu button."""
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        box.append(Gtk.Image.new_from_icon_name(icon_name))
        lbl = Gtk.Label(label=label)
        lbl.set_xalign(0)
        lbl.set_hexpand(True)
        box.append(lbl)
        return box

    def _cached_project_id(self, resolved_or_path: str) -> str | None:
        """The project_id from the sync status cache, if any (no git call)."""
        cached = self.sync_service.get_cached_status(
            str(Path(resolved_or_path).resolve())
        )
        return cached.project_id if cached and cached.project_id else None

    def _on_summary_clicked(self, button: Gtk.Button, project_path: str) -> None:
        """Open a popover showing the project's last session summary."""
        pid = self._cached_project_id(project_path)
        summary = session_summary_service.load(project_path, project_id=pid)
        if summary is None:
            return

        popover = Gtk.Popover()
        popover.set_parent(button)
        popover.set_position(Gtk.PositionType.BOTTOM)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_top(12)
        box.set_margin_bottom(12)
        box.set_margin_start(12)
        box.set_margin_end(12)

        when = humanize_relative_iso(summary["updated"]) if summary["updated"] else ""
        header = "Last session" + (f" · {when}" if when else "")
        header_label = Gtk.Label()
        header_label.set_markup(f"<small>{GLib.markup_escape_text(header)}</small>")
        header_label.set_xalign(0)
        header_label.add_css_class("dim-label")
        box.append(header_label)

        if summary["title"]:
            title_label = Gtk.Label()
            title_label.set_markup(
                f"<b>{GLib.markup_escape_text(summary['title'])}</b>"
            )
            title_label.set_xalign(0)
            box.append(title_label)

        body_label = Gtk.Label()
        body_label.set_markup(markdown_to_pango(summary["content"]))
        body_label.set_xalign(0)
        body_label.set_wrap(True)
        body_label.set_selectable(True)
        # Don't take popup focus, so the text isn't auto-selected on open (mouse
        # drag-selection for copying still works).
        body_label.set_focusable(False)
        body_label.set_max_width_chars(60)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_max_content_height(400)
        scroller.set_propagate_natural_height(True)
        scroller.set_propagate_natural_width(True)
        scroller.set_child(body_label)
        box.append(scroller)

        popover.set_child(box)
        popover.connect("closed", lambda p: p.unparent())
        popover.popup()

    # ------------------------------------------------------------------
    # Status badges
    # ------------------------------------------------------------------
    @staticmethod
    def _make_badge(
        css_class: str, tooltip: str, text: str = "", icon_name: str = ""
    ) -> Gtk.Widget:
        """Create a small pill badge with an optional icon and/or text."""
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=3)
        box.add_css_class("cc-badge")
        box.add_css_class(css_class)
        box.set_tooltip_text(tooltip)
        if icon_name:
            box.append(Gtk.Image.new_from_icon_name(icon_name))
        if text:
            box.append(Gtk.Label(label=text))
        return box

    def _render_local_badges(self, row, status: LocalStatus):
        """(Re)render the offline markers segment of a row's badge box."""
        # Remove previously rendered local badges.
        for badge in row.local_badges or []:
            row.badges_box.remove(badge)

        badges: list[Gtk.Widget] = []
        if not status.has_repo:
            badges.append(
                self._make_badge("cc-badge-norepo", "Not a git repository", text="No Git")
            )
        else:
            # Primary repo-state pill: green "Git".
            badges.append(
                self._make_badge("cc-badge-git", "Git repository", text="Git")
            )
            if status.dirty:
                badges.append(
                    self._make_badge(
                        "cc-badge-dirty",
                        "Uncommitted changes",
                        icon_name="dialog-warning-symbolic",
                    )
                )
            if status.ahead:
                badges.append(
                    self._make_badge(
                        "cc-badge-ahead",
                        f"{status.ahead} commit(s) to push",
                        text=f"↑{status.ahead}",
                    )
                )
            if not status.has_remote:
                badges.append(
                    self._make_badge("cc-badge-local", "No remote configured", text="local")
                )

        # Local badges go before remote ones (prepend in order).
        insert_after = None
        for badge in badges:
            row.badges_box.insert_child_after(badge, insert_after)
            insert_after = badge
        row.local_badges = badges

    def _render_remote_badges(self, row, status: RemoteStatus):
        """(Re)render the network markers segment of a row's badge box."""
        for badge in row.remote_badges or []:
            row.badges_box.remove(badge)

        badges: list[Gtk.Widget] = []
        if status.behind:
            badges.append(
                self._make_badge(
                    "cc-badge-behind",
                    f"{status.behind} commit(s) to pull",
                    text=f"↓{status.behind}",
                )
            )
        if status.pr_count:
            badges.append(
                self._make_badge(
                    "cc-badge-pr",
                    f"{status.pr_count} open pull request(s)",
                    text=f"PR {status.pr_count}",
                )
            )
        if status.issue_count:
            # No portable "flag" symbolic icon across themes; the ⚑ glyph renders
            # reliably and reads as a flag.
            badges.append(
                self._make_badge(
                    "cc-badge-issue",
                    f"{status.issue_count} open issue(s)",
                    text=f"⚑ {status.issue_count}",
                )
            )

        # Remote badges are appended at the end of the box.
        for badge in badges:
            row.badges_box.append(badge)
        row.remote_badges = badges

    # ------------------------------------------------------------------
    # Inter-project messages (badge + desktop notifications)
    # ------------------------------------------------------------------
    def _render_message_badges(self, row, count: int):
        """(Re)render the pending-messages badge on a row."""
        for badge in getattr(row, "message_badges", None) or []:
            row.badges_box.remove(badge)
        badges: list[Gtk.Widget] = []
        if count:
            badges.append(
                self._make_badge(
                    "cc-badge-message",
                    f"{count} pending message(s)",
                    text=f"✉ {count}",
                )
            )
        for badge in badges:
            row.badges_box.append(badge)
        row.message_badges = badges

    def _msg_seen_path(self) -> Path:
        return get_config_dir() / "messages-seen.json"

    def _load_msg_seen(self) -> dict:
        try:
            return json.loads(self._msg_seen_path().read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _save_msg_seen(self):
        try:
            atomic_write_text(self._msg_seen_path(), json.dumps(self._msg_seen, indent=2))
        except OSError:
            pass

    def _scan_messages(self):
        """Recompute per-project pending counts and detect new inbound messages.

        Also serves as a recurring GLib timeout, so it always returns True. The
        canonical-remote resolution (one git call per uncached project) and the
        message-store read run off the main thread.
        """
        rows = dict(getattr(self, "_rows_by_path", {}))
        if not rows:
            return True
        paths = list(rows.keys())
        cache = self._msg_remote_cache

        def worker():
            remotes = {}
            for p in paths:
                if p in cache:
                    remotes[p] = cache[p]
                else:
                    ident = resolve_project_identity(p)
                    remotes[p] = ident.canonical_remote if ident else None
            threads = message_store.list_threads()
            GLib.idle_add(self._apply_message_scan, remotes, threads)

        threading.Thread(target=worker, daemon=True).start()
        return True

    def _apply_message_scan(self, remotes: dict, threads: list):
        """Main thread: render badges and fire notifications for new inbound messages."""
        self._msg_remote_cache.update(remotes)
        active = {r for r in remotes.values() if r}
        pending, inbound = message_store.scan_activity(threads, active)

        for path, row in self._rows_by_path.items():
            remote = self._msg_remote_cache.get(path)
            self._render_message_badges(row, pending.get(remote, 0) if remote else 0)

        self._notify_new_messages(inbound)
        return False

    def _notify_new_messages(self, inbound: dict):
        """Desktop-notify once per new inbound message; advance the persisted seen store.

        A first run with no seen store bootstraps silently: it records the current
        newest timestamps without notifying, so existing history isn't announced.
        """
        bootstrapping = not self._msg_bootstrapped
        app = self.get_application()
        changed = False
        for remote, ts_list in inbound.items():
            if not ts_list:
                continue
            newest = max(ts_list)
            last_seen = self._msg_seen.get(remote, "")
            new = [ts for ts in ts_list if ts > last_seen]
            if new and newest != last_seen:
                self._msg_seen[remote] = newest
                changed = True
                if not bootstrapping and app is not None:
                    name = self._name_for_remote(remote)
                    notification = Gio.Notification.new(f"Messages · {name}")
                    notification.set_body(f"{len(new)} new message(s)")
                    app.send_notification(None, notification)
        if bootstrapping:
            self._msg_bootstrapped = True
        if changed:
            self._save_msg_seen()

    def _name_for_remote(self, remote: str) -> str:
        """Display name of the registered project that owns ``remote`` (folder fallback)."""
        for path, cached in self._msg_remote_cache.items():
            if cached == remote:
                return self.registry.get_name(path)
        parts = remote.split("/")
        return parts[-1] if parts else remote

    # ------------------------------------------------------------------
    # Background local scan
    # ------------------------------------------------------------------
    def _start_local_scan(self, paths: list[Path]):
        """Compute local git status for each project in a background thread."""
        def worker():
            for path in paths:
                status = self.status_service.get_local_status(str(path))
                GLib.idle_add(self._apply_local_status, str(path.resolve()), status)

        threading.Thread(target=worker, daemon=True).start()

    def _apply_local_status(self, resolved_path: str, status: LocalStatus):
        """Main-thread callback: render local badges for a scanned project."""
        row = self._rows_by_path.get(resolved_path)
        if row is not None:
            self._render_local_badges(row, status)
            # Paint the last known sync status instantly (before any Sync run).
            cached = self.sync_service.get_cached_status(resolved_path)
            if cached is not None:
                self._render_sync_badges(row, cached)
        return False

    # ------------------------------------------------------------------
    # Network refresh (fetch + PR/issue counts)
    # ------------------------------------------------------------------
    def _on_refresh_clicked(self, _button, credentials: tuple[str, str] | None = None):
        """Refresh network status for all GitHub-capable projects."""
        if self._refreshing:
            return
        paths = list(self._rows_by_path.keys())
        if not paths:
            return

        self._refreshing = True
        self.refresh_button.set_sensitive(False)
        self.refresh_spinner.start()

        def worker():
            for resolved_path in paths:
                try:
                    status = self.status_service.refresh_remote_status(
                        resolved_path, credentials=credentials
                    )
                    GLib.idle_add(self._apply_remote_status, resolved_path, status)
                except AuthenticationRequired as exc:
                    GLib.idle_add(self._on_refresh_auth_required, exc.remote_url)
                    return
                except GitHubError:
                    # Non-fatal (network/API); skip this project's network badges.
                    continue
            GLib.idle_add(self._on_refresh_done)

        threading.Thread(target=worker, daemon=True).start()

    def _apply_remote_status(self, resolved_path: str, status: RemoteStatus):
        row = self._rows_by_path.get(resolved_path)
        if row is not None:
            self._render_remote_badges(row, status)
        return False

    def _on_refresh_auth_required(self, remote_url: str):
        """Prompt for GitHub credentials, then retry the whole refresh."""
        self._on_refresh_done()
        show_github_credentials_dialog(self, remote_url, self._retry_refresh_with_credentials)
        return False

    def _retry_refresh_with_credentials(self, credentials: tuple[str, str]):
        self._on_refresh_clicked(None, credentials=credentials)

    def _on_refresh_done(self):
        self._refreshing = False
        self.refresh_button.set_sensitive(True)
        self.refresh_spinner.stop()
        self._update_latest_refresh_label()
        return False

    # ------------------------------------------------------------------
    # Cross-machine sync
    # ------------------------------------------------------------------
    def _render_sync_badges(self, row, status: ProjectSyncStatus):
        """(Re)render the single sync-state badge on a row (appended last)."""
        for badge in getattr(row, "sync_badges", None) or []:
            row.badges_box.remove(badge)

        badge = None
        state = status.state
        if state in (SyncState.SYNCED, SyncState.AHEAD, SyncState.BEHIND):
            # A push/pull during a successful bidirectional sync already
            # reconciled the project, so it is now in sync — show one calm badge
            # rather than a divergence-looking arrow. What actually changed this
            # run is reported in the sync summary. Text (not a themed icon) so it
            # renders regardless of the active icon theme.
            tip = status.detail or "In sync"
            badge = self._make_badge("cc-badge-synced", tip, text="✓ in sync")
        elif state == SyncState.CONFLICT:
            tip = "Sync conflict"
            if status.conflict_files:
                tip += ": " + ", ".join(status.conflict_files)
            badge = self._make_badge("cc-badge-conflict", tip, text="conflict")
        elif state == SyncState.ERROR:
            badge = self._make_badge(
                "cc-badge-conflict", status.detail or "Sync error", text="sync ✕"
            )
        elif state == SyncState.PAUSED:
            badge = self._make_badge("cc-badge-syncoff", "Sync busy on another instance", text="paused")
        # NOT_CONFIGURED / SYNCING render nothing to keep the row uncluttered.

        badges = [badge] if badge is not None else []
        for b in badges:
            row.badges_box.append(b)
        row.sync_badges = badges

    def _on_sync_clicked(self, _button, credentials: tuple[str, str] | None = None):
        """Run a bidirectional sync of all registered projects."""
        if self._syncing:
            return
        if not self.sync_service.is_configured():
            self._show_sync_config_dialog()
            return

        paths = list(self._rows_by_path.keys())
        self._syncing = True
        self.sync_button.set_sensitive(False)
        self.refresh_spinner.start()
        self.updated_label.set_text("Syncing…")

        def worker():
            def progress(status: ProjectSyncStatus):
                GLib.idle_add(self._apply_sync_status, status)

            try:
                result = self.sync_service.sync(
                    paths, credentials=credentials, progress=progress
                )
                GLib.idle_add(self._on_sync_done, result)
            except AuthenticationRequired as exc:
                GLib.idle_add(self._on_sync_auth_required, exc.remote_url)

        threading.Thread(target=worker, daemon=True).start()

    def _apply_sync_status(self, status: ProjectSyncStatus):
        row = self._rows_by_path.get(status.local_path)
        if row is not None:
            self._render_sync_badges(row, status)
        return False

    def _on_sync_auth_required(self, remote_url: str):
        """Prompt for credentials, then retry the whole sync."""
        self._syncing = False
        self.sync_button.set_sensitive(True)
        self.refresh_spinner.stop()
        show_github_credentials_dialog(self, remote_url, self._retry_sync_with_credentials)
        return False

    def _retry_sync_with_credentials(self, credentials: tuple[str, str]):
        self._on_sync_clicked(None, credentials=credentials)

    def _on_sync_done(self, result):
        self._syncing = False
        self.sync_button.set_sensitive(True)
        self.refresh_spinner.stop()
        if result.error == "busy":
            self.updated_label.set_text("Sync busy on another instance")
        elif result.error:
            self.updated_label.set_text("Sync failed")
        else:
            states = [s.state for s in result.per_project.values()]
            pushed = sum(1 for st in states if st == SyncState.AHEAD)
            pulled = sum(1 for st in states if st == SyncState.BEHIND)
            conflicts = sum(1 for st in states if st == SyncState.CONFLICT)
            msg = f"Synced {len(states)} project(s)"
            parts = []
            if pushed:
                parts.append(f"↑{pushed} pushed")
            if pulled:
                parts.append(f"↓{pulled} pulled")
            if conflicts:
                parts.append(f"{conflicts} conflict(s)")
            if parts:
                msg += " · " + " · ".join(parts)
            self.updated_label.set_text(msg)
        return False

    def _show_sync_config_dialog(self):
        """First-run dialog to set the sync repo URL and enable sync."""
        dialog = Adw.AlertDialog()
        dialog.set_heading("Configure Sync")
        dialog.set_body(
            "Private git repository used to sync Claude history & memory "
            "between your machines:"
        )
        entry = Gtk.Entry()
        entry.set_text(self.sync_service.settings.get("sync.repo_url", ""))
        entry.set_activates_default(True)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(entry)
        dialog.set_extra_child(box)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("enable", "Enable & Sync")
        dialog.set_response_appearance("enable", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("enable")
        dialog.connect("response", self._on_sync_config_response, entry)
        dialog.present(self)

    def _on_sync_config_response(self, _dialog, response, entry):
        if response != "enable":
            return
        url = entry.get_text().strip()
        if not url:
            return
        self.sync_service.settings.set("sync.repo_url", url)
        self.sync_service.settings.set("sync.enabled", True)
        self._on_sync_clicked(None)

    @staticmethod
    def _icon_button(svg_name: str, fallback_icon: str, tooltip: str) -> Gtk.Button:
        """Header button using a Material SVG icon (falls back to a themed icon)."""
        button = Gtk.Button()
        gicon = IconCache().get_provider_gicon(svg_name)
        if gicon is not None:
            image = Gtk.Image.new_from_gicon(gicon)
            image.set_pixel_size(18)
            button.set_child(image)
        else:
            button.set_icon_name(fallback_icon)
        button.set_tooltip_text(tooltip)
        return button

    # ------------------------------------------------------------------
    # Sync options menu + backup / restore
    # ------------------------------------------------------------------
    def _build_sync_menu(self) -> Gio.Menu:
        group = Gio.SimpleActionGroup()

        configure = Gio.SimpleAction.new("configure", None)
        configure.connect("activate", lambda *_: self._show_sync_config_dialog())
        group.add_action(configure)

        backup_on = self.sync_service.settings.get("sync.mode") == "backup"
        backup = Gio.SimpleAction.new_stateful(
            "backup_mode", None, GLib.Variant.new_boolean(backup_on)
        )
        backup.connect("change-state", self._on_backup_mode_toggle)
        group.add_action(backup)

        restore = Gio.SimpleAction.new("restore", None)
        restore.connect("activate", lambda *_: self._on_restore_clicked())
        group.add_action(restore)

        self.insert_action_group("sync", group)

        menu = Gio.Menu()
        menu.append("Configure sync…", "sync.configure")
        menu.append("Backup mode (all projects + registry)", "sync.backup_mode")
        menu.append("Restore from backup…", "sync.restore")
        return menu

    def _on_backup_mode_toggle(self, action, value):
        action.set_state(value)
        self.sync_service.settings.set(
            "sync.mode", "backup" if value.get_boolean() else "selected"
        )

    def _on_restore_clicked(self):
        """List projects present in the backup but not on this machine, to clone."""
        if not self.sync_service.is_configured():
            self._show_info(
                "Sync not configured",
                "Configure sync and press Sync first, then try Restore.",
            )
            return
        restorable = self.sync_service.list_restorable(list(self._rows_by_path.keys()))
        if not restorable:
            self._show_info(
                "Nothing to restore",
                "All backed-up projects are already here. If you just configured "
                "sync, press Sync first to fetch the backup.",
            )
            return

        dialog = Adw.AlertDialog()
        dialog.set_heading("Restore from Backup")
        dialog.set_body("Select projects to clone and register on this machine:")

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        checks: list[Gtk.CheckButton] = []
        for entry in restorable:
            remote = entry.canonical_remote or entry.remote_url or "(no remote — cannot restore)"
            check = Gtk.CheckButton(label=f"{entry.name}   ·   {remote}")
            check.set_active(bool(entry.remote_url))
            check.set_sensitive(bool(entry.remote_url))
            check.entry = entry
            box.append(check)
            checks.append(check)
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_min_content_height(220)
        scrolled.set_child(box)
        dialog.set_extra_child(scrolled)

        dialog.add_response("cancel", "Cancel")
        dialog.add_response("choose", "Choose Folder & Restore")
        dialog.set_response_appearance("choose", Adw.ResponseAppearance.SUGGESTED)
        dialog.connect("response", self._on_restore_response, checks)
        dialog.present(self)

    def _on_restore_response(self, _dialog, response, checks):
        if response != "choose":
            return
        selected = [c.entry for c in checks if c.get_active() and c.entry.remote_url]
        if not selected:
            return
        self._restore_selected = selected
        file_dialog = Gtk.FileDialog()
        file_dialog.set_title("Choose a folder to clone the projects into")
        file_dialog.select_folder(self, None, self._on_restore_folder_chosen)

    def _on_restore_folder_chosen(self, dialog, result):
        try:
            folder = dialog.select_folder_finish(result)
        except GLib.Error:
            return
        base = folder.get_path()
        if base:
            self._run_restore(self._restore_selected, base)

    def _run_restore(self, entries, base, credentials=None):
        self.updated_label.set_text("Restoring…")

        def worker():
            restored = []
            for entry in entries:
                try:
                    path = self.sync_service.restore_project(
                        entry, base, credentials=credentials
                    )
                    restored.append(path)
                except AuthenticationRequired as exc:
                    GLib.idle_add(self._on_restore_auth, exc.remote_url, entries, base)
                    return
                except Exception as exc:  # noqa: BLE001 — report and continue
                    print(f"Restore failed for {entry.name}: {exc}")
            GLib.idle_add(self._on_restore_done, restored)

        threading.Thread(target=worker, daemon=True).start()

    def _on_restore_auth(self, remote_url, entries, base):
        show_github_credentials_dialog(
            self,
            remote_url,
            lambda creds: self._run_restore(entries, base, credentials=creds),
        )
        return False

    def _on_restore_done(self, restored):
        self._load_projects()  # pick up the newly registered projects
        self.updated_label.set_text(f"Restored {len(restored)} project(s)")
        if restored:
            # A follow-up sync materializes each restored project's history/memory.
            self._on_sync_clicked(None)
        return False

    def _show_info(self, heading: str, body: str):
        dialog = Adw.AlertDialog()
        dialog.set_heading(heading)
        dialog.set_body(body)
        dialog.add_response("ok", "OK")
        dialog.present(self)

    def _update_latest_refresh_label(self):
        """Show 'Updated <relative>' from the newest cached refresh timestamp."""
        latest = None
        for resolved_path in self._rows_by_path:
            cached = self.status_service.get_cached_remote(resolved_path)
            if cached and cached.refreshed_at:
                if latest is None or cached.refreshed_at > latest:
                    latest = cached.refreshed_at
        self.updated_label.set_text(f"Updated {humanize_relative(latest)}" if latest else "")

    # ------------------------------------------------------------------
    # Rename
    # ------------------------------------------------------------------
    def _on_rename_clicked(self, _button, row):
        """Show a dialog to edit the project's display label (folder unchanged)."""
        path = row.project_path
        current = self.registry.get_name(path)

        dialog = Adw.AlertDialog()
        dialog.set_heading("Rename Project")
        dialog.set_body(f"Custom label for:\n{path}")

        entry = Gtk.Entry()
        entry.set_text(current)
        entry.set_activates_default(True)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(entry)
        dialog.set_extra_child(box)

        dialog.add_response("cancel", "Cancel")
        dialog.add_response("rename", "Rename")
        dialog.set_response_appearance("rename", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("rename")
        dialog.connect("response", self._on_rename_response, row, entry)
        dialog.present(self)

    def _on_rename_response(self, _dialog, response, row, entry):
        if response != "rename":
            return
        name = entry.get_text().strip()
        self.registry.set_name(row.project_path, name)
        row.name_label.set_text(self.registry.get_name(row.project_path))

    def _on_search_changed(self, entry):
        """Re-run the list filter as the search query changes."""
        self._query = entry.get_text().strip().lower()
        self.project_list.invalidate_filter()

    def _filter_row(self, row) -> bool:
        """ListBox filter: match the query against a project's name and path."""
        if not self._query:
            return True
        path = getattr(row, "project_path", None)
        if path is None:
            return True  # non-project rows (e.g. empty state) always show
        name = row.name_label.get_text() if hasattr(row, "name_label") else ""
        return self._query in name.lower() or self._query in path.lower()

    def _on_list_double_click(self, _gesture, n_press, _x, _y):
        """Handle double-click on project list."""
        if n_press == 2:  # Double-click
            row = self.project_list.get_selected_row()
            if row and hasattr(row, "project_path"):
                self._open_project(row.project_path)

    def _on_add_project_clicked(self, _button):
        """Handle add project button click."""
        dialog = Gtk.FileDialog()
        dialog.set_title("Select Project Folder")

        # Start in home directory
        home = Gio.File.new_for_path(str(Path.home()))
        dialog.set_initial_folder(home)

        dialog.select_folder(self, None, self._on_folder_selected)

    def _on_folder_selected(self, dialog, result):
        """Handle folder selection from file dialog."""
        try:
            folder = dialog.select_folder_finish(result)
            if folder:
                path = folder.get_path()
                # Register the project
                self.registry.register_project(path)
                # Refresh the list
                self._load_projects()
                # Open the project
                self._open_project(path)
        except GLib.Error:
            # User cancelled
            pass

    def _on_remove_card(self, row: Gtk.ListBoxRow):
        """Remove a project from the list (folder untouched), with confirmation."""
        path = row.project_path
        name = row.name_label.get_text() if hasattr(row, "name_label") else path

        dialog = Adw.AlertDialog(
            heading="Remove Project",
            body=f"Remove “{name}” from the list?\n\n{path}\n\nThe folder on disk is not touched.",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("remove", "Remove")
        dialog.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", self._on_remove_card_response, path)
        dialog.present(self)

    def _on_remove_card_response(self, _dialog, response: str, path: str):
        if response == "remove":
            self.registry.unregister_project(path)
            self._load_projects()

    # ------------------------------------------------------------------
    # Worktrees (spin off / remove) — Stage 3
    # ------------------------------------------------------------------
    def _on_new_worktree(self, row: Gtk.ListBoxRow):
        """Dialog: task name -> derived branch + sibling path (both editable)."""
        parent = row.project_path
        name = row.name_label.get_text() if hasattr(row, "name_label") else Path(parent).name

        dialog = Adw.AlertDialog(
            heading="New Worktree",
            body=f"Spin off a worktree of “{name}” on its own branch.",
        )
        task_entry = Gtk.Entry(activates_default=True)
        task_entry.set_placeholder_text("Task name — e.g. login flow")
        branch_entry = Gtk.Entry()
        path_entry = Gtk.Entry()
        open_check = Gtk.CheckButton(label="Open the worktree in a window")
        open_check.set_active(True)

        # Auto-fill branch + path from the task name until the user edits them.
        state = {"branch_auto": True, "path_auto": True}

        def derive(*_):
            task = task_entry.get_text().strip()
            slug = slugify(task) if task else ""
            if state["branch_auto"]:
                branch_entry.set_text(f"feature/{slug}" if slug else "")
            if state["path_auto"]:
                base = Path(parent)
                path_entry.set_text(str(base.parent / f"{base.name}--{slug}") if slug else "")

        task_entry.connect("changed", derive)
        branch_entry.connect(
            "changed", lambda *_: branch_entry.has_focus() and state.update(branch_auto=False))
        path_entry.connect(
            "changed", lambda *_: path_entry.has_focus() and state.update(path_auto=False))

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        for label, widget in (("Task", task_entry), ("Branch", branch_entry), ("Folder", path_entry)):
            lbl = Gtk.Label(label=label, xalign=0)
            lbl.add_css_class("caption")
            lbl.add_css_class("dim-label")
            box.append(lbl)
            box.append(widget)
        box.append(open_check)
        dialog.set_extra_child(box)

        dialog.add_response("cancel", "Cancel")
        dialog.add_response("create", "Create")
        dialog.set_response_appearance("create", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("create")
        dialog.connect("response", self._on_new_worktree_response,
                       parent, branch_entry, path_entry, open_check)
        dialog.present(self)

    def _on_new_worktree_response(self, _dialog, response, parent, branch_entry, path_entry, open_check):
        if response != "create":
            return
        branch = branch_entry.get_text().strip()
        wt_path = path_entry.get_text().strip()
        if not branch or not wt_path:
            ToastService.show("A worktree needs both a branch and a folder")
            return
        if Path(wt_path).exists():
            ToastService.show("That folder already exists — pick another")
            return
        open_after = open_check.get_active()

        def worker():
            error = None
            try:
                GitService(Path(parent)).add_worktree(wt_path, branch)
            except Exception as exc:  # noqa: BLE001 - surface any git failure
                error = str(exc)
            GLib.idle_add(self._on_worktree_created, wt_path, error, open_after)

        threading.Thread(target=worker, daemon=True).start()

    def _on_worktree_created(self, wt_path: str, error: str | None, open_after: bool):
        if error:
            err = Adw.AlertDialog(heading="Could Not Create Worktree", body=error)
            err.add_response("ok", "OK")
            err.present(self)
            return False
        self.registry.register_project(wt_path)
        self._load_projects()
        if open_after:
            self._open_project(wt_path)
        return False

    def _on_remove_worktree(self, row: Gtk.ListBoxRow):
        """Confirm, then `git worktree remove` (branch kept)."""
        wt = row.project_path
        name = row.name_label.get_text() if hasattr(row, "name_label") else wt
        dialog = Adw.AlertDialog(
            heading="Remove Worktree",
            body=f"Remove the worktree “{name}”?\n\n{wt}\n\nIts branch is kept.",
        )
        force_check = Gtk.CheckButton(label="Force (discard uncommitted changes)")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(force_check)
        dialog.set_extra_child(box)

        dialog.add_response("cancel", "Cancel")
        dialog.add_response("remove", "Remove")
        dialog.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", self._on_remove_worktree_response, wt, force_check)
        dialog.present(self)

    def _on_remove_worktree_response(self, _dialog, response, wt, force_check):
        if response != "remove":
            return
        force = force_check.get_active()
        parent = worktree_parent_root(wt)
        if parent is None:  # no longer a linked worktree — just drop it from the list
            self.registry.unregister_project(wt)
            self._load_projects()
            return

        def worker():
            error = None
            try:
                GitService(parent).remove_worktree(wt, force=force)
            except Exception as exc:  # noqa: BLE001
                error = str(exc)
            GLib.idle_add(self._on_worktree_removed, wt, error)

        threading.Thread(target=worker, daemon=True).start()

    def _on_worktree_removed(self, wt: str, error: str | None):
        if error:
            ToastService.show_error(f"Could not remove worktree: {error}")
            return False
        self.registry.unregister_project(wt)
        self._load_projects()
        ToastService.show("Worktree removed")
        return False

    # -- Merge back (worktree branch -> parent) -------------------------
    def _on_merge_back(self, row: Gtk.ListBoxRow):
        """Preview the merge (no working-tree touch), then merge or flag conflicts."""
        wt = row.project_path
        parent = worktree_parent_root(wt)
        if parent is None:
            ToastService.show_error("Not a linked worktree")
            return

        def worker():
            error = None
            clean = False
            conflicts: list[str] = []
            branch = ""
            try:
                branch = GitService(wt).get_branch_name()
                clean, conflicts = GitService(parent).preview_merge(branch)
            except Exception as exc:  # noqa: BLE001
                error = str(exc)
            GLib.idle_add(self._on_merge_preview, str(parent), branch, clean, conflicts, error)

        threading.Thread(target=worker, daemon=True).start()

    def _on_merge_preview(self, parent, branch, clean, conflicts, error):
        if error:
            ToastService.show_error(f"Merge preview failed: {error}")
            return False
        if not clean:
            files = "\n".join(f"  • {c}" for c in conflicts[:12]) or "  • (unknown)"
            dialog = Adw.AlertDialog(
                heading="Conflicts — resolve in the worktree",
                body=f"Merging “{branch}” into the parent branch conflicts in:\n\n{files}\n\n"
                     "Open the worktree and let its agent resolve the conflicts, then try again.",
            )
            dialog.add_response("ok", "OK")
            dialog.present(self)
            return False
        dialog = Adw.AlertDialog(
            heading="Merge back",
            body=f"Merge “{branch}” into the parent project’s current branch?",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("merge", "Merge")
        dialog.set_response_appearance("merge", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("merge")
        dialog.connect("response", self._on_merge_back_confirm, parent, branch)
        dialog.present(self)
        return False

    def _on_merge_back_confirm(self, _dialog, response, parent, branch):
        if response != "merge":
            return

        def worker():
            error = None
            try:
                GitService(parent).merge_branch(branch)
            except Exception as exc:  # noqa: BLE001
                error = str(exc)
            GLib.idle_add(self._on_merge_done, parent, branch, error)

        threading.Thread(target=worker, daemon=True).start()

    def _on_merge_done(self, parent, branch, error):
        if error:
            ToastService.show_error(f"Merge failed: {error}")
            return False
        worktree_reports.resolve_report(parent, branch)
        self._load_projects()  # refresh the "N ready" badge
        ToastService.show(f"Merged {branch} into the parent")
        return False

    # ------------------------------------------------------------------
    # New project (pick folder -> name -> git init -> register + open)
    # ------------------------------------------------------------------
    def _on_new_project_clicked(self, _button):
        """Pick a folder for a brand-new project."""
        dialog = Gtk.FileDialog()
        dialog.set_title("Choose Folder for New Project")
        dialog.set_initial_folder(Gio.File.new_for_path(str(Path.home())))
        dialog.select_folder(self, None, self._on_new_folder_selected)

    def _on_new_folder_selected(self, dialog, result):
        """Ask for a project name once a folder has been chosen."""
        try:
            folder = dialog.select_folder_finish(result)
        except GLib.Error:
            return  # cancelled
        if not folder:
            return
        path = folder.get_path()

        name_dialog = Adw.AlertDialog()
        name_dialog.set_heading("New Project")
        name_dialog.set_body(f"Initialize a git repository in:\n{path}")

        entry = Gtk.Entry()
        entry.set_text(Path(path).name)
        entry.set_activates_default(True)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.append(entry)

        # Warn if the target folder already has content (git init is still safe).
        try:
            non_empty = any(c.name != ".git" for c in Path(path).iterdir())
        except OSError:
            non_empty = False
        if non_empty:
            note = Gtk.Label(label="This folder is not empty — its files will be left as-is.")
            note.add_css_class("dim-label")
            note.add_css_class("caption")
            note.set_xalign(0)
            note.set_wrap(True)
            box.append(note)

        initial_check = Gtk.CheckButton(label="Create an initial (empty) commit")
        initial_check.set_active(not non_empty)  # unborn repo is awkward; default on when empty
        box.append(initial_check)
        name_dialog.set_extra_child(box)

        name_dialog.add_response("cancel", "Cancel")
        name_dialog.add_response("create", "Create")
        name_dialog.set_response_appearance("create", Adw.ResponseAppearance.SUGGESTED)
        name_dialog.set_default_response("create")
        name_dialog.connect("response", self._on_new_project_response, path, entry, initial_check)
        name_dialog.present(self)

    def _on_new_project_response(self, _dialog, response, path, entry, initial_check):
        if response != "create":
            return
        name = entry.get_text().strip()
        make_initial = initial_check.get_active()
        default_branch = self.sync_service.settings.get("git.default_branch", "main") or "main"

        def worker():
            error = None
            warning = None
            try:
                result = subprocess.run(
                    ["git", "init", "-b", default_branch],
                    cwd=path,
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                if result.returncode != 0:
                    error = result.stderr.strip() or "git init failed"
                elif make_initial:
                    commit = subprocess.run(
                        ["git", "commit", "--allow-empty", "-m", "Initial commit"],
                        cwd=path,
                        capture_output=True,
                        text=True,
                        timeout=15,
                    )
                    if commit.returncode != 0:
                        # Repo is created; only the optional first commit failed
                        # (usually a missing user.name/email) — surface as a warning.
                        warning = commit.stderr.strip() or "could not create the initial commit"
            except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
                error = str(exc)
            GLib.idle_add(self._on_new_project_created, path, name, error, warning)

        threading.Thread(target=worker, daemon=True).start()

    def _on_new_project_created(self, path, name, error, warning=None):
        """Main-thread callback after `git init` completes."""
        if error:
            err = Adw.AlertDialog()
            err.set_heading("Could Not Create Project")
            err.set_body(f"git init failed:\n{error}")
            err.add_response("ok", "OK")
            err.present(self)
            return False

        if warning:
            ToastService.show(f"Project created; initial commit skipped: {warning}")

        self.registry.register_project(path, name)
        self._load_projects()
        self._open_project(path)
        return False

    # ------------------------------------------------------------------
    # Clone (URL -> transient "Cloning…" card -> registered project)
    # ------------------------------------------------------------------
    def _on_clone_clicked(self, _button):
        dialog = Adw.AlertDialog()
        dialog.set_heading("Clone Repository")

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)

        url_entry = Gtk.Entry()
        url_entry.set_placeholder_text("Repository URL (https:// or git@…)")
        name_entry = Gtk.Entry()
        name_entry.set_placeholder_text("Folder name (optional — derived from URL)")

        # --- GitHub repo picker: a searchable dropdown that fills the URL below ---
        gh_label = Gtk.Label(label="From your GitHub:")
        gh_label.set_xalign(0)
        gh_label.add_css_class("dim-label")
        gh_label.add_css_class("caption")
        box.append(gh_label)

        gh_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        repo_store = Gio.ListStore(item_type=_RepoItem)
        repo_store.append(_RepoItem(label="Loading your GitHub repos…"))
        repo_dropdown = Gtk.DropDown(model=repo_store)
        repo_dropdown.set_hexpand(True)
        # Set the search expression BEFORE enabling search (the internal search filter
        # is built from the expression), and match anywhere in "owner/repo".
        repo_dropdown.set_expression(Gtk.PropertyExpression.new(_RepoItem, None, "label"))
        repo_dropdown.set_search_match_mode(Gtk.StringFilterMatchMode.SUBSTRING)
        repo_dropdown.set_enable_search(True)
        repo_dropdown.set_factory(self._build_repo_factory())
        repo_dropdown.set_sensitive(False)
        gh_row.append(repo_dropdown)
        connect_btn = Gtk.Button(label="Connect")
        connect_btn.set_visible(False)
        gh_row.append(connect_btn)
        box.append(gh_row)

        box.append(url_entry)
        box.append(name_entry)

        def on_repo_selected(dropdown, _pspec):
            item = dropdown.get_selected_item()
            if item is not None and item.clone_url:
                url_entry.set_text(item.clone_url)
                name_entry.set_text(item.name)

        repo_dropdown.connect("notify::selected", on_repo_selected)

        def _set_items(items):
            repo_store.splice(0, repo_store.get_n_items(), items)

        def apply_repos(repos):
            if repos:
                # Group by owner (contiguous), then repo name — so the list isn't
                # interleaved by update time.
                repos = sorted(
                    repos,
                    key=lambda r: (r["full_name"].split("/", 1)[0].lower(), r["name"].lower()),
                )
                items = [_RepoItem(label="— select a repo —")] + [
                    _RepoItem(label=r["full_name"], name=r["name"],
                              clone_url=r["clone_url"], private=r["private"])
                    for r in repos
                ]
                repo_dropdown.set_sensitive(True)
            else:
                items = [_RepoItem(label="No repositories found")]
                repo_dropdown.set_sensitive(False)
            _set_items(items)
            connect_btn.set_visible(False)
            return False

        def need_auth(_remote):
            _set_items([_RepoItem(label="Connect GitHub to list repos")])
            repo_dropdown.set_sensitive(False)
            connect_btn.set_visible(True)
            return False

        def load_error(msg):
            _set_items([_RepoItem(label="Couldn't load repos")])
            repo_dropdown.set_sensitive(False)
            ToastService.show_error(f"GitHub: {msg}")
            return False

        def do_load(credentials=None):
            def worker():
                try:
                    repos = github_repos.list_user_repos(credentials=credentials)
                    GLib.idle_add(apply_repos, repos)
                except AuthenticationRequired as exc:
                    GLib.idle_add(need_auth, exc.remote_url)
                except Exception as exc:  # noqa: BLE001 - surfaced as an error state
                    GLib.idle_add(load_error, str(exc))

            threading.Thread(target=worker, daemon=True).start()

        def on_connect(_b):
            def got(creds):
                try:
                    CredentialService.get_instance().store(
                        "https://github.com", creds[0], creds[1]
                    )
                except Exception:  # noqa: BLE001 - storing is best-effort
                    pass
                do_load(credentials=creds)

            show_github_credentials_dialog(self, "github.com", got)

        connect_btn.connect("clicked", on_connect)
        do_load()

        dest_holder = {"parent": None}
        dest_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        dest_label = Gtk.Label(label="No destination chosen")
        dest_label.add_css_class("dim-label")
        dest_label.set_hexpand(True)
        dest_label.set_xalign(0)
        dest_label.set_ellipsize(1)  # PANGO_ELLIPSIZE_START
        dest_row.append(dest_label)
        choose_btn = Gtk.Button(label="Choose folder…")
        choose_btn.connect("clicked", lambda _b: self._pick_clone_dest(dest_holder, dest_label))
        dest_row.append(choose_btn)
        box.append(dest_row)

        dialog.set_extra_child(box)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("clone", "Clone")
        dialog.set_response_appearance("clone", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("clone")
        dialog.connect("response", self._on_clone_response, url_entry, name_entry, dest_holder)
        dialog.present(self)

    @staticmethod
    def _build_repo_factory() -> Gtk.SignalListItemFactory:
        """Factory for the Clone repo dropdown: label + a lock icon on private repos."""
        factory = Gtk.SignalListItemFactory()

        def setup(_f, list_item):
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            label = Gtk.Label()
            label.set_xalign(0)
            label.set_hexpand(True)
            label.set_ellipsize(3)  # PANGO_ELLIPSIZE_END
            row.append(label)
            lock = Gtk.Image.new_from_icon_name("channel-secure-symbolic")
            lock.add_css_class("dim-label")
            row.append(lock)
            list_item.set_child(row)

        def bind(_f, list_item):
            repo = list_item.get_item()
            row = list_item.get_child()
            label = row.get_first_child()
            lock = label.get_next_sibling()
            label.set_label(repo.label)
            lock.set_visible(repo.private)

        factory.connect("setup", setup)
        factory.connect("bind", bind)
        return factory

    def _pick_clone_dest(self, holder, label):
        fd = Gtk.FileDialog()

        def done(dlg, res):
            try:
                folder = dlg.select_folder_finish(res)
            except GLib.Error:
                return
            if folder:
                holder["parent"] = folder.get_path()
                label.set_text(folder.get_path())
                label.remove_css_class("dim-label")

        fd.select_folder(self, None, done)

    def _on_clone_response(self, _dialog, response, url_entry, name_entry, dest_holder):
        if response != "clone":
            return
        url = url_entry.get_text().strip()
        parent = dest_holder.get("parent")
        if not url:
            ToastService.show_error("Enter a repository URL")
            return
        if not parent:
            ToastService.show_error("Choose a destination folder")
            return
        name = name_entry.get_text().strip() or self._clone_name_from_url(url)
        self._start_clone(url, parent, name)

    @staticmethod
    def _clone_name_from_url(url: str) -> str:
        base = url.rstrip("/").split("/")[-1]
        if base.endswith(".git"):
            base = base[:-4]
        return base or "repo"

    def _start_clone(self, url, parent, name, credentials=None):
        target = Path(parent) / name
        n = 2
        while target.exists():
            target = Path(parent) / f"{name}-{n}"
            n += 1
        target = str(target)

        row, label = self._create_cloning_row(name)
        self.project_list.append(row)

        def worker():
            def progress(msg):
                GLib.idle_add(label.set_text, f"Cloning {name}… {msg[:40]}")
            try:
                GitService.clone(url, target, credentials=credentials, progress=progress)
                GLib.idle_add(self._on_clone_done, target, name, row)
            except AuthenticationRequired as exc:
                GLib.idle_add(self._on_clone_auth, url, parent, name, exc.remote_url, row)
            except Exception as exc:  # noqa: BLE001 - surfaced on the error card
                GLib.idle_add(self._on_clone_error, row, url, parent, name, target, str(exc))

        threading.Thread(target=worker, daemon=True).start()

    def _create_cloning_row(self, name):
        row = Gtk.ListBoxRow()
        row.set_activatable(False)
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        box.set_margin_start(12)
        box.set_margin_end(12)
        box.set_margin_top(12)
        box.set_margin_bottom(12)
        spinner = Gtk.Spinner()
        spinner.start()
        box.append(spinner)
        label = Gtk.Label(label=f"Cloning {name}…")
        label.set_xalign(0)
        label.set_hexpand(True)
        label.set_ellipsize(3)  # PANGO_ELLIPSIZE_END
        box.append(label)
        row.set_child(box)
        return row, label

    def _on_clone_done(self, target, name, row):
        self.project_list.remove(row)
        self.registry.register_project(target, name)
        self._load_projects()
        self._open_project(target)
        return False

    def _on_clone_auth(self, url, parent, name, remote_url, row):
        self.project_list.remove(row)
        show_github_credentials_dialog(
            self, remote_url,
            lambda creds: self._start_clone(url, parent, name, credentials=creds),
        )
        return False

    def _on_clone_error(self, row, url, parent, name, target, error):
        # Remove any partial clone directory.
        if Path(target).is_dir():
            shutil.rmtree(target, ignore_errors=True)

        # Swap the transient card to an error state with Retry / Dismiss.
        row.set_child(None)
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        box.set_margin_start(12)
        box.set_margin_end(12)
        box.set_margin_top(12)
        box.set_margin_bottom(12)
        icon = Gtk.Image.new_from_icon_name("dialog-error-symbolic")
        box.append(icon)
        label = Gtk.Label(label=f"Clone failed: {name}")
        label.set_xalign(0)
        label.set_hexpand(True)
        label.set_ellipsize(3)
        label.set_tooltip_text(error)
        box.append(label)
        retry = Gtk.Button(label="Retry")
        retry.connect("clicked", lambda _b: (self.project_list.remove(row),
                                             self._start_clone(url, parent, name)))
        box.append(retry)
        dismiss = Gtk.Button()
        dismiss.set_icon_name("window-close-symbolic")
        dismiss.add_css_class("flat")
        dismiss.connect("clicked", lambda _b: self.project_list.remove(row))
        box.append(dismiss)
        row.set_child(box)
        ToastService.show_error(f"Clone failed: {error[:80]}")
        return False

    def _open_project(self, project_path: str, force: bool = False):
        """Open a project in a new process."""
        if not Path(project_path).is_dir():
            ToastService.show_error(f"Folder not found: {project_path}")
            return

        # Check if project is already open via lock file
        from .services.project_lock import ProjectLock

        lock = ProjectLock(project_path)

        if force:
            lock.force_release()
        elif lock.is_locked():
            # Show dialog with option to force open
            pid = lock.get_lock_pid()
            dialog = Adw.AlertDialog()
            dialog.set_heading("Project Already Open")
            dialog.set_body(
                f"The project is already open in another window (PID: {pid}).\n\n"
                "If the window is not visible or the process is hung, "
                "you can force close it and reopen."
            )
            dialog.add_response("cancel", "Cancel")
            dialog.add_response("force", "Force Open")
            dialog.set_response_appearance("force", Adw.ResponseAppearance.DESTRUCTIVE)
            dialog.connect("response", self._on_force_open_response, project_path)
            dialog.present(self)
            return

        # Spawn new process
        subprocess.Popen(
            [sys.executable, "-m", "src.main", "--project", project_path],
            start_new_session=True,
        )

    def _on_force_open_response(self, _dialog, response, project_path):
        """Handle force open dialog response."""
        if response == "force":
            self._open_project(project_path, force=True)

    def _create_title_widget(self):
        """Create header title with version subtitle."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.set_valign(Gtk.Align.CENTER)

        title = Gtk.Label(label="Code Companion")
        title.add_css_class("title")

        subtitle = Gtk.Label(label=f"v{__version__}")
        subtitle.add_css_class("subtitle")

        box.append(title)
        box.append(subtitle)
        return box

    def _on_prompt_search_clicked(self, _button):
        """Open the cross-project prompt search window (8.5)."""
        window = PromptSearchWindow(self, on_open=self._open_project)
        window.present()

    def _on_about_clicked(self, _button):
        """Show about dialog."""
        info = get_version_info()

        about = Adw.AboutDialog()
        about.set_application_name("Code Companion")
        about.set_version(info["version"])
        about.set_comments("Native GTK4 companion app for AI coding assistants")

        # License
        about.set_license_type(Gtk.License.APACHE_2_0)
        about.set_copyright("© 2025 Alexander Lubovenko")

        # Links
        about.set_website("https://github.com/typedev")
        about.set_issue_url("https://github.com/typedev/code-companion/issues")

        # Credits
        about.set_developer_name("Alexander Lubovenko")
        about.set_developers(["Alexander Lubovenko <lubovenko@gmail.com>"])

        # Third-party links (clickable on Details page)
        about.add_link(
            "Material Icon Theme",
            "https://github.com/material-extensions/vscode-material-icon-theme"
        )
        about.add_link(
            "mistune - Markdown parser",
            "https://github.com/lepture/mistune"
        )
        about.add_link(
            "highlight.js - Syntax highlighting",
            "https://highlightjs.org"
        )

        # Third-party credits
        about.add_credit_section(
            "Icons",
            ["Material Icon Theme by Material Extensions"]
        )
        about.add_credit_section(
            "Libraries",
            [
                "mistune by Hsiaoming Yang",
                "highlight.js by Ivan Sagalaev",
            ]
        )

        # Legal notices for third-party components
        about.add_legal_section(
            "Material Icon Theme",
            "© 2025 Material Extensions",
            Gtk.License.MIT_X11,
            None
        )
        about.add_legal_section(
            "mistune",
            "© Hsiaoming Yang",
            Gtk.License.BSD_3,
            None
        )
        about.add_legal_section(
            "highlight.js",
            "© 2006 Ivan Sagalaev and highlight.js contributors",
            Gtk.License.BSD_3,
            None
        )

        # Debug info with commit
        if info["commit"]:
            commit_info = info["commit"]
            if info["dirty"]:
                commit_info += " (modified)"
            about.set_debug_info(f"Commit: {commit_info}")

        about.present(self)
