"""Resolve a machine-independent ``project_id`` to a local path.

The broker serves file-sync by ``project_id`` (not a live session), so it must
map that id back to a registered project's path on this machine. Uses the same
identity function sync is keyed by, so both machines agree on the id.
"""

from __future__ import annotations

from ...utils.project_identity import resolve_project_identity
from ..project_registry import ProjectRegistry


def resolve_path_for_id(project_id: str, registry: ProjectRegistry | None = None) -> str | None:
    """Return the local path of the registered project whose id matches, or None."""
    if not project_id:
        return None
    reg = registry or ProjectRegistry()
    for path in reg.get_registered_projects():
        ident = resolve_project_identity(path)
        if ident and ident.project_id == project_id:
            return path
    return None
