"""Local worktree completion reports (Stage 5).

When a Claude agent finishes a task in a worktree it writes a completion report
here; the parent ("main") project surfaces a "N ready" badge and its agent reads
the reports to integrate the work.

This is a **local, per-machine** channel (like ``session_notify`` markers), NOT
the synced message store: a worktree shares its parent's git remote, so it can't
be a distinct message endpoint, and worktree completions are ephemeral and
machine-local anyway. Format: a tiny YAML frontmatter header + a markdown body
(summary + verification + reviewer findings) — readable by both the user and
main's agent. Keyed by ``(parent_root, branch)``.
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path

from ..utils.atomic_write import atomic_write_text
from ..utils.git_worktree import slugify
from .config_path import get_config_dir


def reports_dir() -> Path:
    return get_config_dir() / "worktree-reports"


def _resolved(path: str | Path) -> str:
    return str(Path(path).resolve())


def _report_path(parent_root: str | Path, branch: str) -> Path:
    key = hashlib.sha1(f"{_resolved(parent_root)}\n{branch}".encode()).hexdigest()[:12]
    return reports_dir() / f"{slugify(branch)}-{key}.md"


def write_report(parent_root: str | Path, branch: str, worktree_path: str | Path,
                 summary: str, tests: str = "", review: str = "") -> Path:
    """Write (overwrite) the completion report for a worktree."""
    path = _report_path(parent_root, branch)
    path.parent.mkdir(parents=True, exist_ok=True)
    created = datetime.now().isoformat(timespec="seconds")
    body = summary.strip()
    if review.strip():
        body += "\n\n## Review\n" + review.strip()
    document = (
        "---\n"
        f"parent: {_resolved(parent_root)}\n"
        f"branch: {branch}\n"
        f"worktree_path: {_resolved(worktree_path)}\n"
        f"tests: {tests}\n"
        f"created: {created}\n"
        "---\n"
        f"{body}\n"
    )
    atomic_write_text(path, document)
    return path


def list_reports(parent_root: str | Path) -> list[dict]:
    """All pending completion reports whose ``parent`` is ``parent_root``."""
    directory = reports_dir()
    if not directory.exists():
        return []
    target = _resolved(parent_root)
    out: list[dict] = []
    for path in sorted(directory.glob("*.md")):
        try:
            report = _parse(path.read_text(encoding="utf-8"))
        except OSError:
            continue
        if report.get("parent") == target:
            report["_file"] = str(path)
            out.append(report)
    out.sort(key=lambda r: r.get("created", ""), reverse=True)
    return out


def count_reports(parent_root: str | Path) -> int:
    return len(list_reports(parent_root))


def resolve_report(parent_root: str | Path, branch: str) -> bool:
    """Drop a worktree's report (e.g. after it's merged). True if one was removed."""
    path = _report_path(parent_root, branch)
    try:
        path.unlink()
        return True
    except OSError:
        return False


def _parse(text: str) -> dict:
    """Split the leading ``---``…``---`` frontmatter from the markdown body."""
    fields = {"parent": "", "branch": "", "worktree_path": "", "tests": "", "created": ""}
    body = text
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end != -1:
            header = text[4:end]
            body = text[end + len("\n---\n"):]
            for line in header.splitlines():
                key, sep, value = line.partition(":")
                if sep and key.strip() in fields:
                    fields[key.strip()] = value.strip()
    fields["body"] = body.strip()
    return fields
