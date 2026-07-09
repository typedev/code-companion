"""Event-sourced message store: create / fold / comment / status / delete / filter."""

import pytest

from src.services import message_store

A = "github.com/typedev/font-goggles-gtk"
B = "github.com/typedev/font-rover"
C = "github.com/typedev/anchors-factory"


@pytest.fixture(autouse=True)
def _store_in_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(message_store, "get_config_dir", lambda: tmp_path)
    return tmp_path


def test_create_thread_folds_genesis():
    t = message_store.create_thread(A, B, "Need a hint", "Please expose X", refs=["src/x.py"])
    assert t.from_project == A
    assert t.to_project == B
    assert t.subject == "Need a hint"
    assert t.body == "Please expose X"
    assert t.refs == ["src/x.py"]
    assert t.status == "open"
    assert t.deleted is False
    assert t.participants == [A]
    # persisted + reloadable
    reloaded = message_store.load_thread(t.thread_id)
    assert reloaded is not None and reloaded.subject == "Need a hint"


def test_create_requires_sender_and_recipient():
    with pytest.raises(message_store.MessageStoreError):
        message_store.create_thread("", B, "s", "b")
    with pytest.raises(message_store.MessageStoreError):
        message_store.create_thread(A, "", "s", "b")


def test_comments_fold_in_order_and_add_participants():
    t = message_store.create_thread(A, B, "s", "hello")
    message_store.add_comment(t.thread_id, B, "on it")
    folded = message_store.add_comment(t.thread_id, A, "thanks")
    assert [c.body for c in folded.comments] == ["on it", "thanks"]
    assert [c.actor for c in folded.comments] == [B, A]
    assert set(folded.participants) == {A, B}
    assert folded.last_activity >= folded.created_at


def test_status_latest_wins():
    t = message_store.create_thread(A, B, "s", "b")
    message_store.set_status(t.thread_id, B, "in_progress")
    folded = message_store.set_status(t.thread_id, B, "done")
    assert folded.status == "done"


def test_invalid_status_rejected():
    t = message_store.create_thread(A, B, "s", "b")
    with pytest.raises(message_store.MessageStoreError):
        message_store.set_status(t.thread_id, B, "bogus")


def test_delete_tombstones_and_hides():
    t = message_store.create_thread(A, B, "s", "b")
    message_store.delete_thread(t.thread_id, A)
    assert message_store.list_threads() == []
    hidden = message_store.list_threads(include_deleted=True)
    assert len(hidden) == 1 and hidden[0].deleted is True
    # files remain on disk so the tombstone propagates via sync
    assert (message_store.messages_dir() / t.thread_id).is_dir()


def test_threads_for_inbox_sent_all():
    # A -> B, B -> A, A -> C
    message_store.create_thread(A, B, "ab", "b")
    message_store.create_thread(B, A, "ba", "b")
    message_store.create_thread(A, C, "ac", "b")

    inbox_b = message_store.threads_for(B, box="inbox")
    assert {t.subject for t in inbox_b} == {"ab"}

    sent_a = message_store.threads_for(A, box="sent")
    assert {t.subject for t in sent_a} == {"ab", "ac"}

    all_a = message_store.threads_for(A, box="all")
    assert {t.subject for t in all_a} == {"ab", "ba", "ac"}


def test_threads_for_status_filter():
    t = message_store.create_thread(A, B, "open-one", "b")
    done = message_store.create_thread(A, B, "done-one", "b")
    message_store.set_status(done.thread_id, B, "done")

    open_only = message_store.threads_for(B, box="inbox", status="open")
    assert {t.subject for t in open_only} == {"open-one"}
    assert t.thread_id in {x.thread_id for x in open_only}


def test_scan_activity_pending_and_inbound():
    # A -> B (open), A -> B (done), B -> A (open) with a reply from A.
    message_store.create_thread(A, B, "ab-open", "b")
    done = message_store.create_thread(A, B, "ab-done", "b")
    message_store.set_status(done.thread_id, B, "done")
    ba = message_store.create_thread(B, A, "ba", "b")
    message_store.add_comment(ba.thread_id, A, "reply from A")

    threads = message_store.list_threads()
    pending, inbound = message_store.scan_activity(threads, {A, B})

    # B has one open inbound thread (ab-open); the done one doesn't count.
    assert pending.get(B) == 1
    # A has one open inbound thread (ba).
    assert pending.get(A) == 1

    # Inbound to B: the two genesis messages A->B. Inbound to A: genesis B->A only
    # (A's own reply is authored by A, so not inbound to A; it IS inbound to B).
    assert len(inbound.get(B, [])) == 3  # ab-open, ab-done, A's reply on ba
    assert len(inbound.get(A, [])) == 1  # ba genesis


def test_scan_activity_ignores_unregistered_remotes():
    message_store.create_thread(A, C, "ac", "b")
    threads = message_store.list_threads()
    pending, inbound = message_store.scan_activity(threads, {A})  # C not active
    assert C not in pending and C not in inbound
    # A sent it, so it's neither pending nor inbound for A.
    assert pending.get(A, 0) == 0
    assert inbound.get(A, []) == []


def test_list_threads_sorted_by_activity():
    first = message_store.create_thread(A, B, "first", "b")
    second = message_store.create_thread(A, B, "second", "b")
    # touch the first thread so it becomes the most recent activity
    message_store.add_comment(first.thread_id, B, "bump")

    order = [t.subject for t in message_store.list_threads()]
    assert order[0] == "first"
    assert set(order) == {"first", "second"}
    assert second.thread_id  # referenced
