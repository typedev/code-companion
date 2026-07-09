"""Message thread detail view shown in the main tab area (coordination hub, idea 2).

Renders one inter-project thread (subject, route, body + comments) and lets the human act:
reply, change status, delete (tombstone), or hand the thread to the Claude session. Backed by
the local synced ``message_store``. Modeled on IssueDetailView, plus a reply box and status
control (which the read-only Issues view lacks).
"""

import html as html_lib

from gi.repository import GObject, Gtk

from ..services import message_store, ToastService, SettingsService
from ..utils.relative_time import humanize_relative_iso as format_relative_time
from .markdown_preview import MarkdownPreview
from .messages_panel import short_project, _STATUS_LABELS
from .query_editor import language_name_for_code

_STATUS_ORDER = list(message_store.STATUSES)


class MessageThreadView(Gtk.Box):
    """Main-area view for a single message thread with reply, status, and delete."""

    __gsignals__ = {
        # Request to send a prepared prompt to the Claude terminal: (prompt_str,)
        "send-to-claude": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        # Emitted after the thread is mutated (reply/status/delete): (MessageThread,)
        "thread-changed": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
    }

    def __init__(self, thread, me: str):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.thread = thread
        self._me = me
        self._setting_status = False

        self._setup_css()
        self._build_ui()
        self._render()

    def _setup_css(self):
        css = """
        .msg-state-pill {
            font-size: 0.85em; font-weight: bold; padding: 2px 10px;
            border-radius: 10px; color: white;
        }
        .msg-state-open { background-color: #3584e4; }
        .msg-state-in_progress { background-color: #e5a50a; }
        .msg-state-done { background-color: #2da44e; }
        .msg-state-rejected { background-color: #77767b; }
        .msg-header { padding: 12px; background: alpha(@card_bg_color, 0.5); }
        """
        provider = Gtk.CssProvider()
        provider.load_from_data(css.encode())
        Gtk.StyleContext.add_provider_for_display(
            self.get_display(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

    def _build_ui(self):
        header = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        header.add_css_class("msg-header")

        title_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.state_pill = Gtk.Label()
        self.state_pill.add_css_class("msg-state-pill")
        self.state_pill.set_valign(Gtk.Align.CENTER)
        title_row.append(self.state_pill)
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
        self.append(header)

        # Action bar
        action_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        action_bar.set_margin_start(12)
        action_bar.set_margin_end(12)
        action_bar.set_margin_top(8)
        action_bar.set_margin_bottom(8)

        send_btn = Gtk.Button(label="Send to Claude")
        send_btn.add_css_class("suggested-action")
        send_btn.connect("clicked", self._on_send_to_claude)
        action_bar.append(send_btn)

        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        action_bar.append(spacer)

        status_label = Gtk.Label(label="Status:")
        status_label.add_css_class("dim-label")
        status_label.set_valign(Gtk.Align.CENTER)
        action_bar.append(status_label)

        self.status_dropdown = Gtk.DropDown.new_from_strings(
            [_STATUS_LABELS[s] for s in _STATUS_ORDER]
        )
        self.status_dropdown.set_valign(Gtk.Align.CENTER)
        self.status_dropdown.connect("notify::selected", self._on_status_changed)
        action_bar.append(self.status_dropdown)

        delete_btn = Gtk.Button()
        delete_btn.set_icon_name("user-trash-symbolic")
        delete_btn.set_tooltip_text("Delete thread")
        delete_btn.add_css_class("flat")
        delete_btn.connect("clicked", self._on_delete)
        action_bar.append(delete_btn)
        self.append(action_bar)

        self.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        self.body_preview = MarkdownPreview()
        self.body_preview.set_vexpand(True)
        self.append(self.body_preview)

        # Reply box
        reply_bar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        reply_bar.set_margin_start(12)
        reply_bar.set_margin_end(12)
        reply_bar.set_margin_top(6)
        reply_bar.set_margin_bottom(12)

        reply_scroller = Gtk.ScrolledWindow()
        reply_scroller.set_min_content_height(70)
        reply_scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        reply_scroller.add_css_class("card")
        self.reply_view = Gtk.TextView()
        self.reply_view.set_wrap_mode(Gtk.WrapMode.WORD)
        self.reply_view.set_top_margin(6)
        self.reply_view.set_bottom_margin(6)
        self.reply_view.set_left_margin(6)
        self.reply_view.set_right_margin(6)
        reply_scroller.set_child(self.reply_view)
        reply_bar.append(reply_scroller)

        reply_actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        reply_spacer = Gtk.Box()
        reply_spacer.set_hexpand(True)
        reply_actions.append(reply_spacer)
        self.reply_btn = Gtk.Button(label="Reply")
        self.reply_btn.add_css_class("suggested-action")
        self.reply_btn.connect("clicked", self._on_reply)
        reply_actions.append(self.reply_btn)
        reply_bar.append(reply_actions)
        self.append(reply_bar)

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------
    def _render(self):
        thread = self.thread
        status = thread.status

        self.state_pill.set_label(_STATUS_LABELS.get(status, status))
        for s in _STATUS_ORDER:
            self.state_pill.remove_css_class(f"msg-state-{s}")
        self.state_pill.add_css_class(f"msg-state-{status}")

        self.title_label.set_label(thread.subject or "(no subject)")

        route = f"{short_project(thread.from_project)} → {short_project(thread.to_project)}"
        rel = format_relative_time(thread.created_at)
        self.meta_label.set_label(f"{route} · {rel}" if rel else route)

        self._setting_status = True
        try:
            self.status_dropdown.set_selected(
                _STATUS_ORDER.index(status) if status in _STATUS_ORDER else 0
            )
        finally:
            self._setting_status = False

        self._render_markdown()

    def _render_markdown(self):
        thread = self.thread
        body_md = thread.body.strip() if thread.body else "_No message body._"
        body_html = MarkdownPreview.render_markdown(body_md)
        sections = [f'<div class="issue-body">{body_html}</div>']

        if thread.refs:
            refs = ", ".join(html_lib.escape(r) for r in thread.refs)
            sections.append(f'<p class="comment-date">Refs: {refs}</p>')

        if thread.comments:
            sections.append(
                f'<h3 class="issue-comments-title">Replies ({len(thread.comments)})</h3>'
            )
            for c in thread.comments:
                who = html_lib.escape(short_project(c.actor) or "unknown")
                rel = format_relative_time(c.ts)
                date_html = (
                    f' <span class="comment-date">· {html_lib.escape(rel)}</span>'
                    if rel else ""
                )
                comment_html = MarkdownPreview.render_markdown(c.body or "_(empty)_")
                sections.append(
                    '<div class="issue-comment">'
                    f'<div class="comment-head">{who}{date_html}</div>'
                    f'<div class="comment-body">{comment_html}</div>'
                    "</div>"
                )
        self.body_preview.update_html("\n".join(sections))

    def update(self, thread):
        """Point the view at a different thread (tab reuse)."""
        self.thread = thread
        self._render()

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def _reload(self):
        refreshed = message_store.load_thread(self.thread.thread_id)
        if refreshed is not None:
            self.thread = refreshed
            self._render()
            self.emit("thread-changed", refreshed)

    def _on_reply(self, _button):
        buffer = self.reply_view.get_buffer()
        body = buffer.get_text(
            buffer.get_start_iter(), buffer.get_end_iter(), False
        ).strip()
        if not body:
            return
        try:
            message_store.add_comment(self.thread.thread_id, self._me, body)
        except message_store.MessageStoreError as exc:
            ToastService.show_error(str(exc))
            return
        buffer.set_text("")
        self._reload()

    def _on_status_changed(self, dropdown, _pspec):
        if self._setting_status:
            return
        index = dropdown.get_selected()
        if not (0 <= index < len(_STATUS_ORDER)):
            return
        new_status = _STATUS_ORDER[index]
        if new_status == self.thread.status:
            return
        try:
            message_store.set_status(self.thread.thread_id, self._me, new_status)
        except message_store.MessageStoreError as exc:
            ToastService.show_error(str(exc))
            return
        self._reload()

    def _on_delete(self, _button):
        message_store.delete_thread(self.thread.thread_id, self._me)
        ToastService.show("Thread deleted")
        self.emit("thread-changed", self.thread)

    def _on_send_to_claude(self, _button):
        self.emit("send-to-claude", self._build_prompt())

    def _build_prompt(self) -> str:
        thread = self.thread
        parts = [
            f'I received an inter-project message: "{thread.subject}".',
            "",
            f"From: {thread.from_project}",
            f"To: {thread.to_project}",
            f"Status: {thread.status}",
        ]
        if thread.refs:
            parts.append(f"Refs: {', '.join(thread.refs)}")
        parts += ["", "Message:", thread.body.strip() or "(no body)"]

        if thread.comments:
            parts += ["", f"Replies ({len(thread.comments)}):"]
            for i, c in enumerate(thread.comments, 1):
                rel = format_relative_time(c.ts)
                who = short_project(c.actor) + (f", {rel}" if rel else "")
                parts += ["", f"--- Reply {i} by {who} ---", c.body.strip() or "(empty)"]

        parts += [
            "",
            "Help me understand and act on this request in the context of the current "
            "project. Start with a short analysis and plan; do not write code yet.",
        ]

        lang = language_name_for_code(
            SettingsService.get_instance().get("editor.spellcheck_language", "auto")
        )
        if lang:
            parts += ["", f"Please respond to me in {lang}."]
        return "\n".join(parts)
