"""Directional-mirror engine for LAN file-sync (pure diff + local apply).

No 3-way merge and no base state: the direction is chosen explicitly by the user,
so a diff of two manifests (``rel -> sha256``) fully determines the transfer.
Everything the mirror would overwrite or remove locally is first moved into a
``.deleted/<stamp>/`` trash, so a wrong-direction mistake is always recoverable.

"Give" (local -> peer) is not a distinct algorithm: it is the peer running a
Get with us as the source, so only the Get path lives here.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from ...utils.atomic_write import atomic_write_bytes
from .share_spec import DELETED_DIR


@dataclass
class MirrorDiff:
    """How a local manifest differs from a remote one (both ``rel -> sha256``)."""

    only_remote: set[str] = field(default_factory=set)  # on peer, absent locally
    only_local: set[str] = field(default_factory=set)   # local, absent on peer
    changed: set[str] = field(default_factory=set)      # on both, different hash

    @property
    def identical(self) -> bool:
        return not (self.only_remote or self.only_local or self.changed)


def diff_manifests(local: dict[str, str], remote: dict[str, str]) -> MirrorDiff:
    """Pure comparison of two manifests (from the local machine's viewpoint)."""
    d = MirrorDiff()
    for rel, sha in remote.items():
        if rel not in local:
            d.only_remote.add(rel)
        elif local[rel] != sha:
            d.changed.add(rel)
    for rel in local:
        if rel not in remote:
            d.only_local.add(rel)
    return d


@dataclass
class GetPlan:
    """A Get (peer -> local) plan derived from a diff."""

    fetch: set[str]      # only_remote ∪ changed — pull these from the peer
    remove: set[str]     # only_local — remove locally (moved to .deleted/)
    overwrite: set[str]  # subset of fetch already present locally (backed up first)

    @property
    def destructive_count(self) -> int:
        """Local files the Get will overwrite or remove (the wrong-direction guard)."""
        return len(self.remove) + len(self.overwrite)


def plan_get(d: MirrorDiff, local: dict[str, str]) -> GetPlan:
    """Turn a diff into a concrete Get plan for the local side."""
    fetch = set(d.only_remote) | set(d.changed)
    overwrite = {rel for rel in fetch if rel in local}
    return GetPlan(fetch=fetch, remove=set(d.only_local), overwrite=overwrite)


def plan_give(d: MirrorDiff, remote: dict[str, str]) -> GetPlan:
    """The plan the *peer* would execute for a Give (local -> peer).

    Symmetric to :func:`plan_get` with the roles swapped — used to preview counts
    for the Give direction (``fetch`` = files we send; ``remove`` = files removed
    on the peer).
    """
    fetch = set(d.only_local) | set(d.changed)
    overwrite = {rel for rel in fetch if rel in remote}
    return GetPlan(fetch=fetch, remove=set(d.only_remote), overwrite=overwrite)


def _trash_path(root: Path, stamp: str, rel: str) -> Path:
    return root / DELETED_DIR / stamp / rel


def backup_to_trash(root: Path, rel: str, stamp: str) -> bool:
    """Move ``root/rel`` into ``root/.deleted/<stamp>/rel``. Returns whether moved."""
    src = root / rel
    if not src.exists():
        return False
    dest = _trash_path(root, stamp, rel)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dest))
    return True


def prepare_trash(root: str | os.PathLike, plan: GetPlan, stamp: str) -> None:
    """Move everything a Get will destroy (removals + overwrites) into the trash.

    Call this once before writing any fetched bytes, so a wrong-direction Get is
    fully recoverable from ``.deleted/<stamp>/``.
    """
    root = Path(root)
    for rel in sorted(plan.remove):
        backup_to_trash(root, rel, stamp)
    for rel in sorted(plan.overwrite):
        backup_to_trash(root, rel, stamp)


def write_file(root: str | os.PathLike, rel: str, data: bytes) -> None:
    """Atomically write one fetched file (creating parent dirs)."""
    dest = Path(root) / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_bytes(dest, data)


def apply_get(
    root: str | os.PathLike,
    plan: GetPlan,
    fetch_bytes: Callable[[str], bytes | None],
    stamp: str,
) -> None:
    """Apply a Get plan locally (pull-based convenience over :func:`prepare_trash`).

    Backs up destroyed files, then writes each fetched file. ``fetch_bytes(rel)``
    returns the peer's bytes for a rel (or None to skip). Streaming callers can
    instead call :func:`prepare_trash` once and :func:`write_file` per received file.
    """
    prepare_trash(root, plan, stamp)
    for rel in sorted(plan.fetch):
        data = fetch_bytes(rel)
        if data is None:
            continue
        write_file(root, rel, data)
