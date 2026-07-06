"""Heal a sync clone left in a bad state by a crashed prior run.

Called before every fresh sync so a previous crash cannot wedge the clone.
"""

from .sync_repo import SyncRepo


def recover(repo: SyncRepo, last_good_commit: str | None = None) -> None:
    """Best-effort repair of the sync clone.

    - A clone left mid-rebase is aborted.
    - If HEAD is unreadable (corrupt/detached) and a last-good commit is known,
      hard-reset to it.

    Unpushed commits are intentionally left alone: the next ``pull --rebase`` +
    ``push`` integrates them.
    """
    if not repo.exists_locally():
        return
    if repo.is_mid_rebase():
        repo.abort_rebase()
    if not repo.head_hash() and last_good_commit:
        repo.hard_reset_to(last_good_commit)
