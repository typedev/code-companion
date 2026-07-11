"""Registry of single-file runners (extension → run command).

Mirrors ``linter_registry``: a declarative map from file extension to the shell
command that runs that file, so the Run button / toolbar is polyglot instead of
hardcoded to ``.py``/``.sh``. Covers *single-file* execution of interpreted
languages; compiled/project builds (cargo, ``npm start``, Makefile) belong in
``tasks.json``, not here.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass

from gi.repository import GLib


@dataclass(frozen=True)
class Runner:
    """How to run one file of a given language."""
    id: str
    extensions: tuple[str, ...]
    template: str                 # placeholders: {file} {args}
    requires: str | None = None   # binary that must be on PATH; None = assume available


RUNNERS: list[Runner] = [
    Runner("python", (".py",), "uv run python {file} {args}"),
    Runner("shell", (".sh", ".bash"), "bash {file} {args}"),
    Runner("node", (".js", ".mjs", ".cjs"), "node {file} {args}", requires="node"),
    Runner("deno", (".ts",), "deno run {file} {args}", requires="deno"),
    Runner("go", (".go",), "go run {file} {args}", requires="go"),
    Runner("ruby", (".rb",), "ruby {file} {args}", requires="ruby"),
]


def runner_for(ext: str) -> Runner | None:
    """First runner whose extensions include ``ext`` (lowercased)."""
    ext = ext.lower()
    for runner in RUNNERS:
        if ext in runner.extensions:
            return runner
    return None


def runner_available(runner: Runner) -> bool:
    """True if the runner's required binary is present (or none is required)."""
    return runner.requires is None or shutil.which(runner.requires) is not None


def build_command(runner: Runner, file_path: str, args: str = "") -> str:
    """Render the run command, shell-quoting the file path."""
    command = runner.template.format(file=GLib.shell_quote(file_path), args=args)
    return command.strip()
