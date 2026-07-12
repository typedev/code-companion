"""Session view widget for displaying full session content."""

from pathlib import Path

from gi.repository import Gtk, GLib, GObject

from ..models import Session, Message, SessionContent, SessionInsight
from ..services import HistoryAdapter, SessionInsightService, GitService, run_async
from ..utils.relative_time import humanize_relative
from .message_row import MessageRow

# How many messages to render at once. Large agent sessions hold tens of
# thousands of messages; building a widget for every one freezes the UI, so we
# render only the most recent PAGE_SIZE and let the user page backwards.
PAGE_SIZE = 200


class SessionView(Gtk.Box):
    """A scrollable view of session messages (off-thread load, paginated).

    When a project path + git service are supplied, a collapsible "Changes"
    section (8.3) correlates the session with the files it touched and the
    commits made during its time window, with a one-click range diff.
    """

    __gsignals__ = {
        # A commit row in the Changes section was clicked (commit hash).
        "commit-selected": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        # A computed session diff is ready to show (tab title, raw unified diff).
        "show-diff": (GObject.SignalFlags.RUN_FIRST, None, (str, str)),
    }

    def __init__(self, adapter: HistoryAdapter, project_path: Path | str | None = None,
                 git_service: GitService | None = None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)

        self.adapter = adapter
        self.project_path = Path(project_path) if project_path else None
        self.git_service = git_service
        self.current_session: Session | None = None
        self._changes: tuple[SessionInsight, list] | None = None  # (insight, commits)

        # Pagination state.
        self._all_messages: list[Message] = []
        self._rendered_from: int = 0  # index of the oldest currently-rendered message
        self._load_earlier_btn: Gtk.Button | None = None

        self._setup_css()
        self._build_ui()

    def _setup_css(self):
        """Set up CSS for session view."""
        css = b"""
        .system-message {
            background: alpha(@warning_color, 0.1);
            border: 1px dashed alpha(@warning_color, 0.4);
        }
        .system-message .heading {
            color: @warning_color;
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
        """Build the session view UI."""
        # "Changes this session" section (8.3) — filled in the background, above
        # the message list; hidden until it has something to show.
        self.changes_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.changes_container.set_visible(False)
        self.append(self.changes_container)

        # Scrolled window for messages
        self._scrolled = Gtk.ScrolledWindow()
        self._scrolled.set_vexpand(True)
        self._scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        # Message list container
        self.message_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.message_list.set_margin_top(8)
        self.message_list.set_margin_bottom(8)

        self._scrolled.set_child(self.message_list)
        self.append(self._scrolled)

    def load_session(self, session: Session) -> None:
        """Load and display a session's content (parsed off the UI thread)."""
        self.clear()
        self.current_session = session

        # Lightweight placeholder while the (potentially multi-MB) file parses.
        placeholder = Gtk.Label(label="Loading session…")
        placeholder.add_css_class("dim-label")
        placeholder.set_margin_top(24)
        self.message_list.append(placeholder)

        # key="session" gives a generation token: switching sessions quickly
        # drops the stale load so only the newest render lands.
        run_async(
            self,
            worker=lambda: self.adapter.load_session_content(session),
            on_done=self._render,
            key="session",
        )

        # Correlate the session with its files/commits off-thread (8.3).
        self._changes = None
        self.changes_container.set_visible(False)
        run_async(
            self,
            worker=lambda: self._compute_changes(session),
            on_done=self._render_changes,
            key="changes",
        )

    def _compute_changes(self, session: Session):
        """Worker: session insight + commits made during its time window."""
        insight = SessionInsightService.get_instance().get_insight(
            session, self.adapter, str(self.project_path) if self.project_path else session.path.parent
        )
        commits = []
        if (self.git_service is not None and insight.first_ts is not None
                and insight.last_ts is not None):
            try:
                commits = self.git_service.get_commits_in_range(
                    insight.first_ts, insight.last_ts
                )
            except Exception:
                commits = []
        return insight, commits

    def _render_changes(self, result) -> None:
        """Render the Changes section: touched files + session-range commits."""
        self._changes = result
        self._clear_box(self.changes_container)
        insight, commits = result
        files = insight.files_touched

        if not files and not commits:
            self.changes_container.set_visible(False)
            return

        section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        section.set_margin_top(8)
        section.set_margin_bottom(8)
        section.set_margin_start(12)
        section.set_margin_end(12)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        title = Gtk.Label(label="Changes this session", xalign=0)
        title.add_css_class("heading")
        title.set_hexpand(True)
        header.append(title)
        # Review is possible when we have git access + something to diff.
        if self.git_service is not None and (commits or self._repo_paths(files)):
            review = Gtk.Button(label="Review session changes")
            review.add_css_class("flat")
            review.connect("clicked", self._on_review_clicked)
            header.append(review)
        section.append(header)

        if files:
            files_exp = Gtk.Expander(label=f"Files touched ({len(files)})")
            fbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            fbox.set_margin_start(12)
            for path in files:
                lbl = Gtk.Label(label=path, xalign=0)
                lbl.add_css_class("dim-label")
                lbl.set_ellipsize(1)  # START — keep the filename visible
                fbox.append(lbl)
            files_exp.set_child(fbox)
            section.append(files_exp)

        if commits:
            commits_exp = Gtk.Expander(label=f"Commits ({len(commits)})")
            commits_exp.set_expanded(True)
            cbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            cbox.set_margin_start(12)
            for commit in commits:
                cbox.append(self._commit_button(commit))
            commits_exp.set_child(cbox)
            section.append(commits_exp)

        # Cap the changes section so a session that touched many files / commits can't
        # push the message history off-screen: it grows to its natural height, then
        # scrolls internally past ~240px. propagate-natural-height keeps small sessions
        # compact (no empty scroll gap).
        changes_scroller = Gtk.ScrolledWindow()
        changes_scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        changes_scroller.set_propagate_natural_height(True)
        changes_scroller.set_max_content_height(240)
        changes_scroller.set_child(section)

        self.changes_container.append(changes_scroller)
        self.changes_container.append(Gtk.Separator())
        self.changes_container.set_visible(True)

    def _commit_button(self, commit) -> Gtk.Button:
        """A flat, clickable row for one commit → opens the commit detail tab."""
        btn = Gtk.Button()
        btn.add_css_class("flat")
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        sha = Gtk.Label(label=commit.short_hash)
        sha.add_css_class("dim-label")
        sha.add_css_class("monospace")
        row.append(sha)
        subject = (commit.message.splitlines() or [""])[0]
        msg = Gtk.Label(label=subject, xalign=0)
        msg.set_hexpand(True)
        msg.set_ellipsize(3)  # END
        row.append(msg)
        when = Gtk.Label(label=humanize_relative(commit.timestamp))
        when.add_css_class("dim-label")
        row.append(when)
        btn.set_child(row)
        btn.connect("clicked", lambda _b, h=commit.hash: self.emit("commit-selected", h))
        return btn

    def _on_review_clicked(self, _button) -> None:
        """Compute the session diff off-thread and emit it for a diff tab."""
        if not self._changes or self.git_service is None:
            return
        insight, commits = self._changes
        gs = self.git_service

        def work():
            if commits:
                first, last = commits[-1].hash, commits[0].hash  # log order = newest first
                return "Session diff", gs.get_commit_range_diff(first, last)
            paths = self._repo_paths(insight.files_touched)
            if paths:
                return "Uncommitted session changes", gs.get_paths_diff(paths)
            return "", ""

        run_async(self, worker=work, on_done=self._emit_diff, key="review")

    def _emit_diff(self, result) -> None:
        title, diff = result
        if diff.strip():
            self.emit("show-diff", title, diff)
        else:
            from ..services import ToastService
            ToastService.show("No changes to review for this session")

    def _repo_paths(self, files: list[str]) -> list[str]:
        """The touched files that live inside the project, as repo-relative paths."""
        if not self.project_path:
            return []
        out = []
        for f in files:
            try:
                out.append(str(Path(f).resolve().relative_to(self.project_path.resolve())))
            except ValueError:
                continue  # touched a file outside this project
        return out

    @staticmethod
    def _clear_box(box: Gtk.Box) -> None:
        child = box.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            box.remove(child)
            child = nxt

    def _render(self, content: SessionContent) -> None:
        """Render the parsed session: last PAGE_SIZE messages + paging affordances."""
        self._clear_children()

        messages = content.messages
        self._all_messages = messages
        total = len(messages)

        if total == 0:
            if content.in_progress:
                self._append_in_progress_indicator()
            else:
                self._show_empty_state()
            return

        start = max(0, total - PAGE_SIZE)
        self._rendered_from = start

        if start > 0:
            self._add_load_earlier_button()

        for message in messages[start:]:
            self.message_list.append(MessageRow(message))

        if content.in_progress:
            self._append_in_progress_indicator()

        # Land on the newest turn (the part a reviewer cares about).
        self._scroll_to_bottom()

    def _add_load_earlier_button(self) -> None:
        """Insert (or refresh) the top 'Load earlier messages' button."""
        remaining = self._rendered_from
        button = Gtk.Button(label=f"Load earlier messages ({remaining} remaining)")
        button.add_css_class("flat")
        button.set_margin_top(4)
        button.set_margin_bottom(4)
        button.connect("clicked", self._on_load_earlier)
        self.message_list.prepend(button)
        self._load_earlier_btn = button

    def _on_load_earlier(self, button: Gtk.Button) -> None:
        """Prepend the previous PAGE_SIZE messages, preserving scroll position."""
        vadj = self._scrolled.get_vadjustment()
        old_value = vadj.get_value()
        old_upper = vadj.get_upper()

        new_start = max(0, self._rendered_from - PAGE_SIZE)
        batch = self._all_messages[new_start:self._rendered_from]

        # Remove the button, prepend the batch above existing rows, then re-add
        # the button at the very top if there are still older messages.
        self.message_list.remove(button)
        self._load_earlier_btn = None
        for message in reversed(batch):
            self.message_list.prepend(MessageRow(message))

        self._rendered_from = new_start
        if new_start > 0:
            self._add_load_earlier_button()

        # Keep the viewport anchored on the same content: the newly prepended
        # rows grow `upper`, so shift the value by that delta once laid out.
        def restore_scroll() -> bool:
            vadj.set_value(old_value + (vadj.get_upper() - old_upper))
            return False

        GLib.idle_add(restore_scroll)

    def _append_in_progress_indicator(self) -> None:
        """Footer shown when the session's tail is still being written."""
        label = Gtk.Label(label="⏳ Session in progress")
        label.add_css_class("dim-label")
        label.set_margin_top(8)
        label.set_margin_bottom(8)
        self.message_list.append(label)

    def _scroll_to_bottom(self) -> None:
        """Scroll to the newest message once the list has been laid out."""
        def do_scroll() -> bool:
            vadj = self._scrolled.get_vadjustment()
            vadj.set_value(vadj.get_upper() - vadj.get_page_size())
            return False

        GLib.idle_add(do_scroll)

    def _show_empty_state(self) -> None:
        """Show empty state when no messages."""
        label = Gtk.Label(label="No messages in this session")
        label.add_css_class("dim-label")
        label.set_margin_top(24)
        self.message_list.append(label)

    def _clear_children(self) -> None:
        """Remove all rows from the message list."""
        # Collect children first to avoid modification during iteration.
        children = []
        child = self.message_list.get_first_child()
        while child:
            children.append(child)
            child = child.get_next_sibling()
        for child in children:
            self.message_list.remove(child)

    def clear(self) -> None:
        """Clear the session view and reset pagination state."""
        self.current_session = None
        self._all_messages = []
        self._rendered_from = 0
        self._load_earlier_btn = None
        self._changes = None
        self._clear_box(self.changes_container)
        self.changes_container.set_visible(False)
        self._clear_children()
