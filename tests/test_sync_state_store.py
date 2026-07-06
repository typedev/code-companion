"""CP1 tests: per-machine hash base manifest."""

from src.services.sync_state_store import SyncStateStore


def test_first_contact_returns_empty_base(tmp_path):
    store = SyncStateStore(tmp_path / "sync_state.json")
    assert store.get_base("proj") == {}


def test_set_and_get_base_roundtrip(tmp_path):
    path = tmp_path / "sync_state.json"
    store = SyncStateStore(path)
    files = {"memory/MEMORY.md": "aaa", "sessions/x.jsonl": "bbb"}
    store.set_base("proj", files)
    assert store.get_base("proj") == files
    # Persisted to disk and reloaded by a fresh instance.
    assert SyncStateStore(path).get_base("proj") == files


def test_get_base_returns_copy(tmp_path):
    store = SyncStateStore(tmp_path / "s.json")
    store.set_base("proj", {"a": "1"})
    base = store.get_base("proj")
    base["a"] = "mutated"
    assert store.get_base("proj") == {"a": "1"}


def test_clear_forgets_project(tmp_path):
    store = SyncStateStore(tmp_path / "s.json")
    store.set_base("proj", {"a": "1"})
    store.clear("proj")
    assert store.get_base("proj") == {}


def test_corrupt_file_falls_back_to_empty(tmp_path):
    path = tmp_path / "s.json"
    path.write_text("{ not json", encoding="utf-8")
    store = SyncStateStore(path)
    assert store.get_base("proj") == {}
    # And can still be written cleanly afterwards.
    store.set_base("proj", {"a": "1"})
    assert SyncStateStore(path).get_base("proj") == {"a": "1"}
