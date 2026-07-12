"""Read-only cross-project catalog.

Enumerates the projects registered in Code Companion on this machine and resolves a
free-text hint to a project's local path + canonical git remote identity. Backs the
``list_projects`` / ``resolve_project`` MCP tools — step A of the coordination-hub design.
The catalog only *resolves*; the agent then studies the target project with its own
Read/Grep/git tools.

Git identity is resolved live per call (never persisted -> never stale), reusing
:func:`resolve_project_identity` / :func:`origin_url`. The identity resolver, clone-url
lookup, and disk check are dependency-injected so the ranking/shaping logic is unit-testable
without spawning git (mirrors the injection style in ``utils/claude_session.py``).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from ..utils.git_worktree import is_linked_worktree
from ..utils.project_identity import origin_url, resolve_message_address, resolve_project_identity
from .project_registry import ProjectRegistry


@dataclass
class CatalogEntry:
    """One registered project, enriched with its live git identity."""

    name: str  # custom label, else folder name
    local_path: str  # canonical (resolved) absolute path
    project_id: str | None  # stable sync id (idea-2 address); None if not syncable
    remote_url: str | None  # canonical host/owner/repo; None for local-only / non-git
    clone_url: str | None  # raw origin URL (cloneable); None when there is no remote
    exists: bool  # local_path still present on disk
    message_address: str | None = None  # mailbox address (worktree-qualified); None if unmessageable

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "local_path": self.local_path,
            "project_id": self.project_id,
            "remote_url": self.remote_url,
            "clone_url": self.clone_url,
            "exists": self.exists,
            "message_address": self.message_address,
        }


def _canonical(path: str) -> str:
    """Resolve a path to its canonical absolute form, tolerating a missing target."""
    try:
        return str(Path(path).resolve())
    except OSError:
        return str(Path(path))


def list_catalog(
    registry: ProjectRegistry | None = None,
    *,
    resolve_identity=resolve_project_identity,
    clone_url=origin_url,
    path_exists=os.path.isdir,
    message_address=resolve_message_address,
) -> list[CatalogEntry]:
    """Return every registered project with its live git identity.

    Missing-on-disk projects are reported with ``exists=False`` and skip all git work.
    A project's remote identity is only resolved when it is present; the raw clone URL is
    fetched only when a canonical remote exists (avoids a redundant git call otherwise).
    """
    registry = registry or ProjectRegistry()
    entries: list[CatalogEntry] = []

    for proj in registry.get_projects():
        raw_path = proj.get("path", "")
        if not raw_path:
            continue
        local_path = _canonical(raw_path)
        # Registry semantics: custom name, else the folder name.
        name = proj.get("name") or Path(local_path).name

        if not path_exists(local_path):
            entries.append(
                CatalogEntry(name, local_path, None, None, None, exists=False)
            )
            continue

        identity = resolve_identity(local_path)
        project_id = identity.project_id if identity else None
        remote_url = identity.canonical_remote if identity else None
        raw_clone = clone_url(local_path) if remote_url else None
        # Mailbox address: a linked worktree is qualified (host/owner/repo#wt:<branch>);
        # a normal project reuses the bare remote (no extra git call).
        if not remote_url:
            msg_addr = None
        elif is_linked_worktree(local_path):
            msg_addr = message_address(local_path)
        else:
            msg_addr = remote_url
        entries.append(
            CatalogEntry(name, local_path, project_id, remote_url, raw_clone,
                         exists=True, message_address=msg_addr)
        )

    return entries


def _owner_repo(remote_url: str | None) -> str | None:
    """Drop the host from ``host/owner/repo`` -> ``owner/repo`` (or None)."""
    if not remote_url:
        return None
    parts = remote_url.split("/")
    return "/".join(parts[1:]) if len(parts) > 1 else None


def _rank(hint: str, entries: list[CatalogEntry]) -> list[CatalogEntry]:
    """Return the entries in the strongest non-empty match tier (may be >1 -> ambiguous)."""
    h = hint.strip().lower()
    if not h:
        return []

    strong: list[CatalogEntry] = []
    medium: list[CatalogEntry] = []
    weak: list[CatalogEntry] = []

    for e in entries:
        name_l = e.name.lower()
        base = Path(e.local_path).name.lower()
        remote = (e.remote_url or "").lower()
        owner_repo = _owner_repo(remote)

        if h == name_l or h == base:
            strong.append(e)
        elif owner_repo and (h == owner_repo or remote.endswith("/" + h)):
            medium.append(e)
        elif h in name_l or h in base or (remote and h in remote):
            weak.append(e)

    for tier in (strong, medium, weak):
        if tier:
            return tier
    return []


def resolve(hint: str, registry: ProjectRegistry | None = None, **inject) -> dict:
    """Resolve ``hint`` to a single project, or report ambiguity / no match.

    Returns ``{"match": <entry>|None, "candidates": [<entry>...], "ambiguous": bool}``.
    A single winner -> ``match`` set, ``candidates`` empty. A tie in the top tier ->
    ``match`` null, ``candidates`` lists the ties, ``ambiguous`` true. No match ->
    ``match`` null, ``candidates`` empty.
    """
    matches = _rank(hint, list_catalog(registry=registry, **inject))
    if not matches:
        return {"match": None, "candidates": [], "ambiguous": False}
    if len(matches) == 1:
        return {"match": matches[0].as_dict(), "candidates": [], "ambiguous": False}
    return {
        "match": None,
        "candidates": [e.as_dict() for e in matches],
        "ambiguous": True,
    }
