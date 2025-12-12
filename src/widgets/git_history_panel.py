"""Git history panel widget for viewing and managing commits."""

from datetime import datetime
from pathlib import Path

from gi.repository import Gtk, Gio, GLib, GObject, Adw

from ..services import GitService, ToastService


class GitHistoryPanel(Gtk.Box):
    """Panel displaying commit history with checkout/reset/revert actions."""

    __gsignals__ = {
        "commit-view-diff": (GObject.SignalFlags.RUN_FIRST, None, (str,)),  # commit hash
    }

    # Debounce delay for file monitor events (ms)
    REFRESH_DEBOUNCE = 300

    def __init__(self, git_service: GitService):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)

        self.service = git_service
        self._selected_commit = None
        self._file_monitors: list[Gio.FileMonitor] = []
        self._refresh_timeout_id: int | None = None
        self._all_commits = []  # Cache all commits for filtering
        self._filter_text = ""

        self._build_ui()
        self._setup_css()
        self._setup_file_monitoring()
        self.refresh()

        self.connect("destroy", self._on_destroy)

    def _setup_file_monitoring(self):
        """Set up file monitoring for git refs changes."""
        if not self.service.repo:
            return

        git_dir = Path(self.service.repo.path).parent / ".git"
        if not git_dir.exists():
            git_dir = Path(self.service.repo.path)

        # Monitor refs/heads for new commits and branch changes
        refs_heads = git_dir / "refs" / "heads"
        if refs_heads.exists():
            self._add_monitor(refs_heads)

        # Monitor HEAD for checkout/branch switch
        head_file = git_dir / "HEAD"
        if head_file.exists():
            self._add_monitor(head_file.parent, watch_file=head_file.name)

        # Monitor logs/HEAD for commit history
        logs_head = git_dir / "logs" / "HEAD"
        if logs_head.exists():
            self._add_monitor(logs_head.parent, watch_file="HEAD")

    def _add_monitor(self, path: Path, watch_file: str | None = None):
        """Add a file monitor."""
        try:
            gfile = Gio.File.new_for_path(str(path))
            monitor = gfile.monitor_directory(
                Gio.FileMonitorFlags.NONE,
                None
            )
            monitor.connect("changed", self._on_git_changed, watch_file)
            self._file_monitors.append(monitor)
        except GLib.Error:
            pass

    def _on_git_changed(self, monitor, file, other_file, event_type, watch_file):
        """Handle git directory changes."""
        # Filter by specific file if specified
        if watch_file and file.get_basename() != watch_file:
            return

        if event_type in (
            Gio.FileMonitorEvent.CHANGED,
            Gio.FileMonitorEvent.CREATED,
            Gio.FileMonitorEvent.DELETED,
        ):
            self._schedule_refresh()

    def _schedule_refresh(self):
        """Schedule a debounced refresh."""
        if self._refresh_timeout_id is not None:
            GLib.source_remove(self._refresh_timeout_id)

        self._refresh_timeout_id = GLib.timeout_add(
            self.REFRESH_DEBOUNCE,
            self._do_scheduled_refresh
        )

    def _do_scheduled_refresh(self) -> bool:
        """Perform scheduled refresh."""
        self._refresh_timeout_id = None
        self.refresh()
        return False

    def _on_destroy(self, widget):
        """Clean up on destroy."""
        if self._refresh_timeout_id is not None:
            GLib.source_remove(self._refresh_timeout_id)
            self._refresh_timeout_id = None

        for monitor in self._file_monitors:
            monitor.cancel()
        self._file_monitors.clear()

    def _setup_css(self):
        """Set up CSS for history panel."""
        css = b"""
        .commit-hash {
            font-family: monospace;
            font-size: 0.9em;
            color: #f39c12;
        }
        .commit-message {
            font-weight: bold;
        }
        .commit-meta {
            font-size: 0.85em;
        }
        .commit-head {
            color: #2ecc71;
            font-weight: bold;
        }
        """
        provider = Gtk.CssProvider()
        provider.load_from_data(css)
        Gtk.StyleContext.add_provider_for_display(
            self.get_display(),
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

    def _build_ui(self):
        """Build the panel UI."""
        # Header
        header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        header_box.set_margin_start(12)
        header_box.set_margin_end(12)
        header_box.set_margin_top(12)
        header_box.set_margin_bottom(6)

        label = Gtk.Label(label="Commits")
        label.set_xalign(0)
        label.set_hexpand(True)
        label.add_css_class("heading")
        header_box.append(label)

        # Refresh button
        refresh_btn = Gtk.Button()
        refresh_btn.set_icon_name("view-refresh-symbolic")
        refresh_btn.add_css_class("flat")
        refresh_btn.set_tooltip_text("Refresh")
        refresh_btn.connect("clicked", lambda b: self.refresh())
        header_box.append(refresh_btn)

        self.append(header_box)

        # Search entry
        self.search_entry = Gtk.SearchEntry()
        self.search_entry.set_placeholder_text("Filter commits...")
        self.search_entry.set_margin_start(12)
        self.search_entry.set_margin_end(12)
        self.search_entry.set_margin_bottom(6)
        self.search_entry.connect("search-changed", self._on_search_changed)
        self.append(self.search_entry)

        # Scrolled list
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        self.commits_list = Gtk.ListBox()
        self.commits_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.commits_list.add_css_class("boxed-list")
        self.commits_list.set_margin_start(12)
        self.commits_list.set_margin_end(12)
        self.commits_list.set_margin_bottom(6)
        self.commits_list.connect("row-selected", self._on_row_selected)
        self.commits_list.connect("row-activated", self._on_row_activated)

        scrolled.set_child(self.commits_list)
        self.append(scrolled)

        # Action buttons
        actions_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        actions_box.set_margin_start(12)
        actions_box.set_margin_end(12)
        actions_box.set_margin_bottom(12)
        actions_box.set_homogeneous(True)

        self.checkout_btn = Gtk.Button(label="Checkout")
        self.checkout_btn.set_sensitive(False)
        self.checkout_btn.connect("clicked", self._on_checkout_clicked)
        actions_box.append(self.checkout_btn)

        # Reset menu button
        self.reset_btn = Gtk.MenuButton(label="Reset")
        self.reset_btn.set_sensitive(False)
        self._setup_reset_menu()
        actions_box.append(self.reset_btn)

        self.revert_btn = Gtk.Button(label="Revert")
        self.revert_btn.set_sensitive(False)
        self.revert_btn.connect("clicked", self._on_revert_clicked)
        actions_box.append(self.revert_btn)

        self.append(actions_box)

    def _setup_reset_menu(self):
        """Set up reset dropdown menu."""
        menu = Gio.Menu()
        menu.append("Soft Reset", "reset.soft")
        menu.append("Hard Reset", "reset.hard")

        self.reset_btn.set_menu_model(menu)

        # Action group
        action_group = Gio.SimpleActionGroup()

        soft_action = Gio.SimpleAction.new("soft", None)
        soft_action.connect("activate", self._on_reset_soft)
        action_group.add_action(soft_action)

        hard_action = Gio.SimpleAction.new("hard", None)
        hard_action.connect("activate", self._on_reset_hard)
        action_group.add_action(hard_action)

        self.insert_action_group("reset", action_group)

    def refresh(self):
        """Refresh the commits list."""
        # Save current selection
        selected_hash = None
        if self._selected_commit:
            selected_hash = self._selected_commit.hash

        # Clear existing
        self.commits_list.remove_all()
        self._selected_commit = None

        # Get commits
        try:
            self._all_commits = self.service.get_commits(limit=50)
        except Exception as e:
            label = Gtk.Label(label=f"Error: {e}")
            label.add_css_class("dim-label")
            self.commits_list.append(label)
            self._update_buttons()
            return

        self._display_commits(selected_hash)

    def _on_search_changed(self, entry):
        """Handle search entry changes."""
        self._filter_text = entry.get_text().strip().lower()
        # Preserve selection during filtering
        selected_hash = self._selected_commit.hash if self._selected_commit else None
        self._display_commits(selected_hash)

    def _display_commits(self, selected_hash: str = None):
        """Display commits with current filter, optionally restoring selection."""
        self.commits_list.remove_all()

        if not self._all_commits:
            label = Gtk.Label(label="No commits yet")
            label.add_css_class("dim-label")
            label.set_margin_top(24)
            self.commits_list.append(label)
            self._update_buttons()
            return

        # Filter commits
        if self._filter_text:
            filtered = [
                c for c in self._all_commits
                if self._filter_text in c.message.lower()
                or self._filter_text in c.author.lower()
                or self._filter_text in c.short_hash.lower()
            ]
        else:
            filtered = self._all_commits

        if not filtered:
            label = Gtk.Label(label="No matching commits")
            label.add_css_class("dim-label")
            label.set_margin_top(24)
            self.commits_list.append(label)
            self._update_buttons()
            return

        # Rebuild list and restore selection
        row_to_select = None
        for commit in filtered:
            row = self._create_commit_row(commit)
            self.commits_list.append(row)
            if selected_hash and commit.hash == selected_hash:
                row_to_select = row

        # Restore selection
        if row_to_select:
            self.commits_list.select_row(row_to_select)

    def _create_commit_row(self, commit) -> Gtk.ListBoxRow:
        """Create a row for a commit."""
        row = Gtk.ListBoxRow()
        row.commit = commit

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        box.set_margin_start(8)
        box.set_margin_end(8)
        box.set_margin_top(8)
        box.set_margin_bottom(8)

        # Top line: indicator + hash
        top_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)

        # HEAD indicator
        indicator = Gtk.Label(label="●" if commit.is_head else "○")
        if commit.is_head:
            indicator.add_css_class("commit-head")
        else:
            indicator.add_css_class("dim-label")
        top_box.append(indicator)

        # Hash
        hash_label = Gtk.Label(label=commit.short_hash)
        hash_label.add_css_class("commit-hash")
        top_box.append(hash_label)

        # HEAD badge
        if commit.is_head:
            head_badge = Gtk.Label(label="HEAD")
            head_badge.add_css_class("commit-head")
            head_badge.set_margin_start(6)
            top_box.append(head_badge)

        box.append(top_box)

        # Message
        message_label = Gtk.Label(label=commit.message.split('\n')[0])
        message_label.set_xalign(0)
        message_label.set_ellipsize(2)  # MIDDLE
        message_label.add_css_class("commit-message")
        message_label.set_margin_start(20)
        box.append(message_label)

        # Author and time
        relative_time = self._format_relative_time(commit.timestamp)
        meta_text = f"{commit.author} · {relative_time}"
        meta_label = Gtk.Label(label=meta_text)
        meta_label.set_xalign(0)
        meta_label.add_css_class("commit-meta")
        meta_label.add_css_class("dim-label")
        meta_label.set_margin_start(20)
        box.append(meta_label)

        row.set_child(box)
        return row

    def _format_relative_time(self, timestamp: datetime) -> str:
        """Format timestamp as relative time."""
        now = datetime.now()
        diff = now - timestamp

        seconds = diff.total_seconds()
        minutes = seconds / 60
        hours = minutes / 60
        days = hours / 24
        weeks = days / 7

        if seconds < 60:
            return "just now"
        elif minutes < 60:
            n = int(minutes)
            return f"{n} minute{'s' if n != 1 else ''} ago"
        elif hours < 24:
            n = int(hours)
            return f"{n} hour{'s' if n != 1 else ''} ago"
        elif days < 7:
            n = int(days)
            return f"{n} day{'s' if n != 1 else ''} ago"
        elif weeks < 4:
            n = int(weeks)
            return f"{n} week{'s' if n != 1 else ''} ago"
        else:
            return timestamp.strftime("%b %d, %Y")

    def _on_row_selected(self, listbox, row):
        """Handle commit selection."""
        if row and hasattr(row, "commit"):
            self._selected_commit = row.commit
        else:
            self._selected_commit = None
        self._update_buttons()

    def _update_buttons(self):
        """Update button sensitivity based on selection."""
        has_selection = self._selected_commit is not None
        is_head = has_selection and self._selected_commit.is_head

        self.checkout_btn.set_sensitive(has_selection and not is_head)
        self.reset_btn.set_sensitive(has_selection and not is_head)
        self.revert_btn.set_sensitive(has_selection and not is_head)

    def _on_row_activated(self, list_box, row):
        """Handle row activation (double-click or Enter) - open commit details."""
        if row and hasattr(row, "commit"):
            self.emit("commit-view-diff", row.commit.hash)

    def _on_checkout_clicked(self, button):
        """Handle checkout button click."""
        if not self._selected_commit:
            return

        dialog = Adw.AlertDialog()
        dialog.set_heading("Checkout Commit?")
        dialog.set_body(
            f"This will checkout commit {self._selected_commit.short_hash} "
            "and put you in a detached HEAD state.\n\n"
            "Any uncommitted changes may be lost."
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("checkout", "Checkout")
        dialog.set_response_appearance("checkout", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.connect("response", self._on_checkout_response)
        dialog.present(self.get_root())

    def _on_checkout_response(self, dialog, response):
        """Handle checkout dialog response."""
        if response == "checkout" and self._selected_commit:
            try:
                self.service.checkout_commit(self._selected_commit.hash)
                self.refresh()
                self._show_toast(f"Checked out {self._selected_commit.short_hash}")
            except Exception as e:
                self._show_error(f"Checkout failed: {e}")

    def _on_reset_soft(self, action, param):
        """Handle soft reset."""
        self._do_reset(hard=False)

    def _on_reset_hard(self, action, param):
        """Handle hard reset."""
        if not self._selected_commit:
            return

        dialog = Adw.AlertDialog()
        dialog.set_heading("Hard Reset?")
        dialog.set_body(
            f"This will reset to commit {self._selected_commit.short_hash} "
            "and DISCARD all changes.\n\n"
            "This action cannot be undone!"
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("reset", "Hard Reset")
        dialog.set_response_appearance("reset", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.connect("response", self._on_hard_reset_response)
        dialog.present(self.get_root())

    def _on_hard_reset_response(self, dialog, response):
        """Handle hard reset dialog response."""
        if response == "reset":
            self._do_reset(hard=True)

    def _do_reset(self, hard: bool):
        """Perform reset operation."""
        if not self._selected_commit:
            return

        try:
            self.service.reset_to_commit(self._selected_commit.hash, hard=hard)
            self.refresh()
            mode = "Hard" if hard else "Soft"
            self._show_toast(f"{mode} reset to {self._selected_commit.short_hash}")
        except Exception as e:
            self._show_error(f"Reset failed: {e}")

    def _on_revert_clicked(self, button):
        """Handle revert button click."""
        if not self._selected_commit:
            return

        dialog = Adw.AlertDialog()
        dialog.set_heading("Revert Commit?")
        dialog.set_body(
            f"This will create a new commit that undoes the changes "
            f"from {self._selected_commit.short_hash}."
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("revert", "Revert")
        dialog.set_response_appearance("revert", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("cancel")
        dialog.connect("response", self._on_revert_response)
        dialog.present(self.get_root())

    def _on_revert_response(self, dialog, response):
        """Handle revert dialog response."""
        if response == "revert" and self._selected_commit:
            try:
                new_hash = self.service.revert_commit(self._selected_commit.hash)
                self.refresh()
                self._show_toast(f"Created revert commit {new_hash}")
            except Exception as e:
                self._show_error(f"Revert failed: {e}")

    def _show_toast(self, message: str):
        """Show a toast notification."""
        ToastService.show(message)

    def _show_error(self, message: str):
        """Show error toast."""
        ToastService.show_error(message)
