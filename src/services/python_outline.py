"""Python outline parser for extracting classes and functions."""

import ast
from dataclasses import dataclass
from pathlib import Path


@dataclass
class OutlineItem:
    """An item in the code outline (class, function, or method)."""

    name: str
    kind: str  # "class", "function", "method"
    line: int
    parent: str | None = None  # Parent class name for methods

    @property
    def display_name(self) -> str:
        """Get display name with kind prefix."""
        if self.kind == "class":
            return f"class {self.name}"
        elif self.kind == "method":
            return f"def {self.name}()"
        else:
            return f"def {self.name}()"


def parse_python_outline(source: str) -> list[OutlineItem]:
    """Parse Python source code and extract outline items.

    Returns a list of OutlineItem objects representing classes, methods,
    and top-level functions, ordered by line number.
    """
    items = []

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return items

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.ClassDef):
            # Add class
            items.append(OutlineItem(
                name=node.name,
                kind="class",
                line=node.lineno,
            ))

            # Add methods within class
            for child in ast.iter_child_nodes(node):
                if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                    items.append(OutlineItem(
                        name=child.name,
                        kind="method",
                        line=child.lineno,
                        parent=node.name,
                    ))

        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            # Top-level function
            items.append(OutlineItem(
                name=node.name,
                kind="function",
                line=node.lineno,
            ))

    # Sort by line number
    items.sort(key=lambda x: x.line)

    return items


def parse_python_file(file_path: str | Path) -> list[OutlineItem]:
    """Parse a Python file and extract outline items.

    Args:
        file_path: Path to the Python file.

    Returns:
        List of OutlineItem objects, or empty list on error.
    """
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            source = f.read()
        return parse_python_outline(source)
    except (OSError, UnicodeDecodeError):
        return []
