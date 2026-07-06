"""Project Manager window for selecting and opening projects."""

import signal
import subprocess
import sys
import threading
from pathlib import Path

from gi.repository import Adw, Gtk, GLib, Gio, Gdk

from .services.project_registry import ProjectRegistry
from .services.project_lock import ManagerLock
from .services.project_status_service import (
    ProjectStatusService,
    LocalStatus,
    RemoteStatus,
)
from .services.git_service import AuthenticationRequired
from .services.issues_service import GitHubError
from .services.sync_service import SyncService
from .services.icon_cache import IconCache
from .models.sync import ProjectSyncStatus, SyncState
from .utils.relative_time import humanize_relative
from .widgets.github_auth import show_github_credentials_dialog
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
.cc-badge-local  { background: alpha(@theme_fg_color, 0.10); color: alpha(@theme_fg_color, 0.55); }
.cc-badge-synced   { background: alpha(#33d17a, 0.18); color: #26a269; }
.cc-badge-conflict { background: alpha(#e01b24, 0.22); color: #c01c28; }
.cc-badge-syncoff  { background: alpha(@theme_fg_color, 0.10); color: alpha(@theme_fg_color, 0.55); }
"""


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

        scrolled.set_child(self.project_list)
        content_box.append(scrolled)

        # Double-click gesture for opening projects
        click_gesture = Gtk.GestureClick()
        click_gesture.set_button(1)  # Left mouse button
        click_gesture.connect("released", self._on_list_double_click)
        self.project_list.add_controller(click_gesture)

        # Buttons row
        buttons_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)

        # Remove project button
        self.remove_button = Gtk.Button(label="Remove")
        self.remove_button.add_css_class("destructive-action")
        self.remove_button.set_sensitive(False)
        self.remove_button.connect("clicked", self._on_remove_project_clicked)
        buttons_box.append(self.remove_button)

        # New project button (creates a git repo in a chosen folder)
        new_button = Gtk.Button(label="New Project...")
        new_button.connect("clicked", self._on_new_project_clicked)
        buttons_box.append(new_button)

        # Add project button
        add_button = Gtk.Button(label="Add Project...")
        add_button.add_css_class("suggested-action")
        add_button.set_hexpand(True)
        add_button.connect("clicked", self._on_add_project_clicked)
        buttons_box.append(add_button)

        content_box.append(buttons_box)

        main_box.append(content_box)
        self.set_content(main_box)

        # Track selection for remove button
        self.project_list.connect("row-selected", self._on_selection_changed)

    def _load_projects(self):
        """Load projects from registry and kick off a background status scan."""
        projects = self.registry.get_registered_projects()

        # Clear existing
        self.project_list.remove_all()
        self._rows_by_path = {}

        if not projects:
            self._show_empty_state()
            self._update_latest_refresh_label()
            return

        existing = [Path(p) for p in projects if Path(p).exists()]
        for path in existing:
            row = self._create_project_row(path)
            self.project_list.append(row)
            self._rows_by_path[str(path.resolve())] = row
            # Render any cached network status immediately (survives reopen).
            cached = self.status_service.get_cached_remote(str(path))
            if cached:
                self._render_remote_badges(row, cached)

        self._update_latest_refresh_label()
        self._start_local_scan(existing)

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

        hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        hbox.set_margin_top(10)
        hbox.set_margin_bottom(10)
        hbox.set_margin_start(14)
        hbox.set_margin_end(10)

        # Folder icon (larger)
        icon = Gtk.Image.new_from_icon_name("folder-symbolic")
        icon.set_pixel_size(32)
        icon.set_valign(Gtk.Align.CENTER)
        hbox.append(icon)

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

        hbox.append(text_box)

        # Badge container (status markers)
        badges = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        badges.set_valign(Gtk.Align.CENTER)
        row.badges_box = badges
        row.local_badges = None
        row.remote_badges = None
        row.sync_badges = None
        hbox.append(badges)

        rename_button = Gtk.Button(icon_name="document-edit-symbolic")
        rename_button.add_css_class("flat")
        rename_button.set_valign(Gtk.Align.CENTER)
        rename_button.set_tooltip_text("Rename project label")
        rename_button.connect("clicked", self._on_rename_clicked, row)
        hbox.append(rename_button)

        row.set_child(hbox)
        return row

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
        if state == SyncState.SYNCED:
            badge = self._make_badge(
                "cc-badge-synced", "In sync", icon_name="emblem-ok-symbolic"
            )
        elif state == SyncState.AHEAD:
            badge = self._make_badge(
                "cc-badge-ahead", status.detail or "Pushed to sync", text="⇧ sync"
            )
        elif state == SyncState.BEHIND:
            badge = self._make_badge(
                "cc-badge-behind", status.detail or "Updated from sync", text="⇩ sync"
            )
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
            n = len(result.per_project)
            conflicts = sum(
                1 for s in result.per_project.values() if s.state == SyncState.CONFLICT
            )
            msg = f"Synced {n} project(s)"
            if conflicts:
                msg += f" · {conflicts} conflict(s)"
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

    def _on_selection_changed(self, _listbox, row):
        """Handle selection change - enable/disable remove button."""
        self.remove_button.set_sensitive(row is not None and hasattr(row, "project_path"))

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

    def _on_remove_project_clicked(self, _button):
        """Handle remove project button click."""
        row = self.project_list.get_selected_row()
        if row and hasattr(row, "project_path"):
            self.registry.unregister_project(row.project_path)
            self._load_projects()
            self.remove_button.set_sensitive(False)

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
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(entry)
        name_dialog.set_extra_child(box)

        name_dialog.add_response("cancel", "Cancel")
        name_dialog.add_response("create", "Create")
        name_dialog.set_response_appearance("create", Adw.ResponseAppearance.SUGGESTED)
        name_dialog.set_default_response("create")
        name_dialog.connect("response", self._on_new_project_response, path, entry)
        name_dialog.present(self)

    def _on_new_project_response(self, _dialog, response, path, entry):
        if response != "create":
            return
        name = entry.get_text().strip()

        def worker():
            error = None
            try:
                result = subprocess.run(
                    ["git", "init"],
                    cwd=path,
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                if result.returncode != 0:
                    error = result.stderr.strip() or "git init failed"
            except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
                error = str(exc)
            GLib.idle_add(self._on_new_project_created, path, name, error)

        threading.Thread(target=worker, daemon=True).start()

    def _on_new_project_created(self, path, name, error):
        """Main-thread callback after `git init` completes."""
        if error:
            err = Adw.AlertDialog()
            err.set_heading("Could Not Create Project")
            err.set_body(f"git init failed:\n{error}")
            err.add_response("ok", "OK")
            err.present(self)
            return False

        self.registry.register_project(path, name)
        self._load_projects()
        self._open_project(path)
        return False

    def _open_project(self, project_path: str, force: bool = False):
        """Open a project in a new process."""
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
