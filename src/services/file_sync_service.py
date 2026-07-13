"""Per-project LAN file-sync orchestration (directional mirror over dispatch).

Ties the local core (``file_sync.*``) to the transport (``dispatch_api``):
compute a preview against a peer, run a Get (pull the peer's shared files into
this project, backing up anything destroyed to ``.deleted/``), or trigger a Give
(ask the peer to Get from us). Peer connection details are passed in explicitly
so this module is loopback-testable and free of zeroconf/GTK.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ..utils.atomic_write import atomic_write_text
from . import dispatch_api
from .file_sync import file_index
from .file_sync.file_sync_engine import (
    GetPlan,
    MirrorDiff,
    diff_manifests,
    plan_get,
    prepare_trash,
    write_file,
)

ProgressCb = Callable[[int, int, str], None]  # (done, total, current_rel)


@dataclass
class Peer:
    """A paired, online peer's broker connection."""

    device_id: str
    name: str
    host: str
    port: int
    token: str


@dataclass
class SyncPreview:
    """What a Get would do, for the UI to show before applying."""

    diff: MirrorDiff
    local: dict[str, str]
    remote: dict[str, str]
    get: GetPlan   # local-side plan for Get (⭠ peer→local)


@dataclass
class SyncResult:
    fetched: int = 0
    removed: int = 0
    overwritten: int = 0


def default_stamp() -> str:
    """A trash-batch stamp for ``.deleted/<stamp>/`` (local wall-clock)."""
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def git_operation_in_progress(project_path: str) -> bool:
    """True if a git merge/rebase/cherry-pick/revert is mid-flight in this repo.

    We skip file-sync then so we never apply onto a transient tree. Linked
    worktrees (``.git`` is a file) are treated leniently (guard skipped).
    """
    git = Path(project_path) / ".git"
    if not git.is_dir():
        return False
    markers = ("MERGE_HEAD", "rebase-merge", "rebase-apply", "CHERRY_PICK_HEAD", "REVERT_HEAD")
    return any((git / m).exists() for m in markers)


def ensure_deleted_gitignored(project_path: str) -> None:
    """Make sure ``.deleted/`` is in the project's ``.gitignore`` (the trash is
    machine-local and must never be committed). Idempotent, best-effort."""
    gitignore = Path(project_path) / ".gitignore"
    entry = ".deleted/"
    try:
        existing = gitignore.read_text(encoding="utf-8") if gitignore.exists() else ""
        if entry in [line.strip() for line in existing.splitlines()]:
            return
        prefix = existing if (not existing or existing.endswith("\n")) else existing + "\n"
        atomic_write_text(gitignore, prefix + entry + "\n")
    except OSError:
        pass


def count_trash(project_path: str) -> int:
    """Number of files currently in the project's ``.deleted/`` trash."""
    d = Path(project_path) / ".deleted"
    if not d.is_dir():
        return 0
    return sum(1 for p in d.rglob("*") if p.is_file())


def empty_trash(project_path: str) -> int:
    """Delete the project's ``.deleted/`` trash. Returns how many files were removed."""
    d = Path(project_path) / ".deleted"
    if not d.is_dir():
        return 0
    n = count_trash(project_path)
    try:
        shutil.rmtree(d)
    except OSError:
        return 0
    return n


def build_preview(local_path: str, project_id: str, peer: Peer) -> SyncPreview:
    """Fetch the peer manifest, build the local one, and diff — no changes made."""
    remote = dispatch_api.fetch_manifest(peer.host, peer.port, peer.token, project_id)
    local = file_index.build_manifest(local_path)
    d = diff_manifests(local, remote)
    return SyncPreview(d, local, remote, plan_get(d, local))


def run_get(
    local_path: str,
    project_id: str,
    peer: Peer,
    *,
    stamp: str | None = None,
    progress: ProgressCb | None = None,
) -> SyncResult:
    """Mirror the peer's shared files into this project (⭠ Get).

    Destroyed local files (removed or overwritten) are moved to ``.deleted/<stamp>/``
    first, then the peer's files are streamed in and written atomically.
    """
    stamp = stamp or default_stamp()
    remote = dispatch_api.fetch_manifest(peer.host, peer.port, peer.token, project_id)
    local = file_index.build_manifest(local_path)
    plan = plan_get(diff_manifests(local, remote), local)

    if plan.remove or plan.overwrite:
        ensure_deleted_gitignored(local_path)  # the trash must never be committed
    prepare_trash(local_path, plan, stamp)  # recoverable safety net before writing

    total = len(plan.fetch)
    done = 0
    for rel, data in dispatch_api.fetch_files(
        peer.host, peer.port, peer.token, project_id, sorted(plan.fetch)
    ):
        write_file(local_path, rel, data)
        done += 1
        if progress is not None:
            progress(done, total, rel)

    return SyncResult(fetched=done, removed=len(plan.remove), overwritten=len(plan.overwrite))
