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


class LinterStatus:
    """Status of linter availability."""
    AVAILABLE = "available"
    NOT_INSTALLED = "not_installed"
    ERROR = "error"


class ProblemsService:
    """Service for running linters and collecting problems."""

    def __init__(self, project_path: Path | str):
        self.project_path = Path(project_path).resolve()
        # Initially unknown, will be set when linter runs
        self._ruff_status = LinterStatus.NOT_INSTALLED
        self._mypy_status = LinterStatus.NOT_INSTALLED

    @property
    def ruff_status(self) -> str:
        return self._ruff_status

    @property
    def mypy_status(self) -> str:
        return self._mypy_status

    def check_linter_available(self, linter: str) -> bool:
        """Check if a linter is available in the project's .venv."""
        # Check if linter exists in project's .venv
        venv_bin = self.project_path / ".venv" / "bin" / linter
        if venv_bin.exists():
            return True

        # Check if it's in project dependencies (uv.lock or pyproject.toml)
        if self.uses_uv():
            uv_lock = self.project_path / "uv.lock"
            if uv_lock.exists():
                try:
                    content = uv_lock.read_text()
                    if f'name = "{linter}"' in content:
                        return True
                except Exception:
                    pass

        return False

    def uses_uv(self) -> bool:
        """Check if project uses uv."""
        # Check for uv.lock (created after uv add)
        uv_lock = self.project_path / "uv.lock"
        if uv_lock.exists():
            return True

        # Check for .python-version (created by uv init)
        python_version = self.project_path / ".python-version"
        pyproject = self.project_path / "pyproject.toml"
        if python_version.exists() and pyproject.exists():
            return True

        return False

    def _is_in_uv_dependencies(self, package: str) -> bool:
        """Check if package is in project's uv dependencies."""
        # Check uv.lock first (most reliable)
        uv_lock = self.project_path / "uv.lock"
        if uv_lock.exists():
            try:
                content = uv_lock.read_text()
                if f'name = "{package}"' in content:
                    return True
            except Exception:
                pass

        # Check pyproject.toml dependencies and dev-dependencies
        pyproject = self.project_path / "pyproject.toml"
        if pyproject.exists():
            try:
                content = pyproject.read_text()
                # Simple check - look for package in dependencies sections
                if f'"{package}"' in content or f"'{package}'" in content or f"{package}>=" in content or f"{package}=" in content:
                    return True
            except Exception:
                pass

        return False

    def install_linter(self, linter: str) -> tuple[bool, str]:
        """Install a linter into project's .venv.

        Returns (success, message).
        """
        try:
            if self.uses_uv():
                # Use uv add --dev
                result = subprocess.run(
                    ["uv", "add", "--dev", linter],
                    cwd=self.project_path,
                    capture_output=True,
                    text=True,
                    timeout=120
                )
            else:
                # Use pip install
                venv_pip = self.project_path / ".venv" / "bin" / "pip"
                if venv_pip.exists():
                    pip_cmd = str(venv_pip)
                else:
                    pip_cmd = "pip"

                result = subprocess.run(
                    [pip_cmd, "install", linter],
                    cwd=self.project_path,
                    capture_output=True,
                    text=True,
                    timeout=120
                )

            if result.returncode == 0:
                return True, f"{linter} installed successfully"
            else:
                return False, result.stderr or result.stdout or "Installation failed"

        except FileNotFoundError as e:
            return False, f"Package manager not found: {e}"
        except subprocess.TimeoutExpired:
            return False, "Installation timed out"
        except Exception as e:
            return False, str(e)

    def run_ruff(self) -> list[Problem]:
        """Run ruff and return list of problems."""
        # Check if ruff is in project's .venv
        venv_ruff = self.project_path / ".venv" / "bin" / "ruff"

        if venv_ruff.exists():
            ruff_cmd = [str(venv_ruff)]
        elif self.uses_uv() and self._is_in_uv_dependencies("ruff"):
            # Use uv run only if ruff is in project dependencies
            ruff_cmd = ["uv", "run", "ruff"]
        else:
            # Not in project dependencies
            self._ruff_status = LinterStatus.NOT_INSTALLED
            return []

        try:
            result = subprocess.run(
                ruff_cmd + ["check", "--output-format=json", "."],
                cwd=self.project_path,
                capture_output=True,
                text=True,
                timeout=60
            )
            self._ruff_status = LinterStatus.AVAILABLE
            # ruff returns non-zero exit code when there are errors
            output = result.stdout or result.stderr
            if not output.strip():
                return []

            return self._parse_ruff_output(output)

        except FileNotFoundError:
            # ruff not installed
            self._ruff_status = LinterStatus.NOT_INSTALLED
            return []
        except subprocess.TimeoutExpired:
            self._ruff_status = LinterStatus.ERROR
            return []
        except Exception:
            self._ruff_status = LinterStatus.ERROR
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

        # Check if mypy is in project's .venv
        venv_mypy = self.project_path / ".venv" / "bin" / "mypy"

        if venv_mypy.exists():
            mypy_cmd = [str(venv_mypy)]
        elif self.uses_uv() and self._is_in_uv_dependencies("mypy"):
            # Use uv run only if mypy is in project dependencies
            mypy_cmd = ["uv", "run", "mypy"]
        else:
            # Not in project dependencies
            self._mypy_status = LinterStatus.NOT_INSTALLED
            return []

        try:
            result = subprocess.run(
                mypy_cmd + ["--output=json", target],
                cwd=self.project_path,
                capture_output=True,
                text=True,
                timeout=120  # mypy can be slow
            )
            # mypy returns non-zero even for type errors, check if it ran
            if "No module named" in result.stderr and "mypy" in result.stderr:
                self._mypy_status = LinterStatus.NOT_INSTALLED
                return []

            self._mypy_status = LinterStatus.AVAILABLE
            output = result.stdout
            if not output.strip():
                return []

            return self._parse_mypy_output(output)

        except FileNotFoundError:
            self._mypy_status = LinterStatus.NOT_INSTALLED
            return []
        except subprocess.TimeoutExpired:
            self._mypy_status = LinterStatus.ERROR
            return []
        except Exception:
            self._mypy_status = LinterStatus.ERROR
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
