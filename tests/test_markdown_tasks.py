"""Phase 8.6: markdown task-checkbox counting."""
from src.utils.markdown_tasks import count_checkboxes, count_checkboxes_in_file


def test_counts_done_and_total():
    text = (
        "# Plan\n"
        "- [x] done one\n"
        "- [ ] open one\n"
        "* [X] done two (star bullet)\n"
        "  + [ ] nested open (plus bullet)\n"
        "- [x] done three\n"
    )
    assert count_checkboxes(text) == (3, 5)


def test_ignores_non_task_lines():
    text = (
        "- regular bullet\n"
        "- [x]missing space after bracket\n"   # not a task (no space)
        "text [ ] not a bullet\n"
        "- [y] wrong marker\n"
        "- [ ] real open\n"
    )
    assert count_checkboxes(text) == (0, 1)


def test_empty_and_no_tasks():
    assert count_checkboxes("") == (0, 0)
    assert count_checkboxes("# Heading\nprose only\n") == (0, 0)


def test_from_file(tmp_path):
    p = tmp_path / "plan.md"
    p.write_text("- [x] a\n- [ ] b\n", encoding="utf-8")
    assert count_checkboxes_in_file(p) == (1, 2)
    assert count_checkboxes_in_file(tmp_path / "missing.md") == (0, 0)
