# Plan: Run & env-activation registries (de-Python the dev-loop)

## Goal

Turn the two Python-hardcoded dev-loop touch points into registries, same pattern as
the linter registry: a declarative map keyed by detection, with graceful degradation.

- **Run** was `.py`→`uv run python`, `.sh`→bash only; the Run button showed only for
  those extensions.
- **Env activation** on terminal spawn looked only for `.venv/bin/activate`.

## Design

- **`src/services/run_registry.py`** — `Runner(id, extensions, template, requires)` +
  `RUNNERS` (python, shell, node, deno/TS, go, ruby) + `runner_for(ext)` /
  `runner_available(r)` (checks `requires` via `shutil.which`) / `build_command(r, file, args)`
  (shell-quotes the path). Single-file execution only; project builds stay in `tasks.json`.
- **`src/services/env_registry.py`** — `EnvActivator(id, detect)` + `ACTIVATORS`
  (venv, direnv, mise) + `activation_commands(dir)` returning all matching shell lines.
  Detection uses real binaries on PATH (direnv/mise), not shell functions (nvm excluded).

## Integration
- `project_window._on_run_requested` — replace the if/elif with `runner_for(ext)` +
  `build_command`; terminal-tab creation unchanged.
- `script_toolbar._build_ui` — gate the Run button on `runner_for(ext)` +
  `runner_available` (so `.ts` shows Run only if deno is installed).
- `terminal_view` — `_activate_venv_if_exists` → `_activate_env`, registry-driven, gated by
  the new `terminal.auto_activate_env` setting (default true); shell-mode gate + delay kept.

## Checkpoints
- [x] `run_registry.py` + `env_registry.py`
- [x] Run integration (`project_window`, `script_toolbar`)
- [x] Env integration (`terminal_view`) + `terminal.auto_activate_env` setting
- [x] Docs (CLAUDE.md settings/services, README)
- [x] Verified: unit (runner_for/build_command/available, activation_commands); GUI harness
      (Run button for .js, env activation)

## Deferred
- SessionProvider (other AI agents: Gemini/Codex) — separate multi-phase effort.
- Project-run (cargo / npm start / Makefile) — belongs in tasks.json.
- nvm activation — unreliable detection (shell function); covered via direnv/mise.
