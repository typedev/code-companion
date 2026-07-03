"""Issues panel widget for the sidebar (GitHub Issues list)."""

import threading
from pathlib import Path

from gi.repository import Gtk, GLib, GObject, Adw

from ..services import IssuesService, Issue, GitHubError, AuthenticationRequired, ToastService
from .github_auth import show_github_credentials_dialog

FILTER_STATES = ["open", "closed", "all"]


# Backwards-compatible re-exports; the implementation now lives in utils so the
# relative-time phrasing is shared across the app (issue_detail_view imports
# format_relative_time from here).
from ..utils.relative_time import parse_iso, humanize_relative_iso as format_relative_time  # noqa: E402,F401


class IssuesPanel(Gtk.Box):
    """Panel listing GitHub issues with an open/closed/all filter."""

    __gsignals__ = {
        # Emitted when an issue row is activated: (Issue,)
        "issue-selected": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        # Emitted after the issue set changes (create/refresh) so the badge updates.
        "issues-changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self, project_path: str, service: IssuesService):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)

        self.project_path = Path(project_path)
        self.service = service
        self._issues: list[Issue] = []
        self._filter_state = "open"
        self._loading = False
        self._loaded = False

        self._build_ui()
        self._setup_css()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------
    def _build_ui(self):
        # Header
        header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        header_box.set_margin_start(12)
        header_box.set_margin_end(12)
        header_box.set_margin_top(12)
        header_box.set_margin_bottom(6)

        self.title_label = Gtk.Label(label="Issues")
        self.title_label.set_xalign(0)
        self.title_label.add_css_class("heading")
        self.title_label.set_hexpand(True)
        header_box.append(self.title_label)

        self.new_btn = Gtk.Button()
        self.new_btn.set_icon_name("list-add-symbolic")
        self.new_btn.add_css_class("flat")
        self.new_btn.set_tooltip_text("New issue")
        self.new_btn.connect("clicked", self._on_new_issue_clicked)
        header_box.append(self.new_btn)

        refresh_btn = Gtk.Button()
        refresh_btn.set_icon_name("view-refresh-symbolic")
        refresh_btn.add_css_class("flat")
        refresh_btn.set_tooltip_text("Refresh")
        refresh_btn.connect("clicked", self._on_refresh_clicked)
        header_box.append(refresh_btn)

        self.append(header_box)

        # Filter: native segmented control (Open / Closed / All)
        self._filter_group = Adw.ToggleGroup()
        self._filter_group.set_margin_start(12)
        self._filter_group.set_margin_end(12)
        self._filter_group.set_margin_bottom(8)
        self._filter_group.set_halign(Gtk.Align.FILL)
        self._filter_group.set_hexpand(True)
        for label in ("Open", "Closed", "All"):
            self._filter_group.add(Adw.Toggle(label=label))
        self._filter_group.set_active(0)
        self._filter_group.connect("notify::active", self._on_filter_changed)
        self.append(self._filter_group)

        # Spinner
        self.spinner = Gtk.Spinner()
        self.spinner.set_margin_top(24)
        self.spinner.set_margin_bottom(24)
        self.spinner.set_visible(False)
        self.append(self.spinner)

        # Issue list (boxed-list table style, matching Git History)
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        self.issue_list = Gtk.ListBox()
        self.issue_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.issue_list.add_css_class("boxed-list")
        self.issue_list.set_margin_start(12)
        self.issue_list.set_margin_end(12)
        self.issue_list.set_margin_bottom(12)
        self.issue_list.connect("row-activated", self._on_row_activated)
        scrolled.set_child(self.issue_list)
        self.append(scrolled)

        # Empty / error state
        self.empty_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.empty_box.set_margin_start(12)
        self.empty_box.set_margin_end(12)
        self.empty_box.set_margin_top(24)
        self.empty_box.set_margin_bottom(24)
        self.empty_box.set_valign(Gtk.Align.CENTER)
        self.empty_box.set_visible(False)

        self.empty_icon = Gtk.Image.new_from_icon_name("emblem-ok-symbolic")
        self.empty_icon.set_pixel_size(48)
        self.empty_icon.add_css_class("dim-label")
        self.empty_box.append(self.empty_icon)

        self.empty_title = Gtk.Label(label="No issues")
        self.empty_title.add_css_class("dim-label")
        self.empty_title.set_wrap(True)
        self.empty_title.set_justify(Gtk.Justification.CENTER)
        self.empty_box.append(self.empty_title)

        self.empty_subtitle = Gtk.Label(label="")
        self.empty_subtitle.add_css_class("dim-label")
        self.empty_subtitle.add_css_class("caption")
        self.empty_subtitle.set_wrap(True)
        self.empty_subtitle.set_justify(Gtk.Justification.CENTER)
        self.empty_box.append(self.empty_subtitle)

        self.append(self.empty_box)

    def _setup_css(self):
        css = """
        .issue-number {
            font-family: monospace;
            font-size: 0.9em;
        }
        .issue-number-open { color: #2ecc71; }
        .issue-number-closed { color: #a371f7; }
        .issue-title { font-weight: bold; }
        .issue-meta { font-size: 0.85em; }
        .issue-label-chip {
            font-size: 0.78em;
            padding: 0 6px;
            border-radius: 8px;
            background-color: alpha(@theme_fg_color, 0.12);
        }
        """
        provider = Gtk.CssProvider()
        provider.load_from_data(css.encode())
        Gtk.StyleContext.add_provider_for_display(
            self.get_display(),
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------
    def load_if_needed(self):
        """Load issues on first tab show."""
        if not self._loaded and not self._loading:
            self.refresh()

    def refresh(self, credentials=None):
        """Reload issues for the current filter in a background thread."""
        if self._loading:
            return

        if not self.service.is_github_repo():
            self._show_empty(
                "dialog-information-symbolic",
                "GitHub issues only",
                "Issues are available only for repositories hosted on GitHub.",
            )
            self._loaded = True
            return

        self._loading = True
        self.spinner.set_visible(True)
        self.spinner.start()
        self.issue_list.set_visible(False)
        self.empty_box.set_visible(False)

        state = self._filter_state
        thread = threading.Thread(
            target=self._load_background, args=(state, credentials), daemon=True
        )
        thread.start()

    def _load_background(self, state: str, credentials):
        try:
            issues = self.service.list_issues(state, credentials=credentials)
            GLib.idle_add(self._on_loaded, issues)
        except AuthenticationRequired as exc:
            GLib.idle_add(self._on_auth_required, exc.remote_url)
        except GitHubError as exc:
            GLib.idle_add(self._on_error, exc.message)

    def _on_loaded(self, issues: list[Issue]):
        self._loading = False
        self._loaded = True
        self.spinner.stop()
        self.spinner.set_visible(False)
        self._issues = issues
        self._update_list()
        self.emit("issues-changed")

    def _on_error(self, message: str):
        self._loading = False
        self.spinner.stop()
        self.spinner.set_visible(False)
        self._show_empty("dialog-warning-symbolic", "Could not load issues", message)
        ToastService.show_error(f"GitHub: {message}")

    def _on_auth_required(self, remote_url: str):
        self._loading = False
        self.spinner.stop()
        self.spinner.set_visible(False)
        self._show_empty(
            "dialog-password-symbolic",
            "Authentication required",
            "Enter your GitHub token to load issues.",
        )
        show_github_credentials_dialog(self, remote_url, self._retry_with_credentials)

    def _retry_with_credentials(self, credentials):
        self.refresh(credentials=credentials)

    # ------------------------------------------------------------------
    # List rendering
    # ------------------------------------------------------------------
    def _update_list(self):
        while True:
            row = self.issue_list.get_row_at_index(0)
            if row is None:
                break
            self.issue_list.remove(row)

        if not self._issues:
            if self._filter_state == "closed":
                msg = "No closed issues."
            elif self._filter_state == "all":
                msg = "This repository has no issues yet."
            else:
                msg = "No open issues."
            self._show_empty("emblem-ok-symbolic", "All clear", msg)
            return

        self.empty_box.set_visible(False)
        self.issue_list.set_visible(True)

        for issue in self._issues:
            self.issue_list.append(self._create_issue_row(issue))

    def _create_issue_row(self, issue: Issue) -> Gtk.ListBoxRow:
        row = Gtk.ListBoxRow()
        row.issue = issue

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        box.set_margin_start(10)
        box.set_margin_end(10)
        box.set_margin_top(8)
        box.set_margin_bottom(8)

        is_open = issue.state == "open"

        # Top line: state dot + #number + label chips
        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)

        dot = Gtk.Label(label="●")
        dot.add_css_class("issue-number-open" if is_open else "issue-number-closed")
        top.append(dot)

        num = Gtk.Label(label=f"#{issue.number}")
        num.add_css_class("issue-number")
        num.add_css_class("issue-number-open" if is_open else "issue-number-closed")
        top.append(num)

        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        top.append(spacer)

        for label in issue.labels[:2]:
            chip = Gtk.Label(label=label)
            chip.add_css_class("issue-label-chip")
            chip.set_valign(Gtk.Align.CENTER)
            top.append(chip)
        if len(issue.labels) > 2:
            more = Gtk.Label(label=f"+{len(issue.labels) - 2}")
            more.add_css_class("issue-label-chip")
            more.set_valign(Gtk.Align.CENTER)
            top.append(more)

        box.append(top)

        # Title
        title = Gtk.Label(label=issue.title)
        title.set_xalign(0)
        title.set_ellipsize(3)  # PANGO_ELLIPSIZE_END
        title.add_css_class("issue-title")
        title.set_margin_start(18)
        title.set_tooltip_text(issue.title)
        if not is_open:
            title.add_css_class("dim-label")
        box.append(title)

        # Meta line: author · relative time · comments
        parts = []
        if issue.user:
            parts.append(issue.user)
        rel = format_relative_time(issue.created_at)
        if rel:
            parts.append(rel)
        meta_text = " · ".join(parts)
        if issue.comments:
            meta_text = f"{meta_text}   💬 {issue.comments}" if meta_text else f"💬 {issue.comments}"

        meta = Gtk.Label(label=meta_text)
        meta.set_xalign(0)
        meta.add_css_class("issue-meta")
        meta.add_css_class("dim-label")
        meta.set_margin_start(18)
        box.append(meta)

        row.set_child(box)
        return row

    def _on_row_activated(self, _listbox, row):
        if hasattr(row, "issue"):
            self.emit("issue-selected", row.issue)

    # ------------------------------------------------------------------
    # Empty-state helper
    # ------------------------------------------------------------------
    def _show_empty(self, icon_name: str, title: str, subtitle: str):
        self.issue_list.set_visible(False)
        self.empty_icon.set_from_icon_name(icon_name)
        self.empty_title.set_label(title)
        self.empty_subtitle.set_label(subtitle)
        self.empty_subtitle.set_visible(bool(subtitle))
        self.empty_box.set_visible(True)

    # ------------------------------------------------------------------
    # Filter
    # ------------------------------------------------------------------
    def _on_filter_changed(self, group, _pspec):
        index = group.get_active()
        if 0 <= index < len(FILTER_STATES):
            state = FILTER_STATES[index]
            if self._filter_state != state:
                self._filter_state = state
                self.refresh()

    def _on_refresh_clicked(self, _button):
        self.refresh()

    # ------------------------------------------------------------------
    # New issue
    # ------------------------------------------------------------------
    def _on_new_issue_clicked(self, _button):
        if not self.service.is_github_repo():
            ToastService.show_error("Not a GitHub repository")
            return

        dialog = Adw.AlertDialog()
        dialog.set_heading("New Issue")

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_start(8)
        box.set_margin_end(8)

        title_entry = Gtk.Entry()
        title_entry.set_placeholder_text("Title")
        box.append(title_entry)

        body_scroller = Gtk.ScrolledWindow()
        body_scroller.set_min_content_height(160)
        body_scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        body_scroller.add_css_class("card")
        body_view = Gtk.TextView()
        body_view.set_wrap_mode(Gtk.WrapMode.WORD)
        body_view.set_top_margin(6)
        body_view.set_bottom_margin(6)
        body_view.set_left_margin(6)
        body_view.set_right_margin(6)
        body_scroller.set_child(body_view)
        box.append(body_scroller)

        dialog.set_extra_child(box)

        dialog.add_response("cancel", "Cancel")
        dialog.add_response("create", "Create")
        dialog.set_response_appearance("create", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("create")
        dialog.set_close_response("cancel")
        dialog.connect("response", self._on_create_response, title_entry, body_view)
        dialog.present(self.get_root())

    def _on_create_response(self, _dialog, response, title_entry, body_view):
        if response != "create":
            return

        title = title_entry.get_text().strip()
        if not title:
            ToastService.show_error("Title is required")
            return

        buffer = body_view.get_buffer()
        body = buffer.get_text(
            buffer.get_start_iter(), buffer.get_end_iter(), False
        ).strip()

        self._create_issue(title, body)

    def _create_issue(self, title: str, body: str, credentials=None):
        def work():
            try:
                issue = self.service.create_issue(title, body, credentials=credentials)
                GLib.idle_add(self._on_issue_created, issue)
            except AuthenticationRequired as exc:
                GLib.idle_add(self._on_create_auth_required, title, body, exc.remote_url)
            except GitHubError as exc:
                # Bind the message eagerly; `exc` is unbound once the except
                # block exits, so the idle callback must not reference it.
                message = exc.message
                GLib.idle_add(ToastService.show_error, f"GitHub: {message}")

        threading.Thread(target=work, daemon=True).start()

    def _on_issue_created(self, issue: Issue):
        ToastService.show(f"Created issue #{issue.number}")
        # Switch to Open filter so the new issue is visible (triggers refresh).
        if self._filter_state != "open":
            self._filter_group.set_active(0)
        else:
            self.refresh()

    def _on_create_auth_required(self, title: str, body: str, remote_url: str):
        show_github_credentials_dialog(
            self, remote_url, lambda creds: self._create_issue(title, body, credentials=creds)
        )
