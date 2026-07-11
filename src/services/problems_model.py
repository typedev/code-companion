"""Shared data model for the Problems/linters subsystem.

Extracted from ``problems_service`` so both the runner (``problems_service``) and
the linter registry (``linter_registry``) can depend on these types without a
circular import. ``problems_service`` re-exports them for backward compatibility.
"""

from dataclasses import dataclass, field


@dataclass
class Problem:
    """A single linter finding.

    ``code``/``source``/``severity`` are free-form strings — not tied to any one
    linter — so any tool in the registry can populate them.
    """
    file: str
    line: int
    column: int
    code: str  # F401, E501, SC2086, note, etc.
    message: str
    severity: str  # error, warning, info
    source: str  # linter id: ruff, mypy, shellcheck, yamllint, ...

    @property
    def location(self) -> str:
        """Return formatted location string."""
        return f"{self.line}:{self.column}"

    def format_short(self) -> str:
        """Format as short string for display."""
        return f":{self.line} {self.code} {self.message}"

    def format_full(self) -> str:
        """Format as full string with file path."""
        return f"{self.file}:{self.line}:{self.column}: {self.code} {self.message}"


@dataclass
class FileProblems:
    """Problems grouped by file."""
    file: str
    problems: list[Problem] = field(default_factory=list)

    @property
    def error_count(self) -> int:
        """Count errors in this file."""
        return sum(1 for p in self.problems if p.severity == "error")

    @property
    def warning_count(self) -> int:
        """Count warnings in this file."""
        return sum(1 for p in self.problems if p.severity == "warning")

    @property
    def total_count(self) -> int:
        """Total problem count."""
        return len(self.problems)


class LinterStatus:
    """Status of linter availability."""
    AVAILABLE = "available"
    NOT_INSTALLED = "not_installed"
    ERROR = "error"
