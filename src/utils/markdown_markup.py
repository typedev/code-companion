"""Minimal markdown -> Pango markup for small in-app labels (e.g. popovers).

Handles the subset a session summary uses: headings, bullets, bold, inline code.
Everything is escaped first, so arbitrary content can't inject Pango markup.
"""
import re

from gi.repository import GLib

_MD_CODE = re.compile(r"`([^`]+)`")
_MD_BOLD = re.compile(r"\*\*(.+?)\*\*")


def _inline(s: str) -> str:
    """Escape a line and apply inline markdown (code, bold) as Pango markup."""
    s = GLib.markup_escape_text(s)
    s = _MD_CODE.sub(r"<tt>\1</tt>", s)
    s = _MD_BOLD.sub(r"<b>\1</b>", s)
    return s


def markdown_to_pango(text: str) -> str:
    """Convert a markdown subset to Pango markup: headings, bullets, bold, code."""
    lines = []
    for raw in text.splitlines():
        stripped = raw.lstrip()
        if raw.startswith("# "):
            lines.append(f"<big><b>{_inline(raw[2:])}</b></big>")
        elif raw.startswith("## "):
            lines.append(f"<b>{_inline(raw[3:])}</b>")
        elif raw.startswith("### ") or raw.startswith("#### "):
            lines.append(f"<b>{_inline(raw.split(' ', 1)[1])}</b>")
        elif stripped[:2] in ("- ", "* "):
            indent = GLib.markup_escape_text(raw[: len(raw) - len(stripped)])
            lines.append(f"{indent}• {_inline(stripped[2:])}")
        else:
            lines.append(_inline(raw))
    return "\n".join(lines)
