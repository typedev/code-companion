"""Phase 1.3 tests: atomic file writes never corrupt the original file."""

import os

import pytest

from src.utils.atomic_write import atomic_write_bytes, atomic_write_text


def test_writes_new_content(tmp_path):
    p = tmp_path / "f.txt"
    p.write_text("old")
    atomic_write_text(p, "new content")
    assert p.read_text() == "new content"


def test_creates_new_file(tmp_path):
    p = tmp_path / "new.txt"
    atomic_write_text(p, "hello")
    assert p.read_text() == "hello"


def test_original_intact_on_failure_between_write_and_replace(tmp_path, monkeypatch):
    p = tmp_path / "f.txt"
    p.write_text("original")

    def boom(src, dst):
        raise OSError("simulated failure during replace")

    monkeypatch.setattr(os, "replace", boom)

    with pytest.raises(OSError):
        atomic_write_text(p, "should not land")

    # The original file must be untouched and no temp file may be left behind.
    assert p.read_text() == "original"
    leftovers = [name for name in os.listdir(tmp_path) if name != "f.txt"]
    assert leftovers == []


def test_preserves_file_mode(tmp_path):
    p = tmp_path / "f.txt"
    p.write_text("x")
    os.chmod(p, 0o640)
    atomic_write_bytes(p, b"y")
    assert (os.stat(p).st_mode & 0o777) == 0o640


def test_no_newline_translation_by_default(tmp_path):
    p = tmp_path / "f.txt"
    atomic_write_text(p, "a\nb\n")
    assert p.read_bytes() == b"a\nb\n"


def test_translates_newlines_to_crlf(tmp_path):
    p = tmp_path / "f.txt"
    atomic_write_text(p, "a\nb\n", newline="\r\n")
    # read_bytes: no read-side translation, so we see the raw CRLFs.
    assert p.read_bytes() == b"a\r\nb\r\n"
