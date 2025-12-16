"""Markdown outline parser for extracting headings."""

import re
from dataclasses import dataclass


@dataclass
class MarkdownHeading:
    """A heading in the markdown outline."""

    text: str
    level: int  # 1-6
    line: int

    @property
    def display_name(self) -> str:
        """Get display name with level indicator."""
        return self.text


# Regex for ATX-style headings: # Heading, ## Heading, etc.
HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.+?)(?:\s+#+)?$")

# Regex for fenced code block markers: ``` or ~~~
CODE_FENCE_PATTERN = re.compile(r"^(`{3,}|~{3,})")


def parse_markdown_outline(source: str) -> list[MarkdownHeading]:
    """Parse Markdown source and extract headings.

    Supports ATX-style headings (# Heading).
    Ignores headings inside fenced code blocks.

    Returns a list of MarkdownHeading objects ordered by line number.
    """
    items = []
    in_code_block = False
    code_fence_char = None

    for line_num, line in enumerate(source.splitlines(), start=1):
        stripped = line.strip()

        # Check for code fence toggle
        fence_match = CODE_FENCE_PATTERN.match(stripped)
        if fence_match:
            fence = fence_match.group(1)
            if not in_code_block:
                # Entering code block
                in_code_block = True
                code_fence_char = fence[0]
            elif fence[0] == code_fence_char:
                # Exiting code block (same fence type)
                in_code_block = False
                code_fence_char = None
            continue

        # Skip if inside code block
        if in_code_block:
            continue

        # Check for heading
        match = HEADING_PATTERN.match(stripped)
        if match:
            level = len(match.group(1))
            text = match.group(2).strip()
            items.append(MarkdownHeading(
                text=text,
                level=level,
                line=line_num,
            ))

    return items
