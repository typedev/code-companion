"""Issue detail view shown in the main tab area."""

import html as html_lib
import threading
from pathlib import Path

from gi.repository import Gtk, GLib, GObject, Gio

from ..services import (
    IssuesService,
    Issue,
    GitHubError,
    AuthenticationRequired,
    ToastService,
    SettingsService,
)
from .github_auth import show_github_credentials_dialog
from .markdown_preview import MarkdownPreview
from .issues_panel import format_relative_time
from .query_editor import language_name_for_code


class IssueDetailView(Gtk.Box):
    """Main-area view for a single GitHub issue with body, comments and actions."""

    __gsignals__ = {
        # Request to send a prepared prompt to the Claude terminal: (prompt_str,)
        "send-to-claude": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        # Emitted after the issue is mutated (close/reopen): (Issue,)
        "issue-changed": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
    }

    def __init__(self, issue: Issue, service: IssuesService):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)

        self.issue = issue
        self.service = service
        self._busy = False
        self._comments = []
        self._comments_token = 0

        self._setup_css()
        self._build_ui()
        self._render()
        self._load_comments()

    def _setup_css(self):
        css = """
        .issue-state-pill {
            font-size: 0.85em;
            font-weight: bold;
            padding: 2px 10px;
            border-radius: 10px;
            color: white;
        }
        .issue-state-open { background-color: #2da44e; }
        .issue-state-closed { background-color: #8250df; }
        .issue-label-chip {
            font-size: 0.82em;
            padding: 1px 8px;
            border-radius: 9px;
            background-color: alpha(@theme_fg_color, 0.12);
        }
        .issue-header {
            padding: 12px;
            background: alpha(@card_bg_color, 0.5);
        }
        """
        provider = Gtk.CssProvider()
        provider.load_from_data(css.encode())
        Gtk.StyleContext.add_provider_for_display(
            self.get_display(),
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

    def _build_ui(self):
        # Header: state pill + #number + title
        header = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        header.add_css_class("issue-header")

        title_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

        self.state_pill = Gtk.Label()
        self.state_pill.add_css_class("issue-state-pill")
        self.state_pill.set_valign(Gtk.Align.CENTER)
        title_row.append(self.state_pill)

        self.number_label = Gtk.Label()
        self.number_label.add_css_class("dim-label")
        self.number_label.set_valign(Gtk.Align.CENTER)
        title_row.append(self.number_label)

        header.append(title_row)

        self.title_label = Gtk.Label()
        self.title_label.set_xalign(0)
        self.title_label.add_css_class("title-2")
        self.title_label.set_wrap(True)
        header.append(self.title_label)

        self.meta_label = Gtk.Label()
        self.meta_label.set_xalign(0)
        self.meta_label.add_css_class("dim-label")
        self.meta_label.add_css_class("caption")
        header.append(self.meta_label)

        # Label chips row
        self.labels_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        header.append(self.labels_box)

        self.append(header)

        # Action bar
        action_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        action_bar.set_margin_start(12)
        action_bar.set_margin_end(12)
        action_bar.set_margin_top(8)
        action_bar.set_margin_bottom(8)

        # Left: Send to Claude (text) + Open on GitHub (github mark icon)
        send_btn = Gtk.Button(label="Send to Claude")
        send_btn.add_css_class("suggested-action")
        send_btn.connect("clicked", self._on_send_to_claude)
        action_bar.append(send_btn)

        browser_btn = Gtk.Button()
        browser_btn.set_tooltip_text("Open on GitHub")
        github_icon_path = (
            Path(__file__).parent.parent / "resources" / "icons" / "github.svg"
        )
        if github_icon_path.exists():
            gicon = Gio.FileIcon.new(Gio.File.new_for_path(str(github_icon_path)))
            img = Gtk.Image.new_from_gicon(gicon)
            img.set_pixel_size(16)
            browser_btn.set_child(img)
        else:
            browser_btn.set_icon_name("web-browser-symbolic")
        browser_btn.connect("clicked", self._on_open_browser)
        action_bar.append(browser_btn)

        # Spacer pushes Close/Reopen to the right
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        action_bar.append(spacer)

        self.state_btn = Gtk.Button()
        self.state_btn.connect("clicked", self._on_toggle_state)
        action_bar.append(self.state_btn)

        self.append(action_bar)

        self.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        # Body + comments rendered as a single markdown document (WebKit)
        self.body_preview = MarkdownPreview()
        self.body_preview.set_vexpand(True)
        self.append(self.body_preview)

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------
    def _render(self):
        issue = self.issue
        is_open = issue.state == "open"

        self.state_pill.set_label("Open" if is_open else "Closed")
        self.state_pill.remove_css_class("issue-state-open")
        self.state_pill.remove_css_class("issue-state-closed")
        self.state_pill.add_css_class(
            "issue-state-open" if is_open else "issue-state-closed"
        )

        self.number_label.set_label(f"#{issue.number}")
        self.title_label.set_label(issue.title)

        meta = f"opened by {issue.user}" if issue.user else ""
        rel = format_relative_time(issue.created_at)
        if rel:
            meta = f"{meta} · {rel}" if meta else rel
        self.meta_label.set_label(meta)

        # Labels
        while child := self.labels_box.get_first_child():
            self.labels_box.remove(child)
        for label in issue.labels:
            chip = Gtk.Label(label=label)
            chip.add_css_class("issue-label-chip")
            self.labels_box.append(chip)
        self.labels_box.set_visible(bool(issue.labels))

        # State button
        self.state_btn.set_label("Reopen" if not is_open else "Close issue")
        self.state_btn.remove_css_class("destructive-action")
        if is_open:
            self.state_btn.add_css_class("destructive-action")
        self.state_btn.set_sensitive(not self._busy)

        self._render_markdown()

    def _render_markdown(self):
        """Render the issue body plus comments as styled HTML (body + cards)."""
        issue = self.issue

        body_md = issue.body.strip() if issue.body else "_No description provided._"
        body_html = MarkdownPreview.render_markdown(body_md)
        sections = [f'<div class="issue-body">{body_html}</div>']

        if self._comments:
            sections.append(
                f'<h3 class="issue-comments-title">Comments ({len(self._comments)})</h3>'
            )
            for c in self._comments:
                user = html_lib.escape(c.user or "ghost")
                rel = format_relative_time(c.created_at)
                date_html = (
                    f' <span class="comment-date">· {html_lib.escape(rel)}</span>'
                    if rel
                    else ""
                )
                comment_html = MarkdownPreview.render_markdown(c.body or "_(empty)_")
                sections.append(
                    '<div class="issue-comment">'
                    f'<div class="comment-head">@{user}{date_html}</div>'
                    f'<div class="comment-body">{comment_html}</div>'
                    "</div>"
                )

        self.body_preview.update_html("\n".join(sections))

    def update(self, issue: Issue):
        """Update the view with a different issue (for tab reuse)."""
        self.issue = issue
        self._busy = False
        self._comments = []
        self._render()
        self._load_comments()

    # ------------------------------------------------------------------
    # Comments
    # ------------------------------------------------------------------
    def _load_comments(self):
        if self.issue.comments == 0:
            return  # nothing to fetch

        self._comments_token += 1
        token = self._comments_token
        number = self.issue.number

        def work():
            try:
                comments = self.service.list_comments(number)
                GLib.idle_add(self._on_comments_loaded, token, comments)
            except Exception:
                GLib.idle_add(self._on_comments_loaded, token, None)

        threading.Thread(target=work, daemon=True).start()

    def _on_comments_loaded(self, token: int, comments):
        if token != self._comments_token:
            return  # superseded by a newer issue
        if comments is None:
            return  # keep body-only render on error
        self._comments = comments
        self._render_markdown()

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def _on_open_browser(self, _button):
        if self.issue.html_url:
            Gio.AppInfo.launch_default_for_uri(self.issue.html_url, None)

    def _on_send_to_claude(self, _button):
        self.emit("send-to-claude", self._build_prompt())

    def _build_prompt(self) -> str:
        """Build the prompt fed to Claude: issue + comments + language request."""
        issue = self.issue
        labels = ", ".join(issue.labels) if issue.labels else "none"
        body = issue.body.strip() if issue.body else "(no description provided)"

        parts = [
            f'Please help me work on GitHub issue #{issue.number}: "{issue.title}".',
            "",
            f"State: {issue.state}",
            f"URL: {issue.html_url}",
            f"Labels: {labels}",
            "",
            "Issue description:",
            body,
        ]

        if self._comments:
            parts += [
                "",
                f"Below are the {len(self._comments)} comment(s) from the issue "
                "discussion (these are comments, not part of the description):",
            ]
            for i, c in enumerate(self._comments, 1):
                rel = format_relative_time(c.created_at)
                who = f"@{c.user}" + (f", {rel}" if rel else "")
                cbody = c.body.strip() if c.body else "(empty)"
                parts += ["", f"--- Comment {i} by {who} ---", cbody]

        parts += [
            "",
            "Analyze this issue in the context of the current project, propose an "
            "implementation plan, and identify the files that need to change. Do not "
            "write code yet — start with the plan.",
        ]

        lang = language_name_for_code(
            SettingsService.get_instance().get("editor.spellcheck_language", "auto")
        )
        if lang:
            parts += ["", f"Please respond to me in {lang}."]

        return "\n".join(parts)

    def _on_toggle_state(self, _button):
        if self._busy:
            return
        new_state = "closed" if self.issue.state == "open" else "open"
        self._set_state(new_state)

    def _set_state(self, new_state: str, credentials=None):
        self._busy = True
        self.state_btn.set_sensitive(False)
        number = self.issue.number

        def work():
            try:
                updated = self.service.set_issue_state(
                    number, new_state, credentials=credentials
                )
                GLib.idle_add(self._on_state_changed, updated)
            except AuthenticationRequired as exc:
                GLib.idle_add(self._on_state_auth_required, new_state, exc.remote_url)
            except GitHubError as exc:
                GLib.idle_add(self._on_state_error, exc.message)

        threading.Thread(target=work, daemon=True).start()

    def _on_state_changed(self, updated: Issue):
        # Preserve the comment count (PATCH response carries it too).
        self.issue = updated
        self._busy = False
        self._render()
        verb = "Closed" if updated.state == "closed" else "Reopened"
        ToastService.show(f"{verb} issue #{updated.number}")
        self.emit("issue-changed", updated)

    def _on_state_error(self, message: str):
        self._busy = False
        self.state_btn.set_sensitive(True)
        ToastService.show_error(f"GitHub: {message}")

    def _on_state_auth_required(self, new_state: str, remote_url: str):
        self._busy = False
        self.state_btn.set_sensitive(True)
        show_github_credentials_dialog(
            self, remote_url, lambda creds: self._set_state(new_state, credentials=creds)
        )
