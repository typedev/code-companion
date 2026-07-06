"""Phase 1.6 tests: line-ending detection, encoding safety, binary sniff."""

from src.utils.text_files import (
    capture_stat,
    detect_line_ending,
    is_binary,
    read_text_file,
    stat_differs,
)


def test_detect_lf():
    assert detect_line_ending("a\nb\nc") == "\n"


def test_detect_crlf():
    assert detect_line_ending("a\r\nb\r\nc") == "\r\n"


def test_detect_cr():
    assert detect_line_ending("a\rb\rc") == "\r"


def test_detect_defaults_to_lf_when_no_newline():
    assert detect_line_ending("single line") == "\n"


def test_read_preserves_crlf_and_normalizes_buffer(tmp_path):
    p = tmp_path / "f.txt"
    p.write_bytes(b"line1\r\nline2\r\n")
    result = read_text_file(p)
    assert result.ok is True
    assert result.line_ending == "\r\n"
    # Buffer text is normalized to LF regardless of the on-disk ending.
    assert result.text == "line1\nline2\n"


def test_read_lf_file(tmp_path):
    p = tmp_path / "f.txt"
    p.write_bytes(b"a\nb\n")
    result = read_text_file(p)
    assert result.line_ending == "\n"
    assert result.text == "a\nb\n"


def test_read_non_utf8_reports_not_ok(tmp_path):
    p = tmp_path / "f.txt"
    p.write_bytes(b"\xff\xfe\x00invalid utf8 \xc3\x28")
    result = read_text_file(p)
    assert result.ok is False
    assert result.text == ""


def test_is_binary_true_for_null_bytes(tmp_path):
    p = tmp_path / "f.bin"
    p.write_bytes(b"PNG\x00\x00data")
    assert is_binary(p) is True


def test_is_binary_false_for_text(tmp_path):
    p = tmp_path / "f.txt"
    p.write_text("just some text\nwith lines\n")
    assert is_binary(p) is False


def test_stat_differs_detects_change(tmp_path):
    p = tmp_path / "f.txt"
    p.write_text("one")
    mtime, size = capture_stat(p)
    assert stat_differs(p, mtime, size) is False
    p.write_text("changed content")
    assert stat_differs(p, mtime, size) is True


def test_stat_differs_on_missing_file(tmp_path):
    p = tmp_path / "f.txt"
    p.write_text("x")
    mtime, size = capture_stat(p)
    p.unlink()
    assert stat_differs(p, mtime, size) is True


def test_stat_differs_none_baseline_is_false(tmp_path):
    p = tmp_path / "f.txt"
    p.write_text("x")
    assert stat_differs(p, None, None) is False
