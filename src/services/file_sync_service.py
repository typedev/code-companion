"""Per-project LAN file-sync orchestration (directional mirror over dispatch).

Ties the local core (``file_sync.*``) to the transport (``dispatch_api``):
compute a preview against a peer, run a Get (pull the peer's shared files into
this project, backing up anything destroyed to ``.deleted/``), or trigger a Give
(ask the peer to Get from us). Peer connection details are passed in explicitly
so this module is loopback-testable and free of zeroconf/GTK.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from . import dispatch_api
from .file_sync import file_index
from .file_sync.file_sync_engine import (
    GetPlan,
    MirrorDiff,
    diff_manifests,
    plan_get,
    plan_give,
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
    """What a sync would do, both directions, for the UI to show before applying."""

    diff: MirrorDiff
    local: dict[str, str]
    remote: dict[str, str]
    get: GetPlan   # local-side plan for Get (⭠ peer→local)
    give: GetPlan  # peer-side plan for Give (⭒ local→peer), for its counts


@dataclass
class SyncResult:
    fetched: int = 0
    removed: int = 0
    overwritten: int = 0


def default_stamp() -> str:
    """A trash-batch stamp for ``.deleted/<stamp>/`` (local wall-clock)."""
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def build_preview(local_path: str, project_id: str, peer: Peer) -> SyncPreview:
    """Fetch the peer manifest, build the local one, and diff — no changes made."""
    remote = dispatch_api.fetch_manifest(peer.host, peer.port, peer.token, project_id)
    local = file_index.build_manifest(local_path)
    d = diff_manifests(local, remote)
    return SyncPreview(d, local, remote, plan_get(d, local), plan_give(d, remote))


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


def request_give(
    project_id: str, peer: Peer, *, my_device_id: str, my_broker_port: int
) -> None:
    """Ask the peer to Get from us (⭒ Give). The peer connects back and pulls.

    Requires mutual pairing (the peer must hold a token for us to pull back).
    """
    dispatch_api.request_pull(
        peer.host, peer.port, peer.token, project_id, my_device_id, my_broker_port
    )
