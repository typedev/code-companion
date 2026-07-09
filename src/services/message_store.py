"""Event-sourced inter-project message store (the coordination-hub mailbox, idea 2).

A private, human-first "internal GitHub issues" channel: work in one project can hand a
request to another and a human triages it later. Asynchronous store-and-forward — both
projects need not be live at once.

**Event-sourced.** A thread is a directory of append-only, immutable event files:

    messages/<thread_id>/<event_id>.json

Each event is written once and never modified, and is uniquely uuid-named, so two machines
never touch the same path -> the sync engine's additive global-layer merge converges with no
conflicts (see ``sync_service._sync_global``). A thread's subject / status / participants are
a **fold** over its events, never a mutable field.

**Addressing** is by canonical git remote (``host/owner/repo``, from
``resolve_project_identity``), stable across machines. A project with no remote cannot send
(the caller passes its canonical remote, or None -> blocked).

Event types: ``created`` (``to`` / ``subject`` / ``body`` / ``refs``), ``comment`` (``body``),
``status`` (``status`` in open|in_progress|done|rejected), ``deleted`` (tombstone — hard
file-deletion would not propagate under additive sync). Every event carries ``actor`` (the
acting project's canonical remote) and a UTC ``ts``.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from ..utils.atomic_write import atomic_write_text
from .config_path import get_config_dir

STATUSES = ("open", "in_progress", "done", "rejected")


class MessageStoreError(Exception):
    """Raised on an invalid write (e.g. a missing sender/recipient address)."""


@dataclass
class MessageComment:
    actor: str
    body: str
    ts: str


@dataclass
class MessageThread:
    thread_id: str
    subject: str
    from_project: str  # canonical remote of the sender
    to_project: str  # canonical remote of the recipient
    body: str
    status: str  # one of STATUSES (folded from status events; default "open")
    created_at: str
    last_activity: str
    refs: list[str] = field(default_factory=list)
    comments: list[MessageComment] = field(default_factory=list)
    participants: list[str] = field(default_factory=list)
    deleted: bool = False


def messages_dir() -> Path:
    """Directory holding one ``<thread_id>/`` folder of event files per thread."""
    return get_config_dir() / "messages"


def _new_id() -> str:
    return uuid.uuid4().hex


def _now_iso() -> str:
    # Microsecond precision so sequential events on one machine fold in chronological
    # order (event_id only breaks genuine cross-machine ties).
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


# --- writes (append immutable event files) --------------------------------- #
def _append_event(thread_id: str, event: dict) -> Path:
    """Write one immutable event file for a thread and return its path."""
    thread_dir = messages_dir() / thread_id
    thread_dir.mkdir(parents=True, exist_ok=True)
    path = thread_dir / f"{event['event_id']}.json"
    atomic_write_text(path, json.dumps(event, indent=2, ensure_ascii=False))
    return path


def _event(thread_id: str, etype: str, actor: str, **fields) -> dict:
    return {
        "event_id": _new_id(),
        "thread_id": thread_id,
        "type": etype,
        "ts": _now_iso(),
        "actor": actor,
        **fields,
    }


def create_thread(
    actor: str,
    to: str,
    subject: str,
    body: str,
    refs: list[str] | None = None,
) -> MessageThread:
    """Open a new thread from ``actor`` to ``to`` (both canonical remotes)."""
    if not actor:
        raise MessageStoreError("sender has no git remote; messages require a remote")
    if not to:
        raise MessageStoreError("recipient is required")
    thread_id = _new_id()
    _append_event(
        thread_id,
        _event(thread_id, "created", actor, to=to, subject=subject,
               body=body, refs=list(refs or [])),
    )
    thread = load_thread(thread_id)
    assert thread is not None  # just written
    return thread


def add_comment(thread_id: str, actor: str, body: str) -> MessageThread | None:
    """Append a comment/reply event to an existing thread."""
    if not actor:
        raise MessageStoreError("commenter has no git remote; messages require a remote")
    _append_event(thread_id, _event(thread_id, "comment", actor, body=body))
    return load_thread(thread_id)


def set_status(thread_id: str, actor: str, status: str) -> MessageThread | None:
    """Append a status-change event (open|in_progress|done|rejected)."""
    if status not in STATUSES:
        raise MessageStoreError(f"invalid status: {status}")
    _append_event(thread_id, _event(thread_id, "status", actor, status=status))
    return load_thread(thread_id)


def delete_thread(thread_id: str, actor: str) -> None:
    """Tombstone a thread (hides it; files stay so the deletion propagates via sync)."""
    _append_event(thread_id, _event(thread_id, "deleted", actor))


# --- reads (fold event files) ---------------------------------------------- #
def _load_events(thread_id: str) -> list[dict]:
    thread_dir = messages_dir() / thread_id
    if not thread_dir.is_dir():
        return []
    events = []
    for path in thread_dir.glob("*.json"):
        try:
            events.append(json.loads(path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            continue  # skip a corrupt/partial event; the rest still fold
    # Deterministic order across machines: timestamp, then event_id as tiebreak.
    events.sort(key=lambda e: (e.get("ts", ""), e.get("event_id", "")))
    return events


def _fold(thread_id: str, events: list[dict]) -> MessageThread | None:
    created = next((e for e in events if e.get("type") == "created"), None)
    if created is None:
        return None  # no genesis event -> not a real thread

    status = "open"
    comments: list[MessageComment] = []
    participants: list[str] = []
    deleted = False
    last_activity = created.get("ts", "")

    for e in events:
        actor = e.get("actor", "")
        if actor and actor not in participants:
            participants.append(actor)
        ts = e.get("ts", "")
        if ts > last_activity:
            last_activity = ts
        etype = e.get("type")
        if etype == "status" and e.get("status") in STATUSES:
            status = e["status"]
        elif etype == "comment":
            comments.append(MessageComment(actor=actor, body=e.get("body", ""), ts=ts))
        elif etype == "deleted":
            deleted = True

    return MessageThread(
        thread_id=thread_id,
        subject=created.get("subject", ""),
        from_project=created.get("actor", ""),
        to_project=created.get("to", ""),
        body=created.get("body", ""),
        status=status,
        created_at=created.get("ts", ""),
        last_activity=last_activity,
        refs=list(created.get("refs", []) or []),
        comments=comments,
        participants=participants,
        deleted=deleted,
    )


def load_thread(thread_id: str) -> MessageThread | None:
    """Fold a single thread's events into a :class:`MessageThread`, or None."""
    return _fold(thread_id, _load_events(thread_id))


def list_threads(include_deleted: bool = False) -> list[MessageThread]:
    """Return all threads, newest activity first; tombstoned threads hidden by default."""
    root = messages_dir()
    if not root.is_dir():
        return []
    threads = []
    for thread_dir in root.iterdir():
        if not thread_dir.is_dir():
            continue
        thread = load_thread(thread_dir.name)
        if thread is None:
            continue
        if thread.deleted and not include_deleted:
            continue
        threads.append(thread)
    threads.sort(key=lambda t: t.last_activity, reverse=True)
    return threads


def scan_activity(
    threads: list[MessageThread], active_remotes: set[str]
) -> tuple[dict[str, int], dict[str, list[str]]]:
    """Summarize folded threads for the Project Manager dashboard.

    Returns ``(pending, inbound)``:
    - ``pending[remote]`` = count of open/in-progress threads addressed to ``remote``.
    - ``inbound[remote]`` = timestamps of events authored by *someone else* (the genesis
      message to its recipient, and each reply to every other participant) — the basis
      for "new message" notifications.
    Only remotes in ``active_remotes`` (the locally-registered projects) are considered.
    """
    pending: dict[str, int] = {}
    inbound: dict[str, list[str]] = {}
    for t in threads:
        if t.to_project in active_remotes and t.status in ("open", "in_progress"):
            pending[t.to_project] = pending.get(t.to_project, 0) + 1
        if t.to_project in active_remotes and t.from_project != t.to_project:
            inbound.setdefault(t.to_project, []).append(t.created_at)
        participants = {t.from_project, t.to_project}
        for c in t.comments:
            for remote in participants:
                if remote in active_remotes and c.actor != remote:
                    inbound.setdefault(remote, []).append(c.ts)
    return pending, inbound


def threads_for(
    me: str,
    box: str = "all",
    status: str | None = None,
    include_deleted: bool = False,
) -> list[MessageThread]:
    """Threads involving project ``me`` (a canonical remote).

    ``box`` is ``inbox`` (addressed to me), ``sent`` (from me), or ``all`` (either).
    ``status`` optionally filters to a single status.
    """
    out = []
    for t in list_threads(include_deleted=include_deleted):
        if box == "inbox" and t.to_project != me:
            continue
        if box == "sent" and t.from_project != me:
            continue
        if box == "all" and me not in (t.to_project, t.from_project):
            continue
        if status is not None and t.status != status:
            continue
        out.append(t)
    return out
