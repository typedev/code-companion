"""Machine-independent project identity for cross-machine sync.

The sync repo is keyed by a stable ``project_id`` rather than the project's
absolute path, because the path differs between machines. The id must be derived
from something that travels with the project:

1. the git ``origin`` remote URL (deterministic, identical on both machines), else
2. the repository's root-commit hash (stable across clones, no writes to the repo), else
3. an opt-in committed ``.code-companion/project-id`` file (only read here; created
   elsewhere with explicit user consent), else
4. nothing -> the project is not syncable.
"""

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .git_auth import normalize_remote_url

# Never let git prompt on a terminal inside a worker thread.
_GIT_ENV = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}


@dataclass
class ProjectIdentity:
    """A project's machine-independent identity."""

    project_id: str
    id_source: str  # "remote" | "root-commit" | "committed-uuid"
    canonical_remote: str | None = None


def _run_git(args: list[str], cwd: Path, timeout: int = 10) -> str | None:
    """Run a git command, returning stripped stdout or None on failure."""
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=_GIT_ENV,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return None


def _slug(value: str) -> str:
    """Make a filesystem-safe directory name from an identity string."""
    return re.sub(r"[^a-z0-9._-]+", "_", value.lower()).strip("_")


def origin_url(project_path: str | Path) -> str | None:
    """Return the raw ``origin`` remote URL (cloneable), or None."""
    return _run_git(["remote", "get-url", "origin"], Path(project_path))


def resolve_project_identity(project_path: str | Path) -> ProjectIdentity | None:
    """Resolve a project's sync identity, or None if it is not syncable."""
    path = Path(project_path)

    # Must be a git working tree.
    if _run_git(["rev-parse", "--is-inside-work-tree"], path) != "true":
        return None

    # 1. origin remote URL -> deterministic id shared across machines.
    remote = _run_git(["remote", "get-url", "origin"], path)
    if remote:
        canonical = normalize_remote_url(remote)
        if canonical:
            return ProjectIdentity(
                project_id=_slug(canonical),
                id_source="remote",
                canonical_remote=canonical,
            )

    # 2. root-commit hash -> stable across clones, no writes to the repo.
    roots = _run_git(["rev-list", "--max-parents=0", "HEAD"], path)
    if roots:
        # Pick the lexicographically smallest if history has several roots.
        root = sorted(line.strip() for line in roots.splitlines() if line.strip())
        if root:
            return ProjectIdentity(
                project_id=f"root-{root[0][:16]}",
                id_source="root-commit",
            )

    # 3. opt-in committed id file (repo has no commits yet); only read here.
    id_file = path / ".code-companion" / "project-id"
    if id_file.exists():
        try:
            uid = id_file.read_text(encoding="utf-8").strip()
        except OSError:
            uid = ""
        if uid:
            return ProjectIdentity(project_id=_slug(uid), id_source="committed-uuid")

    # 4. not syncable (e.g. empty repo with no commits and no id file).
    return None
