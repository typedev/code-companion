"""Service for running linters (ruff, mypy) and collecting problems."""

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .settings_service import SettingsService


@dataclass
class Problem:
    """Represents a single linter problem."""
    file: str
    line: int
    column: int
    code: str  # F401, E501, error, etc.
    message: str
    severity: str  # error, warning, info
    source: str  # ruff, mypy

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


class ProblemsService:
    """Service for running linters and collecting problems."""

    def __init__(self, project_path: Path | str):
        self.project_path = Path(project_path).resolve()

    def run_ruff(self) -> list[Problem]:
        """Run ruff and return list of problems."""
        try:
            result = subprocess.run(
                ["ruff", "check", "--output-format=json", "."],
                cwd=self.project_path,
                capture_output=True,
                text=True,
                timeout=60
            )
            # ruff returns non-zero exit code when there are errors
            output = result.stdout or result.stderr
            if not output.strip():
                return []

            return self._parse_ruff_output(output)

        except FileNotFoundError:
            # ruff not installed
            return []
        except subprocess.TimeoutExpired:
            return []
        except Exception:
            return []

    def _parse_ruff_output(self, output: str) -> list[Problem]:
        """Parse ruff JSON output into Problem objects."""
        problems = []
        try:
            data = json.loads(output)
            for item in data:
                # Make path relative to project
                file_path = item.get("filename", "")
                try:
                    file_path = str(Path(file_path).relative_to(self.project_path))
                except ValueError:
                    pass  # Keep absolute if not relative to project

                location = item.get("location", {})
                problem = Problem(
                    file=file_path,
                    line=location.get("row", 1),
                    column=location.get("column", 1),
                    code=item.get("code", ""),
                    message=item.get("message", ""),
                    severity="warning" if item.get("code", "").startswith("W") else "error",
                    source="ruff"
                )
                problems.append(problem)
        except json.JSONDecodeError:
            pass

        return problems

    def run_mypy(self) -> list[Problem]:
        """Run mypy and return list of problems."""
        # Find src/ directory or use current
        src_dir = self.project_path / "src"
        target = "src" if src_dir.is_dir() else "."

        try:
            result = subprocess.run(
                ["mypy", "--output=json", target],
                cwd=self.project_path,
                capture_output=True,
                text=True,
                timeout=120  # mypy can be slow
            )
            output = result.stdout
            if not output.strip():
                return []

            return self._parse_mypy_output(output)

        except FileNotFoundError:
            # mypy not installed, try with uv
            try:
                result = subprocess.run(
                    ["uv", "run", "mypy", "--output=json", target],
                    cwd=self.project_path,
                    capture_output=True,
                    text=True,
                    timeout=120
                )
                output = result.stdout
                if not output.strip():
                    return []
                return self._parse_mypy_output(output)
            except Exception:
                return []
        except subprocess.TimeoutExpired:
            return []
        except Exception:
            return []

    def _parse_mypy_output(self, output: str) -> list[Problem]:
        """Parse mypy JSON output into Problem objects."""
        problems = []

        # mypy --output=json produces one JSON object per line
        for line in output.strip().split("\n"):
            if not line.strip():
                continue
            try:
                item = json.loads(line)

                # Make path relative to project
                file_path = item.get("file", "")
                try:
                    file_path = str(Path(file_path).relative_to(self.project_path))
                except ValueError:
                    pass

                severity = item.get("severity", "error")
                if severity == "note":
                    severity = "info"

                problem = Problem(
                    file=file_path,
                    line=item.get("line", 1),
                    column=item.get("column", 1),
                    code=item.get("code", "mypy"),
                    message=item.get("message", ""),
                    severity=severity,
                    source="mypy"
                )
                problems.append(problem)
            except json.JSONDecodeError:
                continue

        return problems

    def get_all_problems(self) -> dict[str, FileProblems]:
        """Run all linters and return problems grouped by file."""
        settings = SettingsService.get_instance()

        # Get settings
        ruff_enabled = settings.get("linters.ruff_enabled", True)
        mypy_enabled = settings.get("linters.mypy_enabled", True)
        ignored_codes_str = settings.get("linters.ignored_codes", "")

        # Parse ignored codes
        ignored_codes = set()
        if ignored_codes_str:
            for code in ignored_codes_str.split(","):
                code = code.strip()
                if code:
                    ignored_codes.add(code)

        all_problems: dict[str, FileProblems] = {}

        # Run ruff
        if ruff_enabled:
            for problem in self.run_ruff():
                if problem.code in ignored_codes:
                    continue
                if problem.file not in all_problems:
                    all_problems[problem.file] = FileProblems(file=problem.file)
                all_problems[problem.file].problems.append(problem)

        # Run mypy
        if mypy_enabled:
            for problem in self.run_mypy():
                if problem.code in ignored_codes:
                    continue
                if problem.file not in all_problems:
                    all_problems[problem.file] = FileProblems(file=problem.file)
                all_problems[problem.file].problems.append(problem)

        # Sort problems within each file by line number
        for fp in all_problems.values():
            fp.problems.sort(key=lambda p: (p.line, p.column))

        return all_problems

    def get_summary(self) -> tuple[int, int, int]:
        """Get summary counts: (total_files, total_errors, total_warnings)."""
        problems = self.get_all_problems()
        total_errors = sum(fp.error_count for fp in problems.values())
        total_warnings = sum(fp.warning_count for fp in problems.values())
        return len(problems), total_errors, total_warnings
