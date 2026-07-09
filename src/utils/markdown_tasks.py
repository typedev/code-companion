"""Count GitHub-style task checkboxes in markdown (Phase 8.6).

Used to show plan progress (e.g. "12/45") next to ``docs/plan-*.md`` in the
Notes panel. A task line looks like ``- [ ] ...`` (open) or ``- [x] ...`` (done),
with ``-``, ``*`` or ``+`` bullets and any leading indentation.
"""
from __future__ import annotations

import re

_CHECKBOX = re.compile(r"^\s*[-*+]\s+\[([ xX])\]\s")


def count_checkboxes(text: str) -> tuple[int, int]:
    """Return ``(done, total)`` task-checkbox counts for markdown ``text``."""
    done = 0
    total = 0
    for line in text.splitlines():
        m = _CHECKBOX.match(line)
        if not m:
            continue
        total += 1
        if m.group(1) in ("x", "X"):
            done += 1
    return done, total


def count_checkboxes_in_file(path) -> tuple[int, int]:
    """``count_checkboxes`` for a file path; ``(0, 0)`` if unreadable."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return count_checkboxes(f.read())
    except OSError:
        return 0, 0
