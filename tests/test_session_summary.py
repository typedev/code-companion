"""Tests for the per-project session summary service + markdown->Pango helper."""
import pytest

from src.services import session_summary_service as svc
from src.utils.markdown_markup import markdown_to_pango


@pytest.fixture
def config_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(svc, "get_config_dir", lambda: tmp_path)
    return tmp_path


# --------------------------------------------------------------------------- #
# service
# --------------------------------------------------------------------------- #
def test_save_then_load_roundtrip(config_dir):
    path = svc.save("/home/u/proj", "## Next\n- do X\n", title="Handoff")
    assert path.is_file()
    assert path.parent == config_dir / "session-summaries"

    loaded = svc.load("/home/u/proj")
    assert loaded["title"] == "Handoff"
    assert loaded["content"] == "## Next\n- do X\n"
    assert loaded["updated"]  # ISO timestamp stamped by save()


def test_load_missing_returns_none(config_dir):
    assert svc.load("/home/u/never-saved") is None


def test_updated_timestamp_is_humanizable(config_dir):
    # The stamp the service writes must be consumable by the PM's relative formatter
    # (guards against passing a raw ISO string to humanize_relative, which expects a
    # datetime).
    from src.utils.relative_time import humanize_relative_iso

    svc.save("/home/u/proj", "x", title="T")
    updated = svc.load("/home/u/proj")["updated"]
    assert humanize_relative_iso(updated)  # non-empty, does not raise


def test_keyed_by_project_id_when_provided(config_dir):
    svc.save("/home/u/proj", "body", title="T", project_id="github.com_me_proj")
    assert (config_dir / "session-summaries" / "github.com_me_proj.md").exists()
    assert svc.load("/home/u/proj", project_id="github.com_me_proj")["content"] == "body"
    # Without the id, a non-git path falls back to the encoded-path key -> not found.
    assert svc.load("/home/u/proj") is None


def test_overwrite_replaces(config_dir):
    svc.save("/home/u/proj", "first", title="A")
    svc.save("/home/u/proj", "second", title="B")
    loaded = svc.load("/home/u/proj")
    assert loaded["content"] == "second"
    assert loaded["title"] == "B"


def test_body_with_dashes_preserved(config_dir):
    body = "line 1\n---\nline 2"  # a horizontal rule inside the body
    svc.save("/home/u/proj", body)
    assert svc.load("/home/u/proj")["content"] == body


def test_same_project_path_keys_consistently(config_dir):
    # trailing slash / relative segments resolve to the same key
    svc.save("/home/u/proj", "x")
    assert svc.load("/home/u/proj/") is not None
    assert svc.load("/home/u/proj/.") is not None


# --------------------------------------------------------------------------- #
# markdown -> Pango
# --------------------------------------------------------------------------- #
def test_markdown_headings_and_bullets():
    out = markdown_to_pango("# Title\n## Sub\n- item\n* other")
    assert "<big><b>Title</b></big>" in out
    assert "<b>Sub</b>" in out
    assert "• item" in out
    assert "• other" in out


def test_markdown_inline_bold_and_code():
    out = markdown_to_pango("do **this** and `that`")
    assert "<b>this</b>" in out
    assert "<tt>that</tt>" in out


def test_markdown_escapes_special_chars():
    out = markdown_to_pango("a < b & c > d")
    assert "&lt;" in out and "&amp;" in out and "&gt;" in out
    # no raw unescaped angle brackets from the content
    assert "a < b" not in out
