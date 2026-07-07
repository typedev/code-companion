"""MCP control-surface tests (Part A, increment 1).

Locks in the A1/A2 acceptance criteria so they don't regress:
- bearer-token auth rejects missing/wrong tokens (401);
- an ``initialize`` round-trip reaches FastMCP and returns our server info;
- ``stop()`` frees the port (window-close criterion);
- ``call_on_main`` marshals results, times out instead of hanging, and forwards
  exceptions;
- the read-only tool body reflects the (fake) window state.
"""

import socket
import threading
import time

import gi
import httpx
import pytest

gi.require_version("Gtk", "4.0")
from gi.repository import GLib  # noqa: E402

from src.services.mcp_server import McpServer  # noqa: E402


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #
class _FakeTabView:
    def __init__(self, pages=None, selected=None):
        self._pages = pages or []
        self._selected = selected

    def get_selected_page(self):
        return self._selected

    def get_n_pages(self):
        return len(self._pages)

    def get_nth_page(self, i):
        return self._pages[i]


class _FakeWindow:
    def __init__(self, tab_view=None, active=True, problems_panel=None, tasks_panel=None):
        self.tab_view = tab_view
        self._active = active
        if problems_panel is not None:
            self.problems_panel = problems_panel
        if tasks_panel is not None:
            self.tasks_panel = tasks_panel

    def is_active(self):
        return self._active

    def get_application(self):
        return None


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _wait_until_listening(port: int, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.02)
    raise TimeoutError(f"server never listened on {port}")


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture
def running_server():
    """A started McpServer with a fake empty window; token + base URL provided."""
    srv = McpServer(_FakeWindow(_FakeTabView()))
    port = _free_port()
    token = "test-token-123"
    srv.start(port, token)
    _wait_until_listening(port)
    base = f"http://127.0.0.1:{port}/mcp"
    try:
        yield srv, base, token
    finally:
        srv.stop()


# --------------------------------------------------------------------------- #
# Auth
# --------------------------------------------------------------------------- #
def test_rejects_without_token(running_server):
    _srv, base, _token = running_server
    r = httpx.get(base)
    assert r.status_code == 401


def test_rejects_wrong_token(running_server):
    _srv, base, _token = running_server
    r = httpx.get(base, headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401


# --------------------------------------------------------------------------- #
# Round-trip
# --------------------------------------------------------------------------- #
def test_initialize_round_trip(running_server):
    _srv, base, token = running_server
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "pytest", "version": "0"},
        },
    }
    r = httpx.post(
        base,
        json=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json, text/event-stream",
        },
    )
    assert r.status_code == 200
    # Body carries our FastMCP server name regardless of JSON vs SSE framing.
    assert "code-companion" in r.text


# --------------------------------------------------------------------------- #
# Lifecycle
# --------------------------------------------------------------------------- #
def test_stop_frees_port():
    srv = McpServer(_FakeWindow(_FakeTabView()))
    port = _free_port()
    srv.start(port, "tok")
    _wait_until_listening(port)
    srv.stop()
    # The port must be rebindable immediately after a clean stop.
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", port))
    finally:
        s.close()


def test_start_is_idempotent(running_server):
    srv, _base, _token = running_server
    thread = srv._thread
    srv.start(_free_port(), "other")  # second start is a no-op
    assert srv._thread is thread


# --------------------------------------------------------------------------- #
# call_on_main marshaling
# --------------------------------------------------------------------------- #
def _with_glib_loop(fn):
    """Run ``fn`` while a GLib main loop services the default context."""
    loop = GLib.MainLoop()
    t = threading.Thread(target=loop.run, daemon=True)
    t.start()
    try:
        # Give the loop a moment to start iterating.
        time.sleep(0.05)
        return fn()
    finally:
        loop.quit()
        t.join(2)


def test_call_on_main_returns_result():
    srv = McpServer(_FakeWindow())
    result = _with_glib_loop(lambda: srv.call_on_main(lambda: 21 * 2))
    assert result == 42


def test_call_on_main_times_out_without_loop():
    srv = McpServer(_FakeWindow())
    # No GLib loop is iterating the default context -> the idle never runs.
    with pytest.raises(TimeoutError):
        srv.call_on_main(lambda: "never", timeout=0.3)


def test_call_on_main_forwards_exception():
    srv = McpServer(_FakeWindow())

    def boom():
        raise ValueError("kaboom")

    with pytest.raises(ValueError, match="kaboom"):
        _with_glib_loop(lambda: srv.call_on_main(boom))


# --------------------------------------------------------------------------- #
# Read-only tool body
# --------------------------------------------------------------------------- #
class _FakePage:
    def __init__(self, title, child):
        self._title = title
        self._child = child

    def get_title(self):
        return self._title

    def get_child(self):
        return self._child


class _FakeEditor:
    def __init__(self, path, modified=False, line=None):
        self.file_path = path
        self._modified = modified
        self._line = line

    def is_modified(self):
        return self._modified

    def _get_cursor_line(self):
        return self._line


def test_get_workspace_state_empty():
    srv = McpServer(_FakeWindow(_FakeTabView()))
    state = srv._do_get_workspace_state()
    assert state == {"active_file": None, "cursor_line": None, "open_tabs": []}


def test_get_workspace_state_reports_active_and_dirty():
    editor = _FakeEditor("/proj/a.py", modified=True, line=12)
    other = _FakeEditor("/proj/b.py", modified=False, line=1)
    page_a = _FakePage("a.py", editor)
    page_b = _FakePage("b.py", other)
    tab_view = _FakeTabView(pages=[page_a, page_b], selected=page_a)

    srv = McpServer(_FakeWindow(tab_view))
    state = srv._do_get_workspace_state()

    assert state["active_file"] == "/proj/a.py"
    assert state["cursor_line"] == 12
    assert state["open_tabs"] == [
        {"title": "a.py", "path": "/proj/a.py", "dirty": True},
        {"title": "b.py", "path": "/proj/b.py", "dirty": False},
    ]


# --------------------------------------------------------------------------- #
# get_selection
# --------------------------------------------------------------------------- #
class _FakeIter:
    def __init__(self, line, offset):  # line/offset are 0-based (GTK convention)
        self._line = line
        self._offset = offset

    def get_line(self):
        return self._line

    def get_line_offset(self):
        return self._offset


class _FakeBuffer:
    def __init__(self, text=None, start=None, end=None):
        self._text = text
        self._start = start
        self._end = end

    def get_has_selection(self):
        return self._text is not None

    def get_selection_bounds(self):
        return (self._start, self._end)

    def get_text(self, start, end, include_hidden):
        return self._text


class _EditorTab:
    """A tab child that looks like a FileEditor (has ``buffer`` + ``file_path``)."""

    def __init__(self, file_path, buffer):
        self.file_path = file_path
        self.buffer = buffer


class _NonEditorTab:
    """A tab child with neither ``buffer`` nor ``file_path`` (e.g. a terminal)."""


def test_get_selection_no_editor_tab():
    page = _FakePage("term", _NonEditorTab())
    tab_view = _FakeTabView(pages=[page], selected=page)
    srv = McpServer(_FakeWindow(tab_view))

    result = srv._do_get_selection()
    assert result["path"] is None
    assert result["has_selection"] is False
    assert result["text"] is None


def test_get_selection_with_selection():
    buffer = _FakeBuffer(
        text="hello\nworld",
        start=_FakeIter(2, 4),   # line 3, column 4
        end=_FakeIter(5, 0),     # line 6, column 0
    )
    page = _FakePage("a.py", _EditorTab("/proj/a.py", buffer))
    tab_view = _FakeTabView(pages=[page], selected=page)
    srv = McpServer(_FakeWindow(tab_view))

    result = srv._do_get_selection()
    assert result == {
        "path": "/proj/a.py",
        "has_selection": True,
        "start_line": 3,
        "start_column": 4,
        "end_line": 6,
        "end_column": 0,
        "text": "hello\nworld",
        "truncated": False,
    }


def test_get_selection_without_selection_reports_path():
    page = _FakePage("a.py", _EditorTab("/proj/a.py", _FakeBuffer(text=None)))
    tab_view = _FakeTabView(pages=[page], selected=page)
    srv = McpServer(_FakeWindow(tab_view))

    result = srv._do_get_selection()
    assert result["path"] == "/proj/a.py"
    assert result["has_selection"] is False
    assert result["start_line"] is None


def test_get_selection_truncates_large_text():
    from src.services.mcp_server import _MAX_SELECTION_CHARS

    big = "x" * (_MAX_SELECTION_CHARS + 100)
    buffer = _FakeBuffer(text=big, start=_FakeIter(0, 0), end=_FakeIter(0, 0))
    page = _FakePage("a.py", _EditorTab("/proj/a.py", buffer))
    tab_view = _FakeTabView(pages=[page], selected=page)
    srv = McpServer(_FakeWindow(tab_view))

    result = srv._do_get_selection()
    assert result["truncated"] is True
    assert len(result["text"]) == _MAX_SELECTION_CHARS


# --------------------------------------------------------------------------- #
# get_problems
# --------------------------------------------------------------------------- #
class _FakeProblem:
    def __init__(self, file, line, column, code, message, severity, source):
        self.file = file
        self.line = line
        self.column = column
        self.code = code
        self.message = message
        self.severity = severity
        self.source = source


class _FakeFileProblems:
    def __init__(self, problems):
        self.problems = problems


class _FakeProblemsPanel:
    def __init__(self, problems_by_file=None, has_run=False):
        self._problems = problems_by_file or {}
        self._has_run = has_run


def _problem(file, code, severity, line=1):
    return _FakeProblem(file, line, 0, code, f"{code} msg", severity, "ruff")


def test_get_problems_empty_not_run():
    panel = _FakeProblemsPanel(has_run=False)
    srv = McpServer(_FakeWindow(problems_panel=panel))

    result = srv._do_get_problems(None)
    assert result["problems"] == []
    assert result["counts"] == {"error": 0, "warning": 0, "total": 0}
    assert result["has_run"] is False


def test_get_problems_populated_counts():
    panel = _FakeProblemsPanel(
        problems_by_file={
            "/proj/a.py": _FakeFileProblems([
                _problem("/proj/a.py", "E501", "error"),
                _problem("/proj/a.py", "F401", "warning"),
            ]),
            "/proj/b.py": _FakeFileProblems([
                _problem("/proj/b.py", "E302", "error"),
            ]),
        },
        has_run=True,
    )
    srv = McpServer(_FakeWindow(problems_panel=panel))

    result = srv._do_get_problems(None)
    assert result["has_run"] is True
    assert result["counts"] == {"error": 2, "warning": 1, "total": 3}
    assert {p["code"] for p in result["problems"]} == {"E501", "F401", "E302"}


def test_get_problems_filters_by_path():
    panel = _FakeProblemsPanel(
        problems_by_file={
            "/proj/a.py": _FakeFileProblems([_problem("/proj/a.py", "E501", "error")]),
            "/proj/b.py": _FakeFileProblems([_problem("/proj/b.py", "E302", "error")]),
        },
        has_run=True,
    )
    srv = McpServer(_FakeWindow(problems_panel=panel))

    result = srv._do_get_problems("/proj/a.py")
    assert result["counts"]["total"] == 1
    assert result["problems"][0]["file"] == "/proj/a.py"


def test_get_problems_no_panel():
    srv = McpServer(_FakeWindow())
    result = srv._do_get_problems(None)
    assert result == {
        "problems": [],
        "counts": {"error": 0, "warning": 0, "total": 0},
        "has_run": False,
    }


# --------------------------------------------------------------------------- #
# list_tasks
# --------------------------------------------------------------------------- #
class _FakeTask:
    def __init__(self, label, command, type="shell", group=None):
        self.label = label
        self.command = command
        self.type = type
        self.group = group


class _FakeTasksService:
    def __init__(self, tasks=None, has_file=True):
        self._tasks = tasks or []
        self._has_file = has_file
        self.loaded = False

    def load(self):
        self.loaded = True
        return self._has_file

    def get_tasks(self):
        return list(self._tasks)

    def has_tasks_file(self):
        return self._has_file


class _FakeTasksPanel:
    def __init__(self, service):
        self.service = service


def test_list_tasks_no_file():
    panel = _FakeTasksPanel(_FakeTasksService(tasks=[], has_file=False))
    srv = McpServer(_FakeWindow(tasks_panel=panel))

    result = srv._do_list_tasks()
    assert result == {"tasks": [], "has_tasks_file": False}
    assert panel.service.loaded is True


def test_list_tasks_with_tasks():
    service = _FakeTasksService(
        tasks=[
            _FakeTask("Run", "pytest", group="test"),
            _FakeTask("Build", "make", type="process"),
        ]
    )
    srv = McpServer(_FakeWindow(tasks_panel=_FakeTasksPanel(service)))

    result = srv._do_list_tasks()
    assert result["has_tasks_file"] is True
    assert result["tasks"] == [
        {"label": "Run", "command": "pytest", "type": "shell", "group": "test"},
        {"label": "Build", "command": "make", "type": "process", "group": None},
    ]


def test_list_tasks_no_panel():
    srv = McpServer(_FakeWindow())
    assert srv._do_list_tasks() == {"tasks": [], "has_tasks_file": False}


# --------------------------------------------------------------------------- #
# UI-mutating tools: open_file / show_diff / show_commit
# --------------------------------------------------------------------------- #
_MISSING = object()


class _FakeCommit:
    def __init__(self, short_hash):
        self.short_hash = short_hash


class _FakeGitService:
    def __init__(self, commits=None, diff_raises_on=None):
        self._commits = commits or {}         # hash -> _FakeCommit
        self._diff_raises_on = diff_raises_on  # rel path that should raise

    def get_commit(self, commit_hash):
        return self._commits.get(commit_hash)


class _RecordingWindow:
    """A window that records which UI handlers were invoked (and with what)."""

    def __init__(self, project_path, git_service=_MISSING):
        self.project_path = project_path
        self.calls = []
        if git_service is not _MISSING:
            self.git_service = git_service

    def _on_file_activated(self, tree, path):
        self.calls.append(("open", path))

    def _go_to_line_in_editor(self, path, line):
        self.calls.append(("goto", path, line))

    def _select_lines_in_editor(self, path, start, end):
        self.calls.append(("range", path, start, end))

    def _on_git_file_clicked(self, panel, path, staged):
        gs = getattr(self, "git_service", None)
        if gs is not None and gs._diff_raises_on == path:
            raise KeyError(path)
        self.calls.append(("diff", path, staged))

    def _on_commit_view_diff(self, panel, commit_hash):
        self.calls.append(("commit", commit_hash))


@pytest.fixture
def sync_idle_add(monkeypatch):
    """Make GLib.idle_add run its callback synchronously so tests are deterministic."""
    from src.services import mcp_server as mod

    def run_now(fn, *args):
        fn(*args)
        return False

    monkeypatch.setattr(mod.GLib, "idle_add", run_now)


# -- open_file ------------------------------------------------------------- #
def test_open_file_relative_resolves_and_opens(tmp_path, sync_idle_add):
    (tmp_path / "a.py").write_text("x = 1\n")
    win = _RecordingWindow(str(tmp_path))
    srv = McpServer(win)

    result = srv._do_open_file("a.py", None, None)
    resolved = str(tmp_path / "a.py")
    assert result == {"ok": True, "path": resolved}
    assert win.calls == [("open", resolved)]


def test_open_file_absolute_with_line(tmp_path, sync_idle_add):
    f = tmp_path / "a.py"
    f.write_text("x = 1\n")
    win = _RecordingWindow(str(tmp_path))
    srv = McpServer(win)

    result = srv._do_open_file(str(f), 3, None)
    assert result["ok"] is True
    assert win.calls == [("open", str(f)), ("goto", str(f), 3)]


def test_open_file_with_range(tmp_path, sync_idle_add):
    f = tmp_path / "a.py"
    f.write_text("x = 1\n")
    win = _RecordingWindow(str(tmp_path))
    srv = McpServer(win)

    result = srv._do_open_file("a.py", 2, 5)
    resolved = str(tmp_path / "a.py")
    assert result["ok"] is True
    assert win.calls == [("open", resolved), ("range", resolved, 2, 5)]


def test_open_file_missing_returns_error(tmp_path, sync_idle_add):
    win = _RecordingWindow(str(tmp_path))
    srv = McpServer(win)

    result = srv._do_open_file("nope.py", None, None)
    assert result["ok"] is False
    assert "not found" in result["error"]
    assert win.calls == []  # UI untouched


# -- show_diff ------------------------------------------------------------- #
def test_show_diff_relative(tmp_path):
    win = _RecordingWindow(str(tmp_path), git_service=_FakeGitService())
    srv = McpServer(win)

    result = srv._do_show_diff("src/a.py")
    assert result == {"ok": True, "path": "src/a.py"}
    assert win.calls == [("diff", "src/a.py", False)]


def test_show_diff_absolute_inside_project(tmp_path):
    win = _RecordingWindow(str(tmp_path), git_service=_FakeGitService())
    srv = McpServer(win)

    abs_path = str(tmp_path / "src" / "a.py")
    result = srv._do_show_diff(abs_path)
    assert result == {"ok": True, "path": "src/a.py"}


def test_show_diff_absolute_outside_project(tmp_path):
    win = _RecordingWindow(str(tmp_path), git_service=_FakeGitService())
    srv = McpServer(win)

    result = srv._do_show_diff("/etc/passwd")
    assert result["ok"] is False
    assert "outside" in result["error"]


def test_show_diff_no_git():
    win = _RecordingWindow("/proj")  # no git_service attribute
    srv = McpServer(win)

    result = srv._do_show_diff("a.py")
    assert result == {"ok": False, "error": "no git repository"}


def test_show_diff_handler_error_is_caught(tmp_path):
    gs = _FakeGitService(diff_raises_on="bad.py")
    win = _RecordingWindow(str(tmp_path), git_service=gs)
    srv = McpServer(win)

    result = srv._do_show_diff("bad.py")
    assert result["ok"] is False
    assert "could not diff" in result["error"]


# -- show_commit ----------------------------------------------------------- #
def test_show_commit_found():
    gs = _FakeGitService(commits={"abc123": _FakeCommit("abc123")})
    win = _RecordingWindow("/proj", git_service=gs)
    srv = McpServer(win)

    result = srv._do_show_commit("abc123")
    assert result == {"ok": True, "short_hash": "abc123"}
    assert win.calls == [("commit", "abc123")]


def test_show_commit_not_found():
    win = _RecordingWindow("/proj", git_service=_FakeGitService())
    srv = McpServer(win)

    result = srv._do_show_commit("deadbeef")
    assert result["ok"] is False
    assert "not found" in result["error"]
    assert win.calls == []  # UI untouched


def test_show_commit_no_git():
    win = _RecordingWindow("/proj")  # no git_service attribute
    srv = McpServer(win)

    result = srv._do_show_commit("abc123")
    assert result == {"ok": False, "error": "no git repository"}


# --------------------------------------------------------------------------- #
# Mutating tools: create_issue / run_task / add_note
# --------------------------------------------------------------------------- #
class _FakeIssue:
    def __init__(self, number, html_url):
        self.number = number
        self.html_url = html_url


class _FakeIssuesService:
    def __init__(self, issue=None, raises=None):
        self._issue = issue
        self._raises = raises
        self.calls = []

    def create_issue(self, title, body="", credentials=None):
        self.calls.append((title, body))
        if self._raises is not None:
            raise self._raises
        return self._issue


class _FakeIssuesPanel:
    def __init__(self):
        self.refreshed = 0

    def refresh(self, credentials=None):
        self.refreshed += 1


class _IssuesWindow:
    def __init__(self, issues_service, issues_panel=_MISSING):
        self.issues_service = issues_service
        if issues_panel is not _MISSING:
            self.issues_panel = issues_panel


def test_create_issue_success_refreshes(sync_idle_add):
    panel = _FakeIssuesPanel()
    service = _FakeIssuesService(issue=_FakeIssue(42, "https://gh/i/42"))
    srv = McpServer(_IssuesWindow(service, panel))

    result = srv._do_create_issue("Title", "Body")
    assert result == {"ok": True, "number": 42, "url": "https://gh/i/42"}
    assert service.calls == [("Title", "Body")]
    assert panel.refreshed == 1


def test_create_issue_success_without_panel(sync_idle_add):
    service = _FakeIssuesService(issue=_FakeIssue(7, "https://gh/i/7"))
    srv = McpServer(_IssuesWindow(service))  # no issues_panel attribute

    result = srv._do_create_issue("T", "")
    assert result == {"ok": True, "number": 7, "url": "https://gh/i/7"}


def test_create_issue_auth_error():
    from src.services.git_service import AuthenticationRequired

    service = _FakeIssuesService(raises=AuthenticationRequired("nope", "url"))
    srv = McpServer(_IssuesWindow(service))

    result = srv._do_create_issue("T", "B")
    assert result == {"ok": False, "error": "GitHub authentication required"}


def test_create_issue_github_error():
    from src.services.issues_service import GitHubError

    service = _FakeIssuesService(raises=GitHubError(500, "server boom"))
    srv = McpServer(_IssuesWindow(service))

    result = srv._do_create_issue("T", "B")
    assert result["ok"] is False
    assert "server boom" in result["error"]


def test_create_issue_no_service():
    srv = McpServer(_FakeWindow())  # no issues_service attribute
    result = srv._do_create_issue("T", "B")
    assert result == {"ok": False, "error": "issues service unavailable"}


# -- run_task -------------------------------------------------------------- #
class _RunTaskService:
    def __init__(self, tasks):
        self._tasks = tasks
        self.loaded = False

    def load(self):
        self.loaded = True

    def get_tasks(self):
        return list(self._tasks)

    def substitute_variables(self, command, context=None):
        return command + " [subst]"


class _RunTaskWindow:
    def __init__(self, tasks):
        self.tasks_panel = _FakeTasksPanel(_RunTaskService(tasks))
        self.calls = []

    def _on_task_run(self, panel, label, command):
        self.calls.append((label, command))


def test_run_task_found_substitutes_and_runs():
    win = _RunTaskWindow([_FakeTask("Build", "make ${workspaceFolder}")])
    srv = McpServer(win)

    result = srv._do_run_task("Build")
    assert result == {"ok": True, "label": "Build"}
    assert win.calls == [("Build", "make ${workspaceFolder} [subst]")]


def test_run_task_label_not_found():
    win = _RunTaskWindow([_FakeTask("Build", "make")])
    srv = McpServer(win)

    result = srv._do_run_task("Nope")
    assert result["ok"] is False
    assert "not found" in result["error"]
    assert win.calls == []  # UI untouched


def test_run_task_no_panel():
    srv = McpServer(_FakeWindow())
    result = srv._do_run_task("Build")
    assert result == {"ok": False, "error": "tasks unavailable"}


# -- add_note -------------------------------------------------------------- #
def test_add_note_creates_new(tmp_path):
    srv = McpServer(_RecordingWindow(str(tmp_path)))

    result = srv._do_add_note("ideas", "first line")
    path = tmp_path / "notes" / "ideas.md"
    assert result == {"ok": True, "path": str(path)}
    assert path.read_text() == "first line"


def test_add_note_appends_to_existing(tmp_path):
    notes = tmp_path / "notes"
    notes.mkdir()
    (notes / "log.md").write_text("day one")
    srv = McpServer(_RecordingWindow(str(tmp_path)))

    result = srv._do_add_note("log.md", "day two")
    assert result["ok"] is True
    assert (notes / "log.md").read_text() == "day one\nday two"


def test_add_note_adds_md_suffix(tmp_path):
    srv = McpServer(_RecordingWindow(str(tmp_path)))
    srv._do_add_note("todo", "x")
    assert (tmp_path / "notes" / "todo.md").is_file()


# --------------------------------------------------------------------------- #
# /refresh endpoint
# --------------------------------------------------------------------------- #
class _RefreshPanel:
    def __init__(self):
        self.count = 0

    def refresh(self, *args, **kwargs):
        self.count += 1


class _RefreshWindow:
    def __init__(self, **panels):
        for name, panel in panels.items():
            setattr(self, name, panel)


def _refresh_window(include_issues=True):
    panels = {
        "git_changes_panel": _RefreshPanel(),
        "git_history_panel": _RefreshPanel(),
        "notes_panel": _RefreshPanel(),
    }
    if include_issues:
        panels["issues_panel"] = _RefreshPanel()
    return _RefreshWindow(**panels)


def test_refresh_targets_git_only():
    win = _refresh_window()
    srv = McpServer(win)

    assert srv._refresh_targets("git") == ["git_changes", "git_history"]
    assert win.git_changes_panel.count == 1
    assert win.git_history_panel.count == 1
    assert win.issues_panel.count == 0
    assert win.notes_panel.count == 0


def test_refresh_targets_issues_only():
    win = _refresh_window()
    srv = McpServer(win)
    assert srv._refresh_targets("issues") == ["issues"]
    assert win.issues_panel.count == 1
    assert win.git_changes_panel.count == 0


def test_refresh_targets_notes_only():
    win = _refresh_window()
    srv = McpServer(win)
    assert srv._refresh_targets("notes") == ["notes"]
    assert win.notes_panel.count == 1


def test_refresh_targets_all():
    win = _refresh_window()
    srv = McpServer(win)
    assert srv._refresh_targets("all") == [
        "git_changes", "git_history", "issues", "notes",
    ]


def test_refresh_targets_all_skips_missing_lazy_panel():
    win = _refresh_window(include_issues=False)  # issues_panel not built yet
    srv = McpServer(win)
    assert srv._refresh_targets("all") == ["git_changes", "git_history", "notes"]


def test_refresh_targets_unknown():
    win = _refresh_window()
    srv = McpServer(win)
    assert srv._refresh_targets("bogus") == []
    assert win.git_changes_panel.count == 0


@pytest.fixture
def refresh_server():
    """Server bound to a window with recording panels, with a GLib loop running so
    call_on_main resolves (the panels are plain Python, so no real GTK affinity)."""
    win = _refresh_window()
    srv = McpServer(win)
    port = _free_port()
    token = "refresh-token"
    srv.start(port, token)
    _wait_until_listening(port)
    loop = GLib.MainLoop()
    thread = threading.Thread(target=loop.run, daemon=True)
    thread.start()
    try:
        yield srv, f"http://127.0.0.1:{port}", token, win
    finally:
        loop.quit()
        thread.join(2)
        srv.stop()


def test_refresh_endpoint_ok(refresh_server):
    _srv, root, token, win = refresh_server
    r = httpx.post(
        root + "/refresh",
        headers={"Authorization": f"Bearer {token}"},
        json={"target": "git"},
    )
    assert r.status_code == 200
    assert set(r.json()["refreshed"]) == {"git_changes", "git_history"}
    assert win.git_changes_panel.count == 1


def test_refresh_endpoint_requires_token(refresh_server):
    _srv, root, _token, _win = refresh_server
    r = httpx.post(root + "/refresh", json={"target": "all"})
    assert r.status_code == 401


def test_refresh_endpoint_bad_json(refresh_server):
    _srv, root, token, _win = refresh_server
    r = httpx.post(
        root + "/refresh",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        content=b"not json",
    )
    assert r.status_code == 400


# --------------------------------------------------------------------------- #
# GUI harness tool bodies
# --------------------------------------------------------------------------- #
class _FakeGui:
    def __init__(self, handle="gui-1", png=b"PNGDATA", launch_error=None,
                 screenshot_error=None, stop_error=None, tree=None, tree_error=None,
                 action_error=None):
        self._handle = handle
        self._png = png
        self._launch_error = launch_error
        self._screenshot_error = screenshot_error
        self._stop_error = stop_error
        self._tree = tree if tree is not None else {"role": "application"}
        self._tree_error = tree_error
        self._action_error = action_error
        self.stopped = []
        self.actions = []

    def launch(self, cmd, width, height):
        if self._launch_error:
            raise self._launch_error
        return self._handle

    def screenshot(self, handle):
        if self._screenshot_error:
            raise self._screenshot_error
        return self._png

    def stop(self, handle):
        if self._stop_error:
            raise self._stop_error
        self.stopped.append(handle)

    def snapshot_tree(self, handle):
        if self._tree_error:
            raise self._tree_error
        return self._tree

    def click(self, handle, role, name):
        if self._action_error:
            raise self._action_error
        self.actions.append(("click", role, name))

    def type_text(self, handle, role, name, text):
        if self._action_error:
            raise self._action_error
        self.actions.append(("type", role, name, text))

    def do_action(self, handle, role, name, action):
        if self._action_error:
            raise self._action_error
        self.actions.append(("do_action", role, name, action))


def test_gui_launch_ok():
    srv = McpServer(_FakeWindow())
    srv.gui = _FakeGui(handle="gui-7")
    assert srv._do_gui_launch("app", 1280, 800) == {"ok": True, "handle": "gui-7"}


def test_gui_launch_error():
    from src.services.gui_harness import GuiHarnessError

    srv = McpServer(_FakeWindow())
    srv.gui = _FakeGui(launch_error=GuiHarnessError("cage missing"))
    result = srv._do_gui_launch("app", 1280, 800)
    assert result["ok"] is False
    assert "cage missing" in result["error"]


def test_gui_screenshot_returns_image():
    from mcp.server.fastmcp import Image

    srv = McpServer(_FakeWindow())
    srv.gui = _FakeGui(png=b"\x89PNG-bytes")
    result = srv._do_gui_screenshot("gui-1")
    assert isinstance(result, Image)


def test_gui_screenshot_error():
    from src.services.gui_harness import GuiHarnessError

    srv = McpServer(_FakeWindow())
    srv.gui = _FakeGui(screenshot_error=GuiHarnessError("no such handle"))
    result = srv._do_gui_screenshot("gui-1")
    assert result["ok"] is False
    assert "no such handle" in result["error"]


def test_gui_stop_ok():
    srv = McpServer(_FakeWindow())
    fake = _FakeGui()
    srv.gui = fake
    assert srv._do_gui_stop("gui-1") == {"ok": True}
    assert fake.stopped == ["gui-1"]


def test_gui_stop_error():
    from src.services.gui_harness import GuiHarnessError

    srv = McpServer(_FakeWindow())
    srv.gui = _FakeGui(stop_error=GuiHarnessError("unknown handle: gui-9"))
    result = srv._do_gui_stop("gui-9")
    assert result["ok"] is False
    assert "unknown handle" in result["error"]


def test_gui_snapshot_tree_ok():
    srv = McpServer(_FakeWindow())
    srv.gui = _FakeGui(tree={"role": "application", "name": "app"})
    result = srv._do_gui_snapshot_tree("gui-1")
    assert result == {"ok": True, "tree": {"role": "application", "name": "app"}}


def test_gui_snapshot_tree_error():
    from src.services.gui_harness import GuiHarnessError

    srv = McpServer(_FakeWindow())
    srv.gui = _FakeGui(tree_error=GuiHarnessError("app not found"))
    result = srv._do_gui_snapshot_tree("gui-1")
    assert result["ok"] is False
    assert "app not found" in result["error"]


def test_gui_click_ok():
    srv = McpServer(_FakeWindow())
    fake = _FakeGui()
    srv.gui = fake
    assert srv._do_gui_action("gui-1", "click", "button", "Click Me", None, None) == {
        "ok": True
    }
    assert fake.actions == [("click", "button", "Click Me")]


def test_gui_type_ok():
    srv = McpServer(_FakeWindow())
    fake = _FakeGui()
    srv.gui = fake
    srv._do_gui_action("gui-1", "type", "text", "field", None, "hello")
    assert fake.actions == [("type", "text", "field", "hello")]


def test_gui_do_action_ok():
    srv = McpServer(_FakeWindow())
    fake = _FakeGui()
    srv.gui = fake
    srv._do_gui_action("gui-1", "do_action", "button", "Save", "click", None)
    assert fake.actions == [("do_action", "button", "Save", "click")]


def test_gui_action_error():
    from src.services.gui_harness import GuiHarnessError

    srv = McpServer(_FakeWindow())
    srv.gui = _FakeGui(action_error=GuiHarnessError("no node matching"))
    result = srv._do_gui_action("gui-1", "click", "button", "X", None, None)
    assert result["ok"] is False
    assert "no node matching" in result["error"]
