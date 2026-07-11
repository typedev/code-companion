# Plan: Multi-language linters (registry + toggles + install + MCP)

## Goal

Extend the Problems panel from Python-only (ruff/mypy) to a registry-driven
multi-language linter system, with settings toggles, convenient installation, and
MCP tools so the embedded agent can lint on demand. Plugins (external declarative
definitions) are intentionally deferred — the descriptor shape is ready for them.

## Design

- **Registry** (`src/services/linter_registry.py`): a `Linter` descriptor (id, name,
  extensions, scope, args, parse callable, `InstallSpec`) + `LINTERS` list + parsers.
  Data model (`Problem`/`FileProblems`/`LinterStatus`) moved to `problems_model.py` to
  avoid a circular import; `problems_service` re-exports it.
- **Runner** (`problems_service.py`): resolves each linter's command (venv/uv/PATH),
  discovers files for `scope="files"` linters (os.walk honoring `.gitignore`, capped),
  runs and parses. `get_all_problems` iterates the registry, skipping disabled linters
  and those with no matching files in the project. `install_linter` (Python/uv) +
  `terminal_install_command` (system/npm) + `project_has_files`.
- **Preferences** (`preferences_dialog.py`): the Linters page is a loop over the registry
  (one SwitchRow per linter, `linters.<id>_enabled`), one shared handler.
- **Problems panel** (`problems_panel.py`): status/install rows are registry-driven and
  file-aware; Install dispatches Python→silent uv, system/npm→run command in a terminal
  tab via the new `terminal-command-requested` signal → `ProjectWindow.run_in_terminal`.
- **install.sh**: new `linters` subcommand installs distro-packaged shellcheck+yamllint
  (Fedora `ShellCheck` vs Ubuntu `shellcheck`, handled per-manager).
- **MCP** (`mcp_server.py`): `list_linters` + `run_linter(id, paths?)` — the latter runs
  on the worker thread (subprocess may exceed call_on_main's 5s) and refreshes the panel.

## Built-in linters
ruff, mypy (Python) · yamllint (YAML) · pymarkdown/`pymarkdownlnt` (Markdown) ·
shellcheck (shell) · eslint (JS/TS).

## Checkpoints
- [x] `problems_model.py` (extract model) + `linter_registry.py` (descriptors + parsers)
- [x] `problems_service.py` refactored to the registry (resolve/discover/run/install)
- [x] Preferences Linters page data-driven
- [x] Problems panel status/install data-driven + `run_in_terminal`
- [x] `install.sh linters` subcommand
- [x] MCP `list_linters` / `run_linter`
- [x] Docs (CLAUDE.md settings + services, README/INSTALL)
- [x] Verified: parsers (unit), service (ruff/mypy/skip/ignored), install commands,
      file detection; GUI harness (Problems install rows, ShellCheck→terminal, Linters
      preferences page)

## Deferred (out of scope)
- External declarative plugins (`*.toml` in a config dir) — descriptor shape is ready.
- Lint-on-save — kept lazy (tab show / Refresh / MCP).
- npm auto-install of ESLint — detect + hint only.
