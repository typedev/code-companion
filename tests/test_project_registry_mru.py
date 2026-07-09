"""Stage 1: MRU (most-recently-opened) support in ProjectRegistry."""
import json

import pytest

from src.services.project_registry import ProjectRegistry


@pytest.fixture
def registry(tmp_path):
    reg = ProjectRegistry()
    reg.config_dir = tmp_path
    reg.config_file = tmp_path / "projects.json"
    return reg


def _paths(reg):
    return [e["path"] for e in reg.get_projects()]


def test_mark_opened_stamps_and_persists(registry):
    registry.register_project("/home/u/proj-a")
    assert registry.get_projects()[0]["last_opened"] is None

    registry.mark_opened("/home/u/proj-a")
    first = registry.get_projects()[0]["last_opened"]
    assert first  # ISO timestamp string

    # a second open updates the stamp
    registry.mark_opened("/home/u/proj-a")
    assert registry.get_projects()[0]["last_opened"] >= first

    # survives a reload from disk (fresh instance on the same file)
    fresh = ProjectRegistry()
    fresh.config_file = registry.config_file
    assert fresh.get_projects()[0]["last_opened"]


def test_mark_opened_creates_missing_entry(registry):
    registry.mark_opened("/home/u/never-registered")
    entries = registry.get_projects()
    assert len(entries) == 1
    assert entries[0]["path"].endswith("never-registered")
    assert entries[0]["last_opened"]


def test_old_format_without_last_opened_loads_as_none(registry):
    # a projects.json written before Stage 1 (no last_opened field)
    registry.config_file.write_text(
        json.dumps({"registered_projects": [{"path": "/home/u/old", "name": "Old"}]}),
        encoding="utf-8",
    )
    entry = registry.get_projects()[0]
    assert entry["name"] == "Old"
    assert entry["last_opened"] is None
    assert ProjectRegistry.last_opened_epoch(entry) == 0.0


def test_last_opened_epoch_orders_mru_first():
    entries = [
        {"path": "/a", "last_opened": "2026-07-09T10:00:00"},
        {"path": "/b", "last_opened": "2026-07-09T12:00:00"},
        {"path": "/c", "last_opened": None},            # never opened
        {"path": "/d"},                                  # field absent
    ]
    ordered = sorted(entries, key=ProjectRegistry.last_opened_epoch, reverse=True)
    assert [e["path"] for e in ordered] == ["/b", "/a", "/c", "/d"]
    assert ProjectRegistry.last_opened_epoch({"path": "/x", "last_opened": "garbage"}) == 0.0


def test_register_then_mark_keeps_single_entry(registry):
    registry.register_project("/home/u/proj-a", name="A")
    registry.mark_opened("/home/u/proj-a")
    entries = registry.get_projects()
    assert len(entries) == 1
    assert entries[0]["name"] == "A"  # name preserved, not clobbered
    assert entries[0]["last_opened"]
