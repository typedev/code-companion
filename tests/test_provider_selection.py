"""Per-project provider persistence + resolution precedence (Stage 5)."""

import json

import pytest

from src.services import adapter_registry
from src.services.adapter_registry import resolve_provider
from src.services.project_registry import ProjectRegistry


@pytest.fixture
def registry(tmp_path, monkeypatch):
    reg = ProjectRegistry()
    monkeypatch.setattr(reg, "config_file", tmp_path / "projects.json")
    return reg


def test_provider_roundtrip(registry, tmp_path):
    project = tmp_path / "p"
    project.mkdir()
    registry.register_project(str(project))
    assert registry.get_provider(str(project)) is None
    registry.set_provider(str(project), "codex")
    assert registry.get_provider(str(project)) == "codex"
    registry.set_provider(str(project), "")
    assert registry.get_provider(str(project)) is None


def test_legacy_entries_gain_provider_field(registry, tmp_path):
    project = tmp_path / "p"
    project.mkdir()
    registry.config_file.write_text(json.dumps(
        {"registered_projects": [str(project), {"path": str(project / "x")}]}
    ))
    entries = registry.get_projects()
    assert all(e["provider"] == "" for e in entries)


def test_provider_survives_other_updates(registry, tmp_path):
    project = tmp_path / "p"
    project.mkdir()
    registry.register_project(str(project))
    registry.set_provider(str(project), "codex")
    registry.set_name(str(project), "Nice Name")
    registry.mark_opened(str(project))
    assert registry.get_provider(str(project)) == "codex"
    assert registry.get_name(str(project)) == "Nice Name"


class _Available:
    @classmethod
    def is_available(cls):
        return True


class _Missing:
    @classmethod
    def is_available(cls):
        return False


@pytest.fixture
def fake_adapters(monkeypatch):
    monkeypatch.setattr(
        adapter_registry, "ADAPTERS",
        {"claude": _Available, "codex": _Available, "ghost": _Missing},
    )


def test_precedence_live_wins(fake_adapters):
    assert resolve_provider("codex", "claude", "claude") == "codex"


def test_precedence_registry_then_default(fake_adapters):
    assert resolve_provider(None, "codex", "claude") == "codex"
    assert resolve_provider(None, None, "codex") == "codex"
    assert resolve_provider(None, None, None) == "claude"


def test_unavailable_and_unknown_skipped(fake_adapters):
    assert resolve_provider("ghost", "nonsense", "codex") == "codex"
    assert resolve_provider("ghost", None, None) == "claude"
