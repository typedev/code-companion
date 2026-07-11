"""Registry of supported linters (language → tool descriptors).

This is the extension seam that turns the Problems subsystem from Python-only
(ruff/mypy) into a multi-language one. Each ``Linter`` is a declarative descriptor:
which files it applies to, how it is invoked, how its output is parsed into
``Problem`` records, and how it is installed. ``ProblemsService`` iterates this
registry instead of calling ruff/mypy by name.

The descriptor shape is intentionally close to a future declarative-plugin format
(a ``*.toml`` per linter), but for now only these built-in definitions are loaded —
no external files. Parsers are small callables kept here beside their descriptors.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .problems_model import Problem


def _target_dot(_project_path: Path) -> list[str]:
    """Default project-scope target: the project directory itself."""
    return ["."]


def _target_mypy(project_path: Path) -> list[str]:
    """mypy prefers ``src/`` when present (less noise), else the whole project."""
    return ["src"] if (project_path / "src").is_dir() else ["."]


@dataclass(frozen=True)
class InstallSpec:
    """How to install a linter.

    ``kind``:
      - ``python`` — a PyPI tool that lives in the project venv (``uv add --dev`` / pip).
      - ``system`` — a distro package (dnf/apt/pacman); installed via the terminal.
      - ``npm``    — a Node tool; detect-and-hint only (no package.json context).
      - ``none``   — not installable by us (detect only).
    ``package`` is the default package name; ``distro_pkgs`` overrides it per manager
    when the name differs (e.g. Fedora ``ShellCheck`` vs Ubuntu ``shellcheck``).
    """
    kind: str
    package: str
    distro_pkgs: dict[str, str] | None = None

    def package_for(self, manager: str) -> str:
        """Package name for a given package manager (``dnf``/``apt``/``pacman``)."""
        if self.distro_pkgs and manager in self.distro_pkgs:
            return self.distro_pkgs[manager]
        return self.package


@dataclass(frozen=True)
class Linter:
    """Declarative descriptor for one linter."""
    id: str                        # stable id; drives the linters.<id>_enabled setting
    name: str                      # display name
    subtitle: str                  # one-line description for preferences
    binary: str                    # executable name to resolve/run
    extensions: tuple[str, ...]    # file extensions this linter applies to
    scope: str                     # "project" (run once on target) | "files" (pass matched files)
    args: list[str]                # base arguments (path/files are appended by the runner)
    parse: Callable[[str, Path], list[Problem]]  # stdout -> problems
    install: InstallSpec
    default_enabled: bool = True
    # For scope="project": resolves the target path(s) appended after args.
    project_target: Callable[[Path], list[str]] = field(default=_target_dot)


# --------------------------------------------------------------------------- #
# Parsers — each turns a linter's stdout into a list of Problem records.
# --------------------------------------------------------------------------- #

def _relativize(file_path: str, project_path: Path) -> str:
    try:
        return str(Path(file_path).relative_to(project_path))
    except ValueError:
        return file_path


def parse_ruff(output: str, project_path: Path) -> list[Problem]:
    """ruff ``--output-format=json`` → a JSON array of findings."""
    problems: list[Problem] = []
    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        return problems
    for item in data:
        code = item.get("code") or ""
        loc = item.get("location") or {}
        problems.append(Problem(
            file=_relativize(item.get("filename", ""), project_path),
            line=loc.get("row", 1),
            column=loc.get("column", 1),
            code=code,
            message=item.get("message", ""),
            severity="warning" if code.startswith("W") else "error",
            source="ruff",
        ))
    return problems


def parse_mypy(output: str, project_path: Path) -> list[Problem]:
    """mypy ``--output=json`` → one JSON object per line."""
    problems: list[Problem] = []
    for line in output.strip().split("\n"):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        severity = item.get("severity", "error")
        if severity == "note":
            severity = "info"
        problems.append(Problem(
            file=_relativize(item.get("file", ""), project_path),
            line=item.get("line", 1),
            column=item.get("column", 1),
            code=item.get("code", "mypy"),
            message=item.get("message", ""),
            severity=severity,
            source="mypy",
        ))
    return problems


_SHELLCHECK_LEVEL = {"error": "error", "warning": "warning", "info": "info", "style": "info"}


def parse_shellcheck(output: str, project_path: Path) -> list[Problem]:
    """shellcheck ``--format=json`` → a JSON array of findings."""
    problems: list[Problem] = []
    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        return problems
    for item in data:
        code = item.get("code")
        problems.append(Problem(
            file=_relativize(item.get("file", ""), project_path),
            line=item.get("line", 1),
            column=item.get("column", 1),
            code=f"SC{code}" if code is not None else "shellcheck",
            message=item.get("message", ""),
            severity=_SHELLCHECK_LEVEL.get(item.get("level", "warning"), "warning"),
            source="shellcheck",
        ))
    return problems


# yamllint -f parsable: "path:line:col: [level] message (rule)"
_YAMLLINT_RE = re.compile(r"^(.*?):(\d+):(\d+): \[(\w+)\] (.*?)(?: \(([\w-]+)\))?$")


def parse_yamllint(output: str, project_path: Path) -> list[Problem]:
    problems: list[Problem] = []
    for line in output.strip().split("\n"):
        m = _YAMLLINT_RE.match(line.strip())
        if not m:
            continue
        file_path, ln, col, level, message, rule = m.groups()
        problems.append(Problem(
            file=_relativize(file_path, project_path),
            line=int(ln),
            column=int(col),
            code=rule or "yamllint",
            message=message,
            severity="error" if level == "error" else "warning",
            source="yamllint",
        ))
    return problems


# pymarkdown scan: "path:line:col: MDxxx: description (rule-name)"
_PYMARKDOWN_RE = re.compile(r"^(.*?):(\d+):(\d+): (\w+): (.*)$")


def parse_pymarkdown(output: str, project_path: Path) -> list[Problem]:
    problems: list[Problem] = []
    for line in output.strip().split("\n"):
        m = _PYMARKDOWN_RE.match(line.strip())
        if not m:
            continue
        file_path, ln, col, code, message = m.groups()
        problems.append(Problem(
            file=_relativize(file_path, project_path),
            line=int(ln),
            column=int(col),
            code=code,
            message=message.strip(),
            severity="warning",
            source="pymarkdown",
        ))
    return problems


def parse_eslint(output: str, project_path: Path) -> list[Problem]:
    """eslint ``--format json`` → array of {filePath, messages:[...]}."""
    problems: list[Problem] = []
    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        return problems
    for file_result in data:
        file_path = _relativize(file_result.get("filePath", ""), project_path)
        for msg in file_result.get("messages", []):
            problems.append(Problem(
                file=file_path,
                line=msg.get("line", 1),
                column=msg.get("column", 1),
                code=msg.get("ruleId") or "eslint",
                message=msg.get("message", ""),
                severity="error" if msg.get("severity") == 2 else "warning",
                source="eslint",
            ))
    return problems


# --------------------------------------------------------------------------- #
# The registry.
# --------------------------------------------------------------------------- #

LINTERS: list[Linter] = [
    Linter(
        id="ruff", name="Ruff", subtitle="Fast Python linter (style, imports, etc.)",
        binary="ruff", extensions=(".py",), scope="project",
        args=["check", "--output-format=json"], parse=parse_ruff,
        install=InstallSpec(kind="python", package="ruff"),
    ),
    Linter(
        id="mypy", name="Mypy", subtitle="Static type checker for Python",
        binary="mypy", extensions=(".py",), scope="project",
        args=["--output=json"], parse=parse_mypy, project_target=_target_mypy,
        install=InstallSpec(kind="python", package="mypy"),
    ),
    Linter(
        id="yamllint", name="yamllint", subtitle="Linter for YAML files",
        binary="yamllint", extensions=(".yaml", ".yml"), scope="files",
        args=["-f", "parsable"], parse=parse_yamllint,
        install=InstallSpec(kind="python", package="yamllint"),
    ),
    Linter(
        id="pymarkdown", name="PyMarkdown", subtitle="Markdown linter (CommonMark rules)",
        binary="pymarkdown", extensions=(".md",), scope="files",
        args=["scan"], parse=parse_pymarkdown,
        install=InstallSpec(kind="python", package="pymarkdownlnt"),
    ),
    Linter(
        id="shellcheck", name="ShellCheck", subtitle="Static analysis for shell scripts",
        binary="shellcheck", extensions=(".sh", ".bash"), scope="files",
        args=["--format=json"], parse=parse_shellcheck,
        install=InstallSpec(
            kind="system", package="shellcheck",
            distro_pkgs={"dnf": "ShellCheck", "apt": "shellcheck", "pacman": "shellcheck"},
        ),
    ),
    Linter(
        id="eslint", name="ESLint", subtitle="Linter for JavaScript / TypeScript",
        binary="eslint", extensions=(".js", ".jsx", ".ts", ".tsx"), scope="project",
        args=["--format", "json"], parse=parse_eslint,
        install=InstallSpec(kind="npm", package="eslint"),
    ),
]


def get_linters() -> list[Linter]:
    """All registered linters."""
    return list(LINTERS)


def get_linter(linter_id: str) -> Linter | None:
    """Look up a linter by id."""
    for linter in LINTERS:
        if linter.id == linter_id:
            return linter
    return None
