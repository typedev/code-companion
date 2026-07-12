"""Per-window MCP control surface.

Hosts a local Model Context Protocol server (streamable HTTP on 127.0.0.1) so the
embedded Claude session can act on *this running window* — read the workspace state,
raise a notification, and (in later increments) open files, show diffs, etc.

Design (see docs/plan-mcp-integration.md, decisions D1-D4):
- One server per ``ProjectWindow`` (NON_UNIQUE process -> own random port -> free
  per-window isolation). Lifecycle == Claude-session lifecycle.
- Transport: the official ``mcp`` SDK's FastMCP ``streamable_http_app()`` ASGI app,
  run under programmatic uvicorn in a background thread with its own asyncio loop.
  Running under ``Server.serve()`` drives the ASGI lifespan, which starts the
  streamable-HTTP session-manager task group (skipping it -> "Task group is not
  initialized").
- Auth: a per-window bearer token checked by a pure-ASGI middleware (pure ASGI, not
  Starlette ``BaseHTTPMiddleware``, so SSE streaming responses are not buffered).
- Threading: tool handlers run on the server thread; every GTK touch is marshalled
  to the main loop via :meth:`McpServer.call_on_main`. Never touch GTK from the
  server thread; never block the main loop on the server.
"""

from __future__ import annotations

import asyncio
import json
import threading
from concurrent.futures import Future
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gio  # noqa: E402

import uvicorn  # noqa: E402
from mcp.server.fastmcp import FastMCP, Image  # noqa: E402
from starlette.concurrency import run_in_threadpool  # noqa: E402

from .gui_harness import GuiHarnessError, GuiHarnessManager  # noqa: E402
from .toast_service import ToastService  # noqa: E402

# Cap on selection text returned by get_selection, so a huge selection can't bloat
# the tool response; the ``truncated`` flag signals when the cap was hit.
_MAX_SELECTION_CHARS = 100_000


class _BearerAuthMiddleware:
    """Pure-ASGI middleware: reject requests without a matching bearer token.

    Pure ASGI (not ``BaseHTTPMiddleware``) so it does not buffer the streaming /
    SSE responses the MCP transport relies on.
    """

    def __init__(self, app, token: str):
        self.app = app
        self._expected = f"Bearer {token}".encode()

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers") or [])
        if headers.get(b"authorization") != self._expected:
            await send({
                "type": "http.response.start",
                "status": 401,
                "headers": [(b"content-type", b"text/plain")],
            })
            await send({"type": "http.response.body", "body": b"Unauthorized"})
            return
        await self.app(scope, receive, send)


class _RefreshEndpoint:
    """Pure-ASGI shim: handle ``POST /refresh``, delegate everything else.

    Lets a hook (or the model) refresh the app's panels after a terminal action
    (``git commit`` / ``gh issue create``) that did not go through an MCP tool.
    Sits behind :class:`_BearerAuthMiddleware`, so it already requires the token.
    """

    def __init__(self, app, server: "McpServer"):
        self.app = app
        self.server = server

    async def __call__(self, scope, receive, send):
        if not (
            scope["type"] == "http"
            and scope.get("path") == "/refresh"
            and scope.get("method") == "POST"
        ):
            await self.app(scope, receive, send)
            return

        body = b""
        while True:
            message = await receive()
            body += message.get("body", b"")
            if not message.get("more_body"):
                break

        try:
            target = (json.loads(body) if body else {}).get("target", "all")
        except (ValueError, AttributeError):
            await self._respond(send, 400, {"error": "invalid JSON body"})
            return

        # Offload so the event loop is not blocked while the main thread refreshes.
        refreshed = await run_in_threadpool(self.server._do_refresh, target)
        await self._respond(send, 200, {"refreshed": refreshed})

    @staticmethod
    async def _respond(send, status: int, payload: dict) -> None:
        data = json.dumps(payload).encode()
        await send({
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", b"application/json")],
        })
        await send({"type": "http.response.body", "body": data})


class McpServer:
    """Local MCP server bound to a single ``ProjectWindow``."""

    def __init__(self, window):
        self.window = window
        self.gui = GuiHarnessManager()
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self.port: int | None = None

    # ------------------------------------------------------------------ #
    # Main-thread marshaling
    # ------------------------------------------------------------------ #
    def call_on_main(self, fn, timeout: float = 5):
        """Run ``fn`` on the GTK main loop from the server thread; return its result.

        Blocks the calling (server) thread until the main loop runs ``fn``. Raises
        ``TimeoutError`` if the main loop does not service it in time, or re-raises
        whatever ``fn`` raised — either way the MCP layer maps it to a tool error
        instead of hanging.
        """
        fut: Future = Future()

        def _run():
            try:
                fut.set_result(fn())
            except Exception as exc:  # noqa: BLE001 - forwarded to the caller
                fut.set_exception(exc)
            return False  # GLib.SOURCE_REMOVE

        GLib.idle_add(_run)
        return fut.result(timeout)

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #
    def start(self, port: int, token: str) -> None:
        """Start the server thread bound to ``127.0.0.1:port`` with ``token`` auth."""
        if self._thread is not None:
            return
        self.port = port

        # Compose pure-ASGI layers around the FastMCP app (not Starlette
        # add_middleware) so /refresh can be added without touching Starlette
        # internals; non-http scopes (incl. the session lifespan) pass through.
        mcp_app = self._build_mcp().streamable_http_app()
        app = _BearerAuthMiddleware(_RefreshEndpoint(mcp_app, self), token=token)

        config = uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            log_level="warning",
            access_log=False,
            loop="asyncio",
        )
        self._server = uvicorn.Server(config)
        # uvicorn installs SIGINT/SIGTERM handlers in serve(); signal.signal() only
        # works on the main thread, so disable it for our background thread.
        self._server.install_signal_handlers = lambda: None

        self._thread = threading.Thread(
            target=self._run, name="mcp-server", daemon=True
        )
        self._thread.start()

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        try:
            loop.run_until_complete(self._server.serve())
        finally:
            loop.close()

    def stop(self, timeout: float = 5) -> None:
        """Signal graceful shutdown and wait for the server thread to exit."""
        # Tear down any GUI harnesses first (kills their headless compositor trees).
        self.gui.stop_all()
        if self._server is not None:
            self._server.should_exit = True  # checked on the next serve() tick
        if self._thread is not None:
            self._thread.join(timeout)
        self._server = None
        self._thread = None
        self._loop = None
        self.port = None

    # ------------------------------------------------------------------ #
    # Tools
    # ------------------------------------------------------------------ #
    def _build_mcp(self) -> FastMCP:
        mcp = FastMCP("code-companion")

        @mcp.tool()
        def notify(message: str) -> str:
            """Show a message to the user in the Code Companion window.

            Displays a toast; if the window is not focused, also raises a desktop
            notification.
            """
            return self.call_on_main(lambda: self._do_notify(message))

        @mcp.tool()
        def get_workspace_state() -> dict:
            """Return the active file, its cursor line, and the open editor tabs.

            Read-only snapshot of what the user currently has open in the window.
            """
            return self.call_on_main(self._do_get_workspace_state)

        @mcp.tool()
        def get_selection() -> dict:
            """Return the current text selection in the active editor or terminal.

            ``source`` is ``"editor"``, ``"terminal"``, or null. For an editor it also
            reports the file path and the selected range (1-based lines, 0-based
            columns); for a terminal only the selected text (path/lines are null).
            ``has_selection`` is false when nothing is selected.
            """
            return self.call_on_main(self._do_get_selection)

        @mcp.tool()
        def get_problems(path: str | None = None) -> dict:
            """Return the current linter findings shown in the Problems panel.

            Read-only snapshot of the panel's cached results; it does not re-run the
            linters. ``has_run`` is false until the linters have completed at least
            once (open the Problems tab to populate them). Pass ``path`` to filter to
            a single file.
            """
            return self.call_on_main(lambda: self._do_get_problems(path))

        @mcp.tool()
        def list_linters() -> dict:
            """List the linters this app knows about and their status for this project.

            Each entry: id, name, enabled (in settings), status (available /
            not_installed), the file extensions it handles, whether the project has
            such files, and an install_hint command. Use ``run_linter`` to run one.
            """
            return self.call_on_main(self._do_list_linters)

        @mcp.tool()
        def run_linter(linter_id: str, paths: list[str] | None = None) -> dict:
            """Run a single linter now and return its structured findings.

            Runs the linter even if disabled in settings (on-demand). ``paths`` limits
            it to specific files (else it runs project-wide / over matching files). The
            Problems panel is refreshed so the human sees the current state too. Returns
            {problems, counts, status, install_hint}.
            """
            # Runs on the FastMCP worker thread; the linter subprocess stays here (it
            # may exceed call_on_main's 5s budget). Only the GUI refresh is marshalled.
            return self._do_run_linter(linter_id, paths)

        @mcp.tool()
        def list_tasks() -> dict:
            """Return the tasks defined in the project's .vscode/tasks.json."""
            return self.call_on_main(self._do_list_tasks)

        @mcp.tool()
        def open_file(
            path: str, line: int | None = None, end_line: int | None = None
        ) -> dict:
            """Open a file in a tab and (optionally) scroll to / highlight a range.

            ``path`` may be absolute or project-relative. With ``line`` the editor
            scrolls to that 1-based line; with both ``line`` and ``end_line`` the
            whole line range is selected. Returns ``{"ok": bool, "path", "error"?}``.
            """
            return self.call_on_main(
                lambda: self._do_open_file(path, line, end_line)
            )

        @mcp.tool()
        def show_diff(path: str) -> dict:
            """Open the working-tree diff view for a file (project-relative or absolute).

            Returns ``{"ok": bool, "path", "error"?}``.
            """
            return self.call_on_main(lambda: self._do_show_diff(path))

        @mcp.tool()
        def show_commit(commit_hash: str) -> dict:
            """Open the commit-detail tab for a commit hash.

            Returns ``{"ok": bool, "short_hash"?, "error"?}``.
            """
            return self.call_on_main(lambda: self._do_show_commit(commit_hash))

        @mcp.tool()
        def create_issue(title: str, body: str = "") -> dict:
            """Create a GitHub issue for this project and refresh the Issues panel.

            Uses the stored GitHub credentials. Returns
            ``{"ok": bool, "number"?, "url"?, "error"?}``.
            """
            # Runs on the FastMCP worker thread; the blocking network call stays here
            # (NOT via call_on_main, which has a 5s timeout).
            return self._do_create_issue(title, body)

        @mcp.tool()
        def run_task(name: str) -> dict:
            """Run a tasks.json task by its label in a new terminal tab.

            Returns ``{"ok": bool, "label"?, "error"?}``.
            """
            return self.call_on_main(lambda: self._do_run_task(name))

        @mcp.tool()
        def create_task(
            label: str,
            command: str,
            type: str = "shell",
            group: str | None = None,
            args: list[str] | None = None,
        ) -> dict:
            """Add or update a task in the project's .vscode/tasks.json (VSCode format).

            Use this when asked to "make a task" / "add a task": it writes a canonical
            tasks.json entry that then appears in the Tasks panel and can be run with
            ``run_task``. If a task with the same ``label`` exists it is updated in place.
            The file is created if missing. ``group`` is e.g. "build"/"test"; ``args`` is
            an optional argument list. Returns ``{"ok": bool, "result"?, "label"?, "error"?}``.
            Note: JSONC comments in an existing tasks.json are not preserved.
            """
            return self.call_on_main(
                lambda: self._do_create_task(label, command, type, group, args)
            )

        @mcp.tool()
        def add_note(name: str, content: str) -> dict:
            """Create or append to a markdown note under ``notes/<name>.md``.

            Returns ``{"ok": bool, "path"?, "error"?}``.
            """
            # Filesystem-only; safe to run on the worker thread. The notes panel
            # auto-refreshes via FileMonitorService.
            return self._do_add_note(name, content)

        @mcp.tool()
        def set_session_summary(content: str, title: str = "") -> dict:
            """Save a résumé / next-session plan for this project.

            Overwrites the project's single summary; surfaced on its card in the
            Project Manager. Use at session end to hand off state. Returns
            ``{"ok": true, "path": ...}``.
            """
            return self._do_set_session_summary(content, title)

        @mcp.tool()
        def get_session_summary() -> dict:
            """Return the last saved session summary for this project.

            ``{"ok": true, "exists": bool, "title", "updated", "content"}`` — read your
            own handoff at the start of a session.
            """
            return self._do_get_session_summary()

        @mcp.tool()
        def list_projects() -> dict:
            """List all projects registered in Code Companion on this machine.

            Read-only catalog for cross-project work. Each entry has its name, local path,
            and canonical git remote identity (``remote_url`` = ``host/owner/repo``, null
            for local-only or non-git projects; ``project_id`` = the stable sync id). Use
            resolve_project to turn a name/hint into a concrete path, then study that
            project with your normal Read/Grep/git tools.
            """
            return self._do_list_projects()

        @mcp.tool()
        def resolve_project(hint: str) -> dict:
            """Resolve a project name or hint to its local path and remote identity.

            ``hint`` is matched against each project's name, folder, or git owner/repo.
            Returns ``{"match", "candidates", "ambiguous"}``: a single winner sets
            ``match`` (an entry like list_projects returns) with an empty ``candidates``;
            an ambiguous hint sets ``ambiguous`` true, leaves ``match`` null, and lists the
            tied entries in ``candidates``; no match leaves both empty/null.
            """
            return self._do_resolve_project(hint)

        @mcp.tool()
        def send_message(to: str, subject: str, body: str,
                         refs: list[str] | None = None) -> dict:
            """Send an inter-project message to another registered project.

            ``to`` is a project name/hint (resolved via the catalog) or a canonical
            ``host/owner/repo`` remote. Opens a new thread from *this* project; the
            recipient's human triages it in their Messages panel. This project must have a
            git remote. ``refs`` optionally lists relevant file paths. Returns
            ``{"ok", "thread_id", "to"}`` or ``{"ok": false, "error", "candidates"?}``.
            """
            return self._do_send_message(to, subject, body, refs)

        @mcp.tool()
        def list_messages(box: str = "inbox", status: str | None = None) -> dict:
            """List inter-project message threads involving this project.

            ``box`` is ``inbox`` (addressed to us), ``sent`` (from us), or ``all``.
            ``status`` optionally filters to open|in_progress|done|rejected. Returns
            ``{"ok": true, "count", "messages": [...]}`` with each thread's body and replies.
            """
            return self._do_list_messages(box, status)

        @mcp.tool()
        def reply_message(thread_id: str, body: str) -> dict:
            """Post a reply to an existing message thread. Returns ``{"ok", "thread_id"}``."""
            return self._do_reply_message(thread_id, body)

        @mcp.tool()
        def resolve_message(thread_id: str, status: str = "done") -> dict:
            """Set a message thread's status (open|in_progress|done|rejected).

            Returns ``{"ok", "thread_id", "status"}``.
            """
            return self._do_resolve_message(thread_id, status)

        @mcp.tool()
        def report_worktree_complete(summary: str, tests: str = "", review: str = "") -> dict:
            """Report this worktree's task as complete to its parent project (Stage 5).

            Only valid in a worktree window. Writes a local completion report that the
            parent surfaces ("N ready") and its agent reads to integrate your branch.
            Call this AFTER you have verified the feature works, run /code-review,
            **committed** (the merge preview only sees committed state), AND the human
            in this window has confirmed the task is done — the "N ready" signal must
            mean human-confirmed, not agent-assumed. Put a short summary in ``summary``,
            the reviewer's findings in ``review``, and the test status in ``tests``.
            Returns ``{"ok", "branch"}``.
            """
            return self._do_report_worktree_complete(summary, tests, review)

        @mcp.tool()
        def list_worktree_reports() -> dict:
            """List completion reports from this project's worktrees, ready to integrate.

            Returns ``{"ok", "count", "reports": [{branch, worktree_path, tests,
            created, body}]}``. Use before merging so you know what's done + its review.
            """
            return self._do_list_worktree_reports()

        @mcp.tool()
        def resolve_worktree_report(branch: str) -> dict:
            """Clear a worktree's completion report after merging its branch.

            Returns ``{"ok", "branch"}``.
            """
            return self._do_resolve_worktree_report(branch)

        @mcp.tool()
        def list_worktrees() -> dict:
            """List this project's linked worktrees (Stage 6).

            Returns ``{"ok", "count", "worktrees": [{path, branch, head, dirty}]}`` —
            the main checkout is excluded.
            """
            return self._do_list_worktrees()

        @mcp.tool()
        def create_worktree(task_name: str, branch: str = "", base: str = "") -> dict:
            """Create a worktree of this project for a task and register it (Stage 6).

            Derives branch ``feature/<slug>`` and a sibling ``<repo>--<slug>`` folder
            from ``task_name`` unless given explicitly. Returns ``{"ok", "path",
            "branch"}``. Open it as its own window (or hand the path to a subagent).
            """
            return self._do_create_worktree(task_name, branch, base)

        @mcp.tool()
        def preview_merge(branch: str) -> dict:
            """Check whether ``branch`` merges cleanly into this project's current
            branch, without touching the working tree (git merge-tree). Returns
            ``{"ok", "clean", "conflicts": [...]}`` (committed state only)."""
            return self._do_preview_merge(branch)

        @mcp.tool()
        def merge_worktree(branch: str) -> dict:
            """Merge a worktree ``branch`` into this project's current branch after a
            clean ``preview_merge``, then clear its completion report. On conflicts it
            does nothing and returns them. Returns ``{"ok", "branch"}`` or the conflicts."""
            return self._do_merge_worktree(branch)

        @mcp.tool()
        def gui_launch(cmd: str, width: int = 1280, height: int = 800) -> dict:
            """Launch a GUI app in an isolated headless compositor for inspection.

            Returns a ``handle`` to screenshot/stop later. ``cmd`` is the shell command
            to run the app. Returns ``{"ok": bool, "handle"?, "error"?}``.
            """
            return self._do_gui_launch(cmd, width, height)

        @mcp.tool()
        def gui_screenshot(handle: str):
            """Capture the current frame of a launched GUI as a PNG image.

            Returns an MCP image on success, or ``{"ok": false, "error"}``.
            """
            return self._do_gui_screenshot(handle)

        @mcp.tool()
        def gui_stop(handle: str) -> dict:
            """Tear down a launched GUI harness (app + headless compositor)."""
            return self._do_gui_stop(handle)

        @mcp.tool()
        def gui_snapshot_tree(handle: str) -> dict:
            """Return the launched GUI's accessibility tree (roles, names, extents).

            Returns ``{"ok": true, "tree": {...}}`` or ``{"ok": false, "error"}``.
            """
            return self._do_gui_snapshot_tree(handle)

        @mcp.tool()
        def gui_click(handle: str, role: str | None = None,
                      name: str | None = None, nth: int = 0) -> dict:
            """Click a widget in the launched GUI, located by accessibility role/name.

            Actionable matches are preferred; ``nth`` picks among multiple matches.
            """
            return self._do_gui_action(handle, "click", role, name, None, None, nth)

        @mcp.tool()
        def gui_type(handle: str, text: str, role: str | None = None,
                     name: str | None = None, nth: int = 0) -> dict:
            """Set the text of an editable widget, located by role/name."""
            return self._do_gui_action(handle, "type", role, name, None, text, nth)

        @mcp.tool()
        def gui_do_action(handle: str, role: str | None = None,
                          name: str | None = None, action: str | None = None,
                          nth: int = 0) -> dict:
            """Invoke a named accessibility action on a widget (default: first action)."""
            return self._do_gui_action(handle, "do_action", role, name, action, None, nth)

        @mcp.tool()
        def gui_pointer(handle: str, x: int, y: int, button: str = "left",
                        action: str = "click", dy: int = 0) -> dict:
            """Inject a pointer action at screenshot coordinates (x, y).

            ``action``: click | double | move | scroll (scroll uses ``dy`` steps).
            Use for widgets AT-SPI can't reach (popovers, canvases, list rows).
            """
            return self._do_gui_input(
                handle, {"kind": "pointer", "x": x, "y": y, "button": button,
                         "action": action, "dy": dy}
            )

        @mcp.tool()
        def gui_key(handle: str, combo: str | None = None,
                    text: str | None = None) -> dict:
            """Send a key combo (e.g. 'Return', 'ctrl+shift+t') or type ``text``
            into the focused widget via a virtual keyboard."""
            return self._do_gui_input(handle, {"kind": "key", "combo": combo,
                                               "text": text})

        return mcp

    # -- main-thread tool bodies -------------------------------------- #
    def _do_notify(self, message: str) -> str:
        ToastService.show(message)
        if not self.window.is_active():
            app = self.window.get_application()
            if app is not None:
                notification = Gio.Notification.new("Code Companion")
                notification.set_body(message)
                app.send_notification(None, notification)
        return f"Notified: {message}"

    def _do_get_workspace_state(self) -> dict:
        tab_view = getattr(self.window, "tab_view", None)
        tabs: list[dict] = []
        active_file = None
        cursor_line = None

        if tab_view is not None:
            selected = tab_view.get_selected_page()
            for i in range(tab_view.get_n_pages()):
                page = tab_view.get_nth_page(i)
                child = page.get_child()
                file_path = getattr(child, "file_path", None)
                entry = {"title": page.get_title(), "path": file_path}
                if hasattr(child, "is_modified"):
                    entry["dirty"] = bool(child.is_modified)
                tabs.append(entry)

                if page is selected:
                    active_file = file_path
                    if file_path is not None and hasattr(child, "_get_cursor_line"):
                        cursor_line = child._get_cursor_line()

        return {
            "active_file": active_file,
            "cursor_line": cursor_line,
            "open_tabs": tabs,
        }

    def _active_child(self):
        tab_view = getattr(self.window, "tab_view", None)
        if tab_view is None:
            return None
        page = tab_view.get_selected_page()
        return page.get_child() if page is not None else None

    def _do_get_selection(self) -> dict:
        empty = {
            "source": None,
            "path": None,
            "has_selection": False,
            "start_line": None,
            "start_column": None,
            "end_line": None,
            "end_column": None,
            "text": None,
            "truncated": False,
        }
        child = self._active_child()
        if child is None:
            return empty

        # Editor tab: selection with a path + line/column range.
        if hasattr(child, "buffer") and hasattr(child, "file_path"):
            return self._editor_selection(child, empty)

        # Terminal tab: selected text only (no path/lines).
        if hasattr(child, "get_selected_text"):
            text = child.get_selected_text()
            if not text:
                return {**empty, "source": "terminal"}
            text, truncated = self._cap_selection(text)
            return {**empty, "source": "terminal", "has_selection": True,
                    "text": text, "truncated": truncated}

        return empty

    def _editor_selection(self, editor, empty: dict) -> dict:
        buffer = editor.buffer
        if not buffer.get_has_selection():
            return {**empty, "source": "editor", "path": editor.file_path}

        start, end = buffer.get_selection_bounds()
        text, truncated = self._cap_selection(buffer.get_text(start, end, False))
        return {
            "source": "editor",
            "path": editor.file_path,
            "has_selection": True,
            "start_line": start.get_line() + 1,
            "start_column": start.get_line_offset(),
            "end_line": end.get_line() + 1,
            "end_column": end.get_line_offset(),
            "text": text,
            "truncated": truncated,
        }

    @staticmethod
    def _cap_selection(text: str) -> tuple[str, bool]:
        truncated = len(text) > _MAX_SELECTION_CHARS
        return (text[:_MAX_SELECTION_CHARS] if truncated else text), truncated

    def _do_get_problems(self, path: str | None) -> dict:
        panel = getattr(self.window, "problems_panel", None)
        if panel is None:
            return {"problems": [], "counts": {"error": 0, "warning": 0, "total": 0},
                    "has_run": False}

        problems: list[dict] = []
        for file_problems in panel._problems.values():
            for p in file_problems.problems:
                if path is not None and p.file != path:
                    continue
                problems.append({
                    "file": p.file,
                    "line": p.line,
                    "column": p.column,
                    "code": p.code,
                    "message": p.message,
                    "severity": p.severity,
                    "source": p.source,
                })

        errors = sum(1 for p in problems if p["severity"] == "error")
        warnings = sum(1 for p in problems if p["severity"] == "warning")
        return {
            "problems": problems,
            "counts": {
                "error": errors,
                "warning": warnings,
                "total": len(problems),
            },
            "has_run": bool(getattr(panel, "_has_run", False)),
        }

    def _do_list_linters(self) -> dict:
        from .linter_registry import get_linters
        from .settings_service import SettingsService

        panel = getattr(self.window, "problems_panel", None)
        service = panel.service if panel is not None else None
        settings = SettingsService.get_instance()

        linters = []
        for linter in get_linters():
            entry = {
                "id": linter.id,
                "name": linter.name,
                "enabled": settings.get(f"linters.{linter.id}_enabled", linter.default_enabled),
                "extensions": list(linter.extensions),
                "install_kind": linter.install.kind,
            }
            if service is not None:
                entry["status"] = service.status(linter.id)
                entry["has_files"] = service.project_has_files(linter)
                entry["install_hint"] = service.terminal_install_command(linter)
            linters.append(entry)
        return {"linters": linters}

    def _do_run_linter(self, linter_id: str, paths: list[str] | None) -> dict:
        from .linter_registry import get_linter

        panel = getattr(self.window, "problems_panel", None)
        if panel is None:
            return {"error": "Problems panel not available"}
        linter = get_linter(linter_id)
        if linter is None:
            return {"error": f"Unknown linter: {linter_id}"}

        service = panel.service
        problems = service.run_linter(linter, paths)  # blocking; on the worker thread
        result = [
            {"file": p.file, "line": p.line, "column": p.column, "code": p.code,
             "message": p.message, "severity": p.severity, "source": p.source}
            for p in problems
        ]
        errors = sum(1 for p in result if p["severity"] == "error")
        warnings = sum(1 for p in result if p["severity"] == "warning")
        # Refresh the panel on the main thread so the human sees the current state.
        try:
            self.call_on_main(lambda: (panel.refresh(), None)[1])
        except Exception:
            pass
        return {
            "linter": linter_id,
            "status": service.status(linter_id),
            "install_hint": service.terminal_install_command(linter),
            "problems": result,
            "counts": {"error": errors, "warning": warnings, "total": len(result)},
        }

    def _do_list_tasks(self) -> dict:
        panel = getattr(self.window, "tasks_panel", None)
        if panel is None:
            return {"tasks": [], "has_tasks_file": False}

        service = panel.service
        service.load()
        tasks = [
            {
                "label": t.label,
                "command": t.command,
                "type": t.type,
                "group": t.group,
            }
            for t in service.get_tasks()
        ]
        return {"tasks": tasks, "has_tasks_file": service.has_tasks_file()}

    # -- main-thread tool bodies: UI-mutating ------------------------------- #
    def _do_open_file(self, path: str, line: int | None, end_line: int | None) -> dict:
        project_root = Path(self.window.project_path)
        p = Path(path).expanduser()
        resolved = str(p if p.is_absolute() else project_root / p)
        if not Path(resolved).is_file():
            return {"ok": False, "error": f"file not found: {resolved}"}

        win = self.window
        win._on_file_activated(None, resolved)
        # Defer navigation so the freshly-created editor is realized first
        # (mirrors _on_search_open_file_at_line).
        if line is not None and end_line is not None:
            GLib.idle_add(win._select_lines_in_editor, resolved, line, end_line)
        elif line is not None:
            GLib.idle_add(win._go_to_line_in_editor, resolved, line)
        return {"ok": True, "path": resolved}

    def _do_show_diff(self, path: str) -> dict:
        win = self.window
        if getattr(win, "git_service", None) is None:
            return {"ok": False, "error": "no git repository"}

        project_root = Path(win.project_path)
        p = Path(path).expanduser()
        if p.is_absolute():
            try:
                rel = str(p.relative_to(project_root))
            except ValueError:
                return {"ok": False, "error": "path is outside the project"}
        else:
            rel = path

        try:
            win._on_git_file_clicked(None, rel, False)
        except Exception as exc:  # noqa: BLE001 - surfaced as a tool error, not a hang
            return {"ok": False, "error": f"could not diff {rel}: {exc}"}
        return {"ok": True, "path": rel}

    def _do_show_commit(self, commit_hash: str) -> dict:
        win = self.window
        git_service = getattr(win, "git_service", None)
        if git_service is None:
            return {"ok": False, "error": "no git repository"}

        commit = git_service.get_commit(commit_hash)
        if not commit:
            return {"ok": False, "error": f"commit not found: {commit_hash}"}

        win._on_commit_view_diff(None, commit_hash)
        return {"ok": True, "short_hash": commit.short_hash}

    # -- tool bodies: mutating --------------------------------------------- #
    def _do_create_issue(self, title: str, body: str) -> dict:
        # Lazy imports so mcp_server stays light and the GitHub stack only loads on use.
        from .git_service import AuthenticationRequired
        from .issues_service import GitHubError

        service = getattr(self.window, "issues_service", None)
        if service is None:
            return {"ok": False, "error": "issues service unavailable"}

        try:
            issue = service.create_issue(title, body)
        except AuthenticationRequired:
            return {"ok": False, "error": "GitHub authentication required"}
        except GitHubError as exc:
            return {"ok": False, "error": f"GitHub error: {exc}"}

        # Refresh the panel on the main thread (fast; it spawns its own load thread).
        def _refresh():
            panel = getattr(self.window, "issues_panel", None)
            if panel is not None:
                panel.refresh()
            return None

        try:
            self.call_on_main(_refresh)
        except Exception:  # noqa: BLE001 - refresh is best-effort; the issue was created
            pass

        return {"ok": True, "number": issue.number, "url": issue.html_url}

    def _do_run_task(self, name: str) -> dict:
        panel = getattr(self.window, "tasks_panel", None)
        if panel is None:
            return {"ok": False, "error": "tasks unavailable"}

        service = panel.service
        service.load()
        task = next((t for t in service.get_tasks() if t.label == name), None)
        if task is None:
            return {"ok": False, "error": f"task not found: {name}"}

        command = service.substitute_variables(task.command)
        self.window._on_task_run(None, task.label, command)
        return {"ok": True, "label": task.label}

    def _do_create_task(
        self, label: str, command: str, task_type: str,
        group: str | None, args: list[str] | None,
    ) -> dict:
        panel = getattr(self.window, "tasks_panel", None)
        if panel is None:
            return {"ok": False, "error": "tasks unavailable"}
        try:
            result = panel.service.add_task(label, command, task_type, group, args)
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        panel.refresh()  # reflect the new task in the GUI (also auto-refreshes via monitor)
        return {
            "ok": True,
            "result": result,  # "created" | "updated"
            "label": label.strip(),
            "tasks_file": str(panel.service.tasks_file),
        }

    def _do_add_note(self, name: str, content: str) -> dict:
        from ..utils.atomic_write import atomic_write_text

        notes_dir = Path(self.window.project_path) / "notes"
        notes_dir.mkdir(parents=True, exist_ok=True)
        filename = name if name.endswith(".md") else f"{name}.md"
        path = notes_dir / filename

        existing = path.read_text(encoding="utf-8") if path.is_file() else ""
        text = f"{existing}\n{content}" if existing else content
        atomic_write_text(path, text)
        return {"ok": True, "path": str(path)}

    def _do_set_session_summary(self, content: str, title: str) -> dict:
        from . import session_summary_service

        path = session_summary_service.save(self.window.project_path, content, title)
        return {"ok": True, "path": str(path)}

    def _do_get_session_summary(self) -> dict:
        from . import session_summary_service

        summary = session_summary_service.load(self.window.project_path)
        if summary is None:
            return {"ok": True, "exists": False}
        return {"ok": True, "exists": True, **summary}

    # -- cross-project catalog (read-only) ---------------------------------- #
    def _do_list_projects(self) -> dict:
        from . import project_catalog

        try:
            current = str(Path(self.window.project_path).resolve())
        except OSError:
            current = str(self.window.project_path)

        projects = []
        for entry in project_catalog.list_catalog():
            item = entry.as_dict()
            # Catalog paths are already canonicalized the same way -> equality holds.
            item["is_current"] = entry.local_path == current
            projects.append(item)
        return {"projects": projects, "count": len(projects)}

    def _do_resolve_project(self, hint: str) -> dict:
        from . import project_catalog

        return project_catalog.resolve(hint)

    # -- inter-project messages (mailbox) ----------------------------------- #
    def _current_remote(self) -> str | None:
        """This project's canonical git remote (the messaging address), or None."""
        remote = getattr(self.window, "_project_remote", None)
        if remote:
            return remote
        from ..utils.project_identity import resolve_message_address

        return resolve_message_address(self.window.project_path)

    @staticmethod
    def _thread_dict(thread) -> dict:
        return {
            "thread_id": thread.thread_id,
            "subject": thread.subject,
            "from": thread.from_project,
            "to": thread.to_project,
            "status": thread.status,
            "created_at": thread.created_at,
            "last_activity": thread.last_activity,
            "refs": list(thread.refs),
            "body": thread.body,
            "comments": [
                {"actor": c.actor, "body": c.body, "ts": c.ts} for c in thread.comments
            ],
        }

    def _refresh_messages_ui(self):
        """Best-effort main-thread refresh of the Messages panel + badge after a write."""
        def _refresh():
            panel = getattr(self.window, "messages_panel", None)
            if panel is not None:
                panel.refresh()
            if hasattr(self.window, "_update_messages_badge"):
                self.window._update_messages_badge()
            return None

        try:
            self.call_on_main(_refresh)
        except Exception:  # noqa: BLE001 - refresh is best-effort; the write succeeded
            pass

    def _do_send_message(self, to: str, subject: str, body: str,
                         refs: list[str] | None) -> dict:
        from . import message_store, project_catalog

        me = self._current_remote()
        if not me:
            return {"ok": False, "error": "this project has no git remote; messages require a remote"}

        result = project_catalog.resolve(to)
        if result.get("ambiguous"):
            return {"ok": False, "error": f"ambiguous recipient: {to}",
                    "candidates": result.get("candidates", [])}
        match = result.get("match")
        if match:
            # A worktree entry addresses to its sub-address; fall back to the bare remote.
            recipient = match.get("message_address") or match.get("remote_url")
            if not recipient:
                return {"ok": False,
                        "error": f"'{to}' is a local-only project (no remote to address)"}
        elif "/" in to:
            recipient = to  # a canonical remote not registered locally (lives elsewhere)
        else:
            return {"ok": False, "error": f"unknown recipient: {to}"}

        try:
            thread = message_store.create_thread(me, recipient, subject, body, refs)
        except message_store.MessageStoreError as exc:
            return {"ok": False, "error": str(exc)}
        self._refresh_messages_ui()
        return {"ok": True, "thread_id": thread.thread_id, "to": recipient}

    def _do_list_messages(self, box: str, status: str | None) -> dict:
        from . import message_store

        me = self._current_remote()
        if not me:
            return {"ok": True, "count": 0, "messages": []}
        threads = message_store.threads_for(me, box=box, status=status)
        messages = [self._thread_dict(t) for t in threads]
        return {"ok": True, "count": len(messages), "messages": messages}

    # -- worktree completion reports (Stage 5) ----------------------------
    def _do_report_worktree_complete(self, summary: str, tests: str, review: str) -> dict:
        from . import worktree_reports
        from .git_service import GitService

        if not getattr(self.window, "_is_worktree", False):
            return {"ok": False,
                    "error": "not a worktree — report_worktree_complete only works in a worktree window"}
        parent = getattr(self.window, "_worktree_parent", None)
        if parent is None:
            return {"ok": False, "error": "could not resolve the parent project"}
        branch = GitService(self.window.project_path).get_branch_name()
        worktree_reports.write_report(
            str(parent), branch, str(self.window.project_path), summary, tests, review
        )
        return {"ok": True, "branch": branch}

    def _do_list_worktree_reports(self) -> dict:
        from . import worktree_reports

        reports = worktree_reports.list_reports(str(self.window.project_path))
        slim = [
            {"branch": r["branch"], "worktree_path": r["worktree_path"],
             "tests": r["tests"], "created": r["created"], "body": r["body"]}
            for r in reports
        ]
        return {"ok": True, "count": len(slim), "reports": slim}

    def _do_resolve_worktree_report(self, branch: str) -> dict:
        from . import worktree_reports

        ok = worktree_reports.resolve_report(str(self.window.project_path), branch)
        return {"ok": ok, "branch": branch}

    # -- worktree orchestration (Stage 6) --------------------------------
    def _do_list_worktrees(self) -> dict:
        from .git_service import GitService
        from ..utils.git_worktree import is_linked_worktree

        entries = GitService(self.window.project_path).list_worktrees()
        out = []
        for entry in entries:
            path = entry.get("path")
            if not path or not is_linked_worktree(path):
                continue  # skip the main checkout (and bare)
            st = GitService(path)._run_git(["status", "--porcelain"])
            entry["dirty"] = bool(st.stdout.strip()) if st.returncode == 0 else False
            out.append(entry)
        return {"ok": True, "count": len(out), "worktrees": out}

    def _do_create_worktree(self, task_name: str, branch: str, base: str) -> dict:
        from pathlib import Path
        from .git_service import GitService
        from .project_registry import ProjectRegistry
        from ..utils.git_worktree import slugify

        slug = slugify(task_name) if task_name.strip() else ""
        branch = branch.strip() or (f"feature/{slug}" if slug else "")
        if not branch:
            return {"ok": False, "error": "task_name (or an explicit branch) is required"}
        main = Path(self.window.project_path)
        wt_path = main.parent / f"{main.name}--{slug or slugify(branch)}"
        if wt_path.exists():
            return {"ok": False, "error": f"folder already exists: {wt_path}"}
        try:
            GitService(main).add_worktree(str(wt_path), branch, base or None)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}
        ProjectRegistry().register_project(str(wt_path))
        return {"ok": True, "path": str(wt_path), "branch": branch}

    def _do_preview_merge(self, branch: str) -> dict:
        from .git_service import GitService

        try:
            clean, conflicts = GitService(self.window.project_path).preview_merge(branch)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "clean": clean, "conflicts": conflicts}

    def _do_merge_worktree(self, branch: str) -> dict:
        from .git_service import GitService
        from . import worktree_reports

        gs = GitService(self.window.project_path)
        try:
            clean, conflicts = gs.preview_merge(branch)
            if not clean:
                return {"ok": False, "error": "conflicts — resolve in the worktree",
                        "conflicts": conflicts}
            gs.merge_branch(branch)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)}
        worktree_reports.resolve_report(str(self.window.project_path), branch)
        return {"ok": True, "branch": branch}

    def _do_reply_message(self, thread_id: str, body: str) -> dict:
        from . import message_store

        me = self._current_remote()
        if not me:
            return {"ok": False, "error": "this project has no git remote; messages require a remote"}
        if message_store.load_thread(thread_id) is None:
            return {"ok": False, "error": f"thread not found: {thread_id}"}
        try:
            message_store.add_comment(thread_id, me, body)
        except message_store.MessageStoreError as exc:
            return {"ok": False, "error": str(exc)}
        self._refresh_messages_ui()
        return {"ok": True, "thread_id": thread_id}

    def _do_resolve_message(self, thread_id: str, status: str) -> dict:
        from . import message_store

        me = self._current_remote()
        if not me:
            return {"ok": False, "error": "this project has no git remote; messages require a remote"}
        if status not in message_store.STATUSES:
            return {"ok": False, "error": f"invalid status: {status}"}
        if message_store.load_thread(thread_id) is None:
            return {"ok": False, "error": f"thread not found: {thread_id}"}
        message_store.set_status(thread_id, me, status)
        self._refresh_messages_ui()
        return {"ok": True, "thread_id": thread_id, "status": status}

    # -- /refresh endpoint -------------------------------------------------- #
    def _do_refresh(self, target: str) -> list[str]:
        """Worker-thread entry for POST /refresh; marshals the work to the main loop."""
        return self.call_on_main(lambda: self._refresh_targets(target))

    def _refresh_targets(self, target: str) -> list[str]:
        """Refresh the panels matching ``target`` on the GTK main thread.

        ``target`` is one of ``git`` / ``issues`` / ``notes`` / ``all`` (default).
        Every panel is guarded — some are built lazily. Returns the keys refreshed.
        """
        win = self.window
        want_all = target == "all"
        refreshed: list[str] = []

        def _refresh(key: str, panel_attr: str):
            panel = getattr(win, panel_attr, None)
            if panel is not None:
                panel.refresh()
                refreshed.append(key)

        if want_all or target == "git":
            _refresh("git_changes", "git_changes_panel")
            _refresh("git_history", "git_history_panel")
        if want_all or target == "issues":
            _refresh("issues", "issues_panel")
        if want_all or target == "notes":
            _refresh("notes", "notes_panel")

        return refreshed

    # -- GUI harness tool bodies (worker thread; subprocess I/O, no GTK) ---- #
    def _do_gui_launch(self, cmd: str, width: int, height: int) -> dict:
        try:
            handle = self.gui.launch(cmd, width, height)
        except (GuiHarnessError, OSError) as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "handle": handle}

    def _do_gui_screenshot(self, handle: str):
        try:
            png = self.gui.screenshot(handle)
        except (GuiHarnessError, OSError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}
        return Image(data=png, format="png")

    def _do_gui_stop(self, handle: str) -> dict:
        try:
            self.gui.stop(handle)
        except GuiHarnessError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True}

    def _do_gui_snapshot_tree(self, handle: str) -> dict:
        try:
            tree = self.gui.snapshot_tree(handle)
        except (GuiHarnessError, OSError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "tree": tree}

    def _do_gui_action(self, handle: str, kind: str, role, name, action, text,
                       nth: int = 0) -> dict:
        try:
            if kind == "click":
                self.gui.click(handle, role, name, nth=nth)
            elif kind == "type":
                self.gui.type_text(handle, role, name, text, nth=nth)
            else:
                self.gui.do_action(handle, role, name, action, nth=nth)
        except (GuiHarnessError, OSError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True}

    def _do_gui_input(self, handle: str, req: dict) -> dict:
        try:
            if req["kind"] == "pointer":
                self.gui.pointer(handle, req["x"], req["y"], req["button"],
                                 req["action"], req["dy"])
            else:
                self.gui.key(handle, combo=req.get("combo"), text=req.get("text"))
        except (GuiHarnessError, OSError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True}
