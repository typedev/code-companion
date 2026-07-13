"""Pure, path-only read-only introspection of a project's working state.

Backs the remote-dispatch panels. Crucially these depend ONLY on a filesystem
path (+ git), never on a running window or MCP server: a dispatched session is
*free* (its desktop window is closed), and closing that window stops its MCP
server — so the broker computes this directly from the session's project path
instead. Also reused by the equivalent MCP tools.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


def list_changes(project_path: str | Path) -> dict:
    from .git_service import GitService

    git = GitService(project_path)
    if not git.is_git_repo():
        return {"ok": False, "error": "not a git repository", "changes": []}
    try:
        staged, unstaged = git.get_porcelain_status()
    except Exception as exc:
        return {"ok": False, "error": str(exc), "changes": []}
    changes = [
        {"path": fs.path, "status": fs.status.value, "staged": fs.staged}
        for fs in list(unstaged) + list(staged)
    ]
    return {"ok": True, "changes": changes}


def get_file_diff(project_path: str | Path, path: str, staged: bool = False) -> dict:
    from .git_service import GitService

    git = GitService(project_path)
    if not git.is_git_repo():
        return {"ok": False, "error": "not a git repository"}
    try:
        old, new = git.get_diff(path, staged)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "path": path, "staged": staged, "old": old, "new": new}


def list_files(project_path: str | Path) -> dict:
    try:
        result = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            capture_output=True, cwd=str(project_path), timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "error": str(exc), "files": []}
    if result.returncode != 0:
        return {"ok": False, "error": "git ls-files failed", "files": []}
    files = [f for f in result.stdout.decode("utf-8", "replace").split("\0") if f]
    return {"ok": True, "files": sorted(files)}


def read_file(project_path: str | Path, path: str, max_bytes: int = 1_000_000) -> dict:
    from ..utils.text_files import is_binary_bytes

    base = Path(project_path).resolve()
    try:
        target = (base / path).resolve()
    except (OSError, ValueError):
        return {"ok": False, "error": "bad path"}
    if target != base and base not in target.parents:
        return {"ok": False, "error": "path outside project"}
    if not target.is_file():
        return {"ok": False, "error": "not a file"}
    cap = max(0, int(max_bytes))
    try:
        data = target.read_bytes()[: cap + 1]
    except OSError as exc:
        return {"ok": False, "error": str(exc)}
    truncated = len(data) > cap
    data = data[:cap]
    if is_binary_bytes(data):
        return {"ok": True, "path": path, "binary": True}
    return {
        "ok": True,
        "path": path,
        "content": data.decode("utf-8", "replace"),
        "truncated": truncated,
    }


def get_problems(project_path: str | Path) -> dict:
    """Run the enabled linters against the project and return findings.

    Runs fresh (a free session has no live Problems panel to read from).
    """
    from .problems_service import ProblemsService

    empty_counts = {"error": 0, "warning": 0, "total": 0}
    try:
        grouped = ProblemsService(project_path).get_all_problems()
    except Exception as exc:
        return {"ok": False, "error": str(exc), "problems": [], "counts": empty_counts, "has_run": True}
    problems = []
    for fp in grouped.values():
        for p in fp.problems:
            problems.append({
                "file": p.file, "line": p.line, "column": p.column,
                "code": p.code, "message": p.message,
                "severity": p.severity, "source": p.source,
            })
    errors = sum(1 for p in problems if p["severity"] == "error")
    warnings = sum(1 for p in problems if p["severity"] == "warning")
    return {
        "ok": True,
        "problems": problems,
        "counts": {"error": errors, "warning": warnings, "total": len(problems)},
        "has_run": True,
    }
