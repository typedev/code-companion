"""Path encoding/decoding utilities for Claude Code project paths."""

from pathlib import Path


def encode_project_path(path: str) -> str:
    """Encode project path as Claude Code does.

    Example: /home/user/my-project -> -home-user-my-project
    """
    return path.replace("/", "-")


def decode_project_path(encoded: str) -> str:
    """Decode project path from Claude Code format.

    Claude encodes paths by replacing / with -
    Example: /home/user/my-project -> -home-user-my-project

    The challenge is that folder names can contain dashes too.
    We decode by trying to find the longest valid path.
    """
    if not encoded.startswith("-"):
        return encoded

    # Split by dash
    parts = encoded.split("-")
    # First element is empty (before leading dash)
    parts = parts[1:]

    # Try to reconstruct path by finding valid directories
    # Start from root and greedily find existing paths
    result_parts = []
    current_part = ""

    for i, part in enumerate(parts):
        if current_part:
            candidate = current_part + "-" + part
        else:
            candidate = part

        # Build the full path so far
        test_parts = result_parts + [candidate]
        test_path = "/" + "/".join(test_parts)

        # Also try treating this as a new path component
        test_parts_new = result_parts + ([current_part] if current_part else []) + [part]
        test_path_new = "/" + "/".join(test_parts_new)

        if Path(test_path).exists():
            # Path with combined part exists
            current_part = candidate
        elif Path(test_path_new).exists() and current_part:
            # Path with separate parts exists
            result_parts.append(current_part)
            current_part = part
        elif not current_part:
            # First part, just accumulate
            current_part = part
        else:
            # Neither exists yet, prefer combining (might be deep path)
            # Check if parent of combined exists
            parent_combined = "/" + "/".join(result_parts + [candidate.rsplit("-", 1)[0]]) if "-" in candidate else ""
            parent_separate = "/" + "/".join(result_parts + [current_part])

            if parent_separate and Path(parent_separate).exists():
                result_parts.append(current_part)
                current_part = part
            else:
                current_part = candidate

    if current_part:
        result_parts.append(current_part)

    return "/" + "/".join(result_parts)
