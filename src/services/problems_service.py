"""Service for running linters from the registry and collecting problems.

Linters are described declaratively in ``linter_registry``; this service resolves
each one's command, runs it (project-wide or over matching files), parses the
output, and groups the results per file. The ``Problem``/``FileProblems``/
``LinterStatus`` types live in ``problems_model`` and are re-exported here for
backward compatibility.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pathspec

from .problems_model import Problem, FileProblems, LinterStatus  # re-exported
from .linter_registry import Linter, get_linter, get_linters
from .settings_service import SettingsService

__all__ = ["Problem", "FileProblems", "LinterStatus", "ProblemsService"]

# Directories never descended into when discovering files to lint.
_SKIP_DIRS = {
    ".git", ".venv", "venv", "env", "node_modules", "__pycache__",
    ".mypy_cache", ".ruff_cache", ".pytest_cache", "dist", "build",
    ".tox", ".idea", ".vscode", "site-packages",
}
# Safety cap on how many files a file-scope linter is handed at once.
_MAX_FILES = 2000

_PKG_INSTALLERS = {
    "dnf": "sudo dnf install -y",
    "apt": "sudo apt install -y",
    "pacman": "sudo pacman -S",
}


class ProblemsService:
    """Runs the registered linters and collects their problems."""

    def __init__(self, project_path: Path | str):
        self.project_path = Path(project_path).resolve()
        self._status: dict[str, str] = {}  # linter id -> LinterStatus
        self._ignore_spec: pathspec.PathSpec | None = None
        self._ignore_loaded = False

    # -- status ------------------------------------------------------------
    def status(self, linter_id: str) -> str:
        """Availability status of a linter after its last run."""
        return self._status.get(linter_id, LinterStatus.NOT_INSTALLED)

    @property
    def ruff_status(self) -> str:  # back-compat shim
        return self.status("ruff")

    @property
    def mypy_status(self) -> str:  # back-compat shim
        return self.status("mypy")

    # -- uv / project detection -------------------------------------------
    def uses_uv(self) -> bool:
        """Check if project uses uv."""
        if (self.project_path / "uv.lock").exists():
            return True
        return (self.project_path / ".python-version").exists() and (
            self.project_path / "pyproject.toml"
        ).exists()

    def _is_in_uv_dependencies(self, package: str) -> bool:
        """Check if a package is a project dependency (uv.lock / pyproject.toml)."""
        uv_lock = self.project_path / "uv.lock"
        if uv_lock.exists():
            try:
                if f'name = "{package}"' in uv_lock.read_text():
                    return True
            except Exception:
                pass
        pyproject = self.project_path / "pyproject.toml"
        if pyproject.exists():
            try:
                content = pyproject.read_text()
                if (f'"{package}"' in content or f"'{package}'" in content
                        or f"{package}>=" in content or f"{package}=" in content):
                    return True
            except Exception:
                pass
        return False

    @staticmethod
    def _detect_pkg_manager() -> str | None:
        for mgr in ("dnf", "apt", "pacman"):
            if shutil.which(mgr):
                return mgr
        return None

    # -- file discovery ----------------------------------------------------
    def _load_ignore_spec(self) -> pathspec.PathSpec | None:
        """Load .gitignore patterns once (best-effort)."""
        if self._ignore_loaded:
            return self._ignore_spec
        self._ignore_loaded = True
        gitignore = self.project_path / ".gitignore"
        if gitignore.exists():
            try:
                lines = gitignore.read_text(encoding="utf-8").splitlines()
                self._ignore_spec = pathspec.PathSpec.from_lines(
                    pathspec.patterns.GitWildMatchPattern, lines
                )
            except Exception:
                self._ignore_spec = None
        return self._ignore_spec

    def _iter_files(self, extensions: tuple[str, ...]) -> list[str]:
        """Project-relative files matching ``extensions``, honoring .gitignore."""
        spec = self._load_ignore_spec()
        out: list[str] = []
        for root, dirs, files in os.walk(self.project_path):
            dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
            for name in files:
                if not name.endswith(extensions):
                    continue
                rel = str((Path(root) / name).relative_to(self.project_path))
                if spec and spec.match_file(rel):
                    continue
                out.append(rel)
                if len(out) >= _MAX_FILES:
                    return out
        return out

    def project_has_files(self, linter: Linter) -> bool:
        """True if the project has files this linter applies to (or it self-discovers)."""
        if not linter.extensions:
            return True
        return self._has_any_file(linter.extensions)

    def _has_any_file(self, extensions: tuple[str, ...]) -> bool:
        """True if at least one project file matches ``extensions`` (early exit)."""
        spec = self._load_ignore_spec()
        for root, dirs, files in os.walk(self.project_path):
            dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
            for name in files:
                if name.endswith(extensions):
                    rel = str((Path(root) / name).relative_to(self.project_path))
                    if not (spec and spec.match_file(rel)):
                        return True
        return False

    # -- command resolution + running -------------------------------------
    def _resolve_cmd(self, linter: Linter) -> list[str] | None:
        """Resolve the runnable command for a linter, or None if unavailable."""
        kind = linter.install.kind
        if kind == "python":
            venv_bin = self.project_path / ".venv" / "bin" / linter.binary
            if venv_bin.exists():
                return [str(venv_bin)]
            if self.uses_uv() and self._is_in_uv_dependencies(linter.install.package):
                return ["uv", "run", linter.binary]
            return None
        if kind == "npm":
            local = self.project_path / "node_modules" / ".bin" / linter.binary
            if local.exists():
                return [str(local)]
        found = shutil.which(linter.binary)
        return [found] if found else None

    @staticmethod
    def _timeout(linter: Linter) -> int:
        return 120 if linter.id in ("mypy", "eslint") else 60

    def run_linter(self, linter: Linter, paths: list[str] | None = None) -> list[Problem]:
        """Run one linter and return its problems. ``paths`` overrides the targets."""
        cmd = self._resolve_cmd(linter)
        if cmd is None:
            self._status[linter.id] = LinterStatus.NOT_INSTALLED
            return []

        if linter.scope == "files":
            files = paths if paths is not None else self._iter_files(linter.extensions)
            if not files:
                self._status[linter.id] = LinterStatus.AVAILABLE
                return []
            full_cmd = cmd + linter.args + list(files)
        else:  # project
            targets = paths if paths is not None else linter.project_target(self.project_path)
            full_cmd = cmd + linter.args + list(targets)

        try:
            result = subprocess.run(
                full_cmd, cwd=self.project_path, capture_output=True,
                text=True, timeout=self._timeout(linter),
            )
        except FileNotFoundError:
            self._status[linter.id] = LinterStatus.NOT_INSTALLED
            return []
        except subprocess.TimeoutExpired:
            self._status[linter.id] = LinterStatus.ERROR
            return []
        except Exception:
            self._status[linter.id] = LinterStatus.ERROR
            return []

        self._status[linter.id] = LinterStatus.AVAILABLE
        output = result.stdout if result.stdout.strip() else result.stderr
        if not output.strip():
            return []
        try:
            return linter.parse(output, self.project_path)
        except Exception:
            self._status[linter.id] = LinterStatus.ERROR
            return []

    # -- installation ------------------------------------------------------
    def install_linter(self, linter) -> tuple[bool, str]:
        """Install a *Python* linter into the project venv. Returns (success, message).

        Non-Python linters are installed via the terminal (see
        ``terminal_install_command``), not silently here.
        """
        obj = get_linter(linter) if isinstance(linter, str) else linter
        if obj is None:
            return False, f"Unknown linter: {linter}"
        if obj.install.kind != "python":
            return False, f"{obj.name} is not a pip/uv package — install it via the terminal."

        package = obj.install.package
        try:
            if self.uses_uv():
                cmd = ["uv", "add", "--dev", package]
            else:
                venv_pip = self.project_path / ".venv" / "bin" / "pip"
                cmd = [str(venv_pip) if venv_pip.exists() else "pip", "install", package]
            result = subprocess.run(
                cmd, cwd=self.project_path, capture_output=True, text=True, timeout=120
            )
            if result.returncode == 0:
                return True, f"{obj.name} installed successfully"
            return False, result.stderr or result.stdout or "Installation failed"
        except FileNotFoundError as e:
            return False, f"Package manager not found: {e}"
        except subprocess.TimeoutExpired:
            return False, "Installation timed out"
        except Exception as e:
            return False, str(e)

    def terminal_install_command(self, linter: Linter) -> str | None:
        """A shell command that installs the linter, to run in the embedded terminal."""
        spec = linter.install
        if spec.kind == "python":
            return f"uv add --dev {spec.package}" if self.uses_uv() else None
        if spec.kind == "npm":
            return f"npm install --save-dev {spec.package}"
        if spec.kind == "system":
            mgr = self._detect_pkg_manager()
            if not mgr:
                return None
            return f"{_PKG_INSTALLERS[mgr]} {spec.package_for(mgr)}"
        return None

    # -- aggregation -------------------------------------------------------
    @staticmethod
    def _parse_ignored(raw: str) -> tuple[set[str], set[tuple[str, str]]]:
        """Split the ignored-codes setting into global codes and (linter, code) pairs.

        Entries are comma-separated. A bare ``E402`` ignores that code for every linter;
        a qualified ``shellcheck:SC2086`` ignores it only for that linter.
        """
        global_codes: set[str] = set()
        scoped: set[tuple[str, str]] = set()
        for entry in raw.split(","):
            entry = entry.strip()
            if not entry:
                continue
            if ":" in entry:
                source, code = entry.split(":", 1)
                scoped.add((source.strip(), code.strip()))
            else:
                global_codes.add(entry)
        return global_codes, scoped

    def get_all_problems(self) -> dict[str, FileProblems]:
        """Run all enabled+applicable linters and return problems grouped by file."""
        settings = SettingsService.get_instance()
        global_codes, scoped_codes = self._parse_ignored(settings.get("linters.ignored_codes", ""))

        all_problems: dict[str, FileProblems] = {}
        for linter in get_linters():
            if not settings.get(f"linters.{linter.id}_enabled", linter.default_enabled):
                continue
            # Skip linters whose file types are absent (don't run eslint on a Python
            # project, or ruff on a JS-only one).
            if linter.extensions and not self._has_any_file(linter.extensions):
                continue
            for problem in self.run_linter(linter):
                if problem.code in global_codes:
                    continue
                if (problem.source, problem.code) in scoped_codes:
                    continue
                all_problems.setdefault(problem.file, FileProblems(file=problem.file)).problems.append(problem)

        for fp in all_problems.values():
            fp.problems.sort(key=lambda p: (p.line, p.column))
        return all_problems

    def get_summary(self) -> tuple[int, int, int]:
        """Get summary counts: (total_files, total_errors, total_warnings)."""
        problems = self.get_all_problems()
        total_errors = sum(fp.error_count for fp in problems.values())
        total_warnings = sum(fp.warning_count for fp in problems.values())
        return len(problems), total_errors, total_warnings
