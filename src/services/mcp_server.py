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
from mcp.server.fastmcp import FastMCP  # noqa: E402
from starlette.concurrency import run_in_threadpool  # noqa: E402

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
            """Return the current text selection in the active editor.

            Reports the file path, the selected range (1-based lines, 0-based
            columns), and the selected text. ``has_selection`` is false when nothing
            is selected or the active tab is not an editor.
            """
            return self.call_on_main(self._do_get_selection)

        @mcp.tool()
        def get_problems(path: str | None = None) -> dict:
            """Return the current ruff/mypy findings shown in the Problems panel.

            Read-only snapshot of the panel's cached results; it does not re-run the
            linters. ``has_run`` is false until the linters have completed at least
            once (open the Problems tab to populate them). Pass ``path`` to filter to
            a single file.
            """
            return self.call_on_main(lambda: self._do_get_problems(path))

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
        def add_note(name: str, content: str) -> dict:
            """Create or append to a markdown note under ``notes/<name>.md``.

            Returns ``{"ok": bool, "path"?, "error"?}``.
            """
            # Filesystem-only; safe to run on the worker thread. The notes panel
            # auto-refreshes via FileMonitorService.
            return self._do_add_note(name, content)

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
                    entry["dirty"] = bool(child.is_modified())
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

    def _active_editor(self):
        """Return the FileEditor in the active tab, or None if it isn't an editor."""
        tab_view = getattr(self.window, "tab_view", None)
        if tab_view is None:
            return None
        page = tab_view.get_selected_page()
        if page is None:
            return None
        child = page.get_child()
        # Non-editor tabs (terminals, diff/detail views) also live in the tab view.
        if not hasattr(child, "buffer") or not hasattr(child, "file_path"):
            return None
        return child

    def _do_get_selection(self) -> dict:
        empty = {
            "path": None,
            "has_selection": False,
            "start_line": None,
            "start_column": None,
            "end_line": None,
            "end_column": None,
            "text": None,
            "truncated": False,
        }
        editor = self._active_editor()
        if editor is None:
            return empty

        path = editor.file_path
        buffer = editor.buffer
        if not buffer.get_has_selection():
            return {**empty, "path": path}

        start, end = buffer.get_selection_bounds()
        text = buffer.get_text(start, end, False)
        truncated = len(text) > _MAX_SELECTION_CHARS
        if truncated:
            text = text[:_MAX_SELECTION_CHARS]

        return {
            "path": path,
            "has_selection": True,
            "start_line": start.get_line() + 1,
            "start_column": start.get_line_offset(),
            "end_line": end.get_line() + 1,
            "end_column": end.get_line_offset(),
            "text": text,
            "truncated": truncated,
        }

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
