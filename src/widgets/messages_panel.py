"""Messages panel for the sidebar — inter-project mailbox (coordination hub, idea 2).

Lists message threads involving the current project, backed by the local synced
``message_store`` (not GitHub). Mirrors the Issues panel structure. Sending requires the
current project to have a git remote (canonical identity); a local-only project shows a
"requires a remote" state with compose disabled.
"""

import threading

from gi.repository import Adw, GLib, GObject, Gtk

from ..services import message_store, project_catalog
from ..services import ToastService
from ..utils.relative_time import humanize_relative_iso as format_relative_time

# Sidebar filter -> message_store box.
FILTER_BOXES = ["inbox", "sent", "all"]

_STATUS_LABELS = {
    "open": "Open",
    "in_progress": "In progress",
    "done": "Done",
    "rejected": "Rejected",
}


def short_project(canonical_remote: str) -> str:
    """``host/owner/repo`` -> ``owner/repo`` for compact display."""
    if not canonical_remote:
        return "?"
    parts = canonical_remote.split("/")
    return "/".join(parts[1:]) if len(parts) > 1 else canonical_remote


class MessagesPanel(Gtk.Box):
    """Panel listing inter-project message threads with an inbox/sent/all filter."""

    __gsignals__ = {
        # Emitted when a thread row is activated: (MessageThread,)
        "message-selected": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        # Emitted after the thread set changes (create/refresh) so the badge updates.
        "messages-changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self, me: str | None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)

        self._me = me or ""  # current project's canonical remote ("" -> local-only)
        self._threads = []
        self._filter_box = "inbox"
        self._loading = False
        self._loaded = False

        self._build_ui()
        self._setup_css()
        if not self._me:
            self._show_empty(
                "network-offline-symbolic",
                "Messages require a git remote",
                "Add an origin remote to this project to send and receive messages.",
            )
            self.new_btn.set_sensitive(False)
            self._filter_group.set_sensitive(False)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------
    def _build_ui(self):
        header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        header_box.set_margin_start(12)
        header_box.set_margin_end(12)
        header_box.set_margin_top(12)
        header_box.set_margin_bottom(6)

        self.title_label = Gtk.Label(label="Messages")
        self.title_label.set_xalign(0)
        self.title_label.add_css_class("heading")
        self.title_label.set_hexpand(True)
        header_box.append(self.title_label)

        self.new_btn = Gtk.Button()
        self.new_btn.set_icon_name("list-add-symbolic")
        self.new_btn.add_css_class("flat")
        self.new_btn.set_tooltip_text("New message")
        self.new_btn.connect("clicked", self._on_new_clicked)
        header_box.append(self.new_btn)

        refresh_btn = Gtk.Button()
        refresh_btn.set_icon_name("view-refresh-symbolic")
        refresh_btn.add_css_class("flat")
        refresh_btn.set_tooltip_text("Refresh")
        refresh_btn.connect("clicked", lambda _b: self.refresh())
        header_box.append(refresh_btn)

        self.append(header_box)

        # Filter: Inbox / Sent / All
        self._filter_group = Adw.ToggleGroup()
        self._filter_group.set_margin_start(12)
        self._filter_group.set_margin_end(12)
        self._filter_group.set_margin_bottom(8)
        self._filter_group.set_halign(Gtk.Align.FILL)
        self._filter_group.set_hexpand(True)
        for label in ("Inbox", "Sent", "All"):
            self._filter_group.add(Adw.Toggle(label=label))
        self._filter_group.set_active(0)
        self._filter_group.connect("notify::active", self._on_filter_changed)
        self.append(self._filter_group)

        self.spinner = Gtk.Spinner()
        self.spinner.set_margin_top(24)
        self.spinner.set_margin_bottom(24)
        self.spinner.set_visible(False)
        self.append(self.spinner)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        self.thread_list = Gtk.ListBox()
        self.thread_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.thread_list.add_css_class("boxed-list")
        self.thread_list.set_margin_start(12)
        self.thread_list.set_margin_end(12)
        self.thread_list.set_margin_bottom(12)
        self.thread_list.connect("row-activated", self._on_row_activated)
        scrolled.set_child(self.thread_list)
        self.append(scrolled)

        self.empty_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.empty_box.set_margin_start(12)
        self.empty_box.set_margin_end(12)
        self.empty_box.set_margin_top(24)
        self.empty_box.set_margin_bottom(24)
        self.empty_box.set_valign(Gtk.Align.CENTER)
        self.empty_box.set_visible(False)

        self.empty_icon = Gtk.Image.new_from_icon_name("mail-read-symbolic")
        self.empty_icon.set_pixel_size(48)
        self.empty_icon.add_css_class("dim-label")
        self.empty_box.append(self.empty_icon)

        self.empty_title = Gtk.Label(label="No messages")
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
        .msg-subject { font-weight: bold; }
        .msg-meta { font-size: 0.85em; }
        .msg-dot-open { color: #3584e4; }
        .msg-dot-in_progress { color: #e5a50a; }
        .msg-dot-done { color: #2ecc71; }
        .msg-dot-rejected { color: alpha(@theme_fg_color, 0.4); }
        """
        provider = Gtk.CssProvider()
        provider.load_from_data(css.encode())
        Gtk.StyleContext.add_provider_for_display(
            self.get_display(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------
    def load_if_needed(self):
        if self._me and not self._loaded and not self._loading:
            self.refresh()

    def refresh(self):
        if not self._me or self._loading:
            return
        self._loading = True
        self.spinner.set_visible(True)
        self.spinner.start()
        self.thread_list.set_visible(False)
        self.empty_box.set_visible(False)

        me, box = self._me, self._filter_box

        def work():
            threads = message_store.threads_for(me, box=box)
            GLib.idle_add(self._on_loaded, threads)

        threading.Thread(target=work, daemon=True).start()

    def _on_loaded(self, threads):
        self._loading = False
        self._loaded = True
        self.spinner.stop()
        self.spinner.set_visible(False)
        self._threads = threads
        self._update_list()
        self.emit("messages-changed")

    # ------------------------------------------------------------------
    # List rendering
    # ------------------------------------------------------------------
    def _update_list(self):
        while (row := self.thread_list.get_row_at_index(0)) is not None:
            self.thread_list.remove(row)

        if not self._threads:
            msg = {
                "inbox": "No incoming messages.",
                "sent": "You have not sent any messages.",
                "all": "No messages yet.",
            }[self._filter_box]
            self._show_empty("mail-read-symbolic", "All clear", msg)
            return

        self.empty_box.set_visible(False)
        self.thread_list.set_visible(True)
        for thread in self._threads:
            self.thread_list.append(self._create_thread_row(thread))

    def _create_thread_row(self, thread) -> Gtk.ListBoxRow:
        row = Gtk.ListBoxRow()
        row.thread = thread

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        box.set_margin_start(10)
        box.set_margin_end(10)
        box.set_margin_top(8)
        box.set_margin_bottom(8)

        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        dot = Gtk.Label(label="●")
        dot.add_css_class(f"msg-dot-{thread.status}")
        top.append(dot)

        subject = Gtk.Label(label=thread.subject or "(no subject)")
        subject.set_xalign(0)
        subject.set_ellipsize(3)  # PANGO_ELLIPSIZE_END
        subject.add_css_class("msg-subject")
        subject.set_hexpand(True)
        subject.set_tooltip_text(thread.subject)
        top.append(subject)

        status = Gtk.Label(label=_STATUS_LABELS.get(thread.status, thread.status))
        status.add_css_class("msg-meta")
        status.add_css_class("dim-label")
        status.set_valign(Gtk.Align.CENTER)
        top.append(status)
        box.append(top)

        # Meta: from -> to · relative activity · comment count
        route = f"{short_project(thread.from_project)} → {short_project(thread.to_project)}"
        rel = format_relative_time(thread.last_activity)
        meta_text = f"{route} · {rel}" if rel else route
        if thread.comments:
            meta_text = f"{meta_text}   💬 {len(thread.comments)}"
        meta = Gtk.Label(label=meta_text)
        meta.set_xalign(0)
        meta.add_css_class("msg-meta")
        meta.add_css_class("dim-label")
        meta.set_margin_start(18)
        box.append(meta)

        row.set_child(box)
        return row

    def _on_row_activated(self, _listbox, row):
        if hasattr(row, "thread"):
            self.emit("message-selected", row.thread)

    def _show_empty(self, icon_name: str, title: str, subtitle: str):
        self.thread_list.set_visible(False)
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
        if 0 <= index < len(FILTER_BOXES):
            box = FILTER_BOXES[index]
            if self._filter_box != box:
                self._filter_box = box
                self.refresh()

    # ------------------------------------------------------------------
    # New message (recipient picked from the project catalog)
    # ------------------------------------------------------------------
    def _recipients(self) -> list[tuple[str, str]]:
        """(label, message_address) for every cataloged project addressable != me.

        Uses the mailbox address, so a worktree (host/owner/repo#wt:<branch>) is a
        distinct recipient from its parent rather than being collapsed onto it.
        """
        out = []
        for entry in project_catalog.list_catalog():
            addr = entry.message_address
            if addr and addr != self._me:
                out.append((f"{entry.name}  ({short_project(addr)})", addr))
        out.sort(key=lambda x: x[0].lower())
        return out

    def _on_new_clicked(self, _button):
        if not self._me:
            return
        recipients = self._recipients()
        if not recipients:
            ToastService.show_error(
                "No other projects with a git remote are registered"
            )
            return

        dialog = Adw.AlertDialog()
        dialog.set_heading("New Message")

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_start(8)
        box.set_margin_end(8)

        to_label = Gtk.Label(label="To")
        to_label.set_xalign(0)
        to_label.add_css_class("dim-label")
        box.append(to_label)
        dropdown = Gtk.DropDown.new_from_strings([label for label, _ in recipients])
        box.append(dropdown)

        subject_entry = Gtk.Entry()
        subject_entry.set_placeholder_text("Subject")
        box.append(subject_entry)

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
        dialog.add_response("send", "Send")
        dialog.set_response_appearance("send", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("send")
        dialog.set_close_response("cancel")
        dialog.connect(
            "response", self._on_new_response, recipients, dropdown, subject_entry, body_view
        )
        dialog.present(self.get_root())

    def _on_new_response(self, _dialog, response, recipients, dropdown, subject_entry, body_view):
        if response != "send":
            return
        subject = subject_entry.get_text().strip()
        if not subject:
            ToastService.show_error("Subject is required")
            return
        index = dropdown.get_selected()
        if not (0 <= index < len(recipients)):
            return
        to = recipients[index][1]
        buffer = body_view.get_buffer()
        body = buffer.get_text(
            buffer.get_start_iter(), buffer.get_end_iter(), False
        ).strip()

        try:
            message_store.create_thread(self._me, to, subject, body)
        except message_store.MessageStoreError as exc:
            ToastService.show_error(str(exc))
            return
        ToastService.show(f"Message sent to {short_project(to)}")
        # Show it in Sent so the user sees it land.
        if self._filter_box != "sent":
            self._filter_group.set_active(1)
        else:
            self.refresh()
