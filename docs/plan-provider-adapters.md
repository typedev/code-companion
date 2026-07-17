# Provider-Independent Agent Layer (Claude + Codex CLI)

## Progress

- [x] Stage 1 — Contract + port Claude launch (behavior unchanged) — tests green; live check in final pass
- [x] Stage 2 — Codex history adapter + pricing — validated on real ~/.codex history (gpt-5.6-terra)
- [x] Stage 3 — Codex launch (MCP + notify + system prompt) — spikes: `-c` injection ✓, bearer ✓,
  notify ✓, developer_instructions ✓; codex hooks are trust-gated → no injected clears
  (`notification_clears=False`), window-side focus/key clear instead. Interactive-TUI
  MCP tool-call round-trip left for the live pass (exec mode auto-cancels MCP calls,
  known codex limitation #24135)
- [x] Stage 4 — Knowledge layer (AGENTS.md + get_project_memory)
- [x] Stage 5 — Agent picker UI + per-project persistence — verified in the GUI harness:
  split-button picker, Codex start (CC_PROVIDER=codex in tmux env, registry provider
  persisted), window restart → "Reconnect to Codex CLI"

Live checks: interactive Codex MCP round-trip ✅ VALIDATED (2026-07-17, user-confirmed:
Codex session sees and calls the code-companion MCP tools). Claude regression pass
(start/attach, MCP tools, notification dot, worktree prompt) — pending.

## Context

Code Companion's adapter abstraction (`HistoryAdapter`) covers only *reading* session
history. Everything else — CLI launch flags, MCP config wiring, notification hooks,
instruction files, pricing — is hardwired to Claude Code and bypasses the adapter
(`project_window.py:1195-1420`, `session_notify.py`). The user wants the app to become
AI-provider-independent, starting with a working **Codex CLI** adapter at feature parity
(Grok Build later; the contract must accommodate it). Also: project knowledge must be
available to all providers, and the agent is picked **per session at start** via a
switcher on the Start button. Cross-machine sync stays Claude-only (out of scope).
Renames: minimal — only provider-dependent API; cosmetic `claude_*` names stay.

Research facts this plan relies on (verified against official docs, 2026-07):
- Codex: history at `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl` (SessionMeta first
  line has `cwd`; `event_msg/token_count` carries usage; schema drifts — parse
  defensively). MCP client via `[mcp_servers]` TOML with `bearer_token_env_var`;
  per-launch `-c key=value` dotted overrides. Hooks (no `Notification` event) + top-level
  `notify = [cmd]` on agent-turn-complete. System prompt append: `-c developer_instructions="…"`.
  Instructions file: `AGENTS.md`.
- Grok Build (future): same TOML MCP shape, hooks superset incl. `Notification`,
  `--rules` appends system prompt — fits the same contract.

## Design decisions (agreed)

1. **Contract**: one expanded ABC + small frozen dataclasses. Rename
   `history_adapter.py` → `provider_adapter.py`, `HistoryAdapter` → `ProviderAdapter`
   (keep `HistoryAdapter = ProviderAdapter` alias; ~5 import sites migrate same commit).
   Read-path methods unchanged. New surface:

   ```python
   @dataclass(frozen=True)
   class ProviderCapabilities:
       mcp: bool = False; notifications: bool = False
       notification_clears: bool = False; system_prompt_append: bool = False
       resume: bool = False

   @dataclass(frozen=True)
   class McpEndpoint:
       port: int; server_id: str = "code-companion"
       port_env: str = "CC_MCP_PORT"; token_env: str = "CC_MCP_TOKEN"
       # url(literal_port: bool) -> "http://127.0.0.1:${CC_MCP_PORT}/mcp" | literal

   @dataclass
   class LaunchPlan:
       command: str                       # shell string (tmux `sh -c` / VTE)
       temp_files: list[Path] = ...       # read-once; window deletes at teardown
       env: dict[str, str] = ...

   class ProviderAdapter(ABC):
       name/provider_id/cli_command/icon_name: str
       capabilities: ProviderCapabilities
       instruction_filenames: tuple[str, ...]     # ("CLAUDE.md",) / ("AGENTS.md",)
       # unchanged abstract: config_dir, find_project_history_dir,
       #   get_sessions_for_path, load_session_content, get_session_insight, is_available
       @abstractmethod
       def build_launch(self, *, project_path, session_name, mcp: McpEndpoint | None,
                        extra_system_prompt: str | None, notifications: bool) -> LaunchPlan
   ```

2. **tmux**: keep ONE path-keyed session per project (`cc-<hash>`); record provider via
   `-e CC_PROVIDER=<provider_id>` at `new-session`. Port reservation, PM live-dot,
   notify markers, locks all stay keyed on the existing name. Missing var ⇒ "claude".
3. **Codex MCP injection**: per-launch `-c mcp_servers.…` overrides (token via
   `bearer_token_env_var=CC_MCP_TOKEN`, never on disk; port literal in URL). Never write
   `~/.codex/config.toml` or repo `.codex/`. Spike-gated (Stage 3); degradation =
   `capabilities.mcp=False`.
4. **Codex notifications**: stable wrapper script `<config>/notify/codex-notify.sh`
   invoked by `notify` on agent-turn-complete → writes the same marker files the PM
   already polls. Clears: spike `-c hooks.UserPromptSubmit`; regardless, add a
   provider-neutral window-side clear on terminal focus/keypress (helps Claude too).
   NB: must be a **stable** file, not a temp file — `_on_destroy` deletes launch temp
   files while the tmux session (and its notify command) lives on.
5. **Picker**: `Adw.SplitButton` in the Claude-pane placeholder (main = start current,
   menu = available adapters). Per-project persistence: new optional `"provider"` field
   in `projects.json` (fits `_normalize_entry`, mirrors `name`). Live tmux session ⇒
   plain "Reconnect to {provider from CC_PROVIDER}" (choice already made). Resolution
   precedence: live `CC_PROVIDER` → registry → `ai.provider` setting → "claude".
6. **Knowledge**: adapters expose `instruction_filenames`; notes_panel uses union
   constant `("CLAUDE.md", "AGENTS.md")` (both shown/protected when present); new
   read-only MCP tool `get_project_memory` over `claude_paths.project_memory_dir()`.
7. **Pricing**: rate entries become `(input, output, cache_write_mult, cache_read_mult)`;
   add gpt-5-codex/gpt-5/gpt-5-mini rows (family order matters: `gpt-5-codex` before
   `gpt-5`); verify list prices at implementation time. `is_partial` fallback unchanged.
8. **Worktree delegation prompt** stays in `project_window._worktree_system_prompt()`
   (content provider-neutral), passed as `extra_system_prompt`; Claude renders
   `--append-system-prompt`, Codex `-c developer_instructions=`.
9. **`ai.provider`** becomes the *default* agent (used when no per-project choice).

## Stages (each independently landable; app stays fully working after each)

Step 0: mirror this plan to `docs/plan-provider-adapters.md` (project convention) and
update progress there as stages land.

### Stage 1 — Contract + port Claude launch (behavior unchanged)
- `src/services/history_adapter.py` → `src/services/provider_adapter.py`: dataclasses +
  attrs + `build_launch`; alias for old name.
- `src/services/adapters/claude_adapter.py`: `provider_id="claude"`, full capabilities,
  `instruction_filenames=("CLAUDE.md",)`; `build_launch()` reproduces today's command
  byte-for-byte — moves the `mcpServers` temp-JSON write out of `_start_mcp_server` and
  the notify `--settings` temp write out of `_write_notify_settings`; `shlex.quote`.
- `src/project_window.py`: delete `_claude_cli_command` + `_write_notify_settings`; new
  `_build_launch_plan(mcp_env)`; `_start_mcp_server` no longer writes config;
  `_mcp_config_path`/`_notify_settings_path` → `self._launch_temp_files: list[Path]`;
  `_start_claude_fresh` adds `-e CC_PROVIDER=…` + plan.env; `_start_claude_plain`
  merges plan.env.
- Import updates: `services/__init__.py`, `adapter_registry.py`,
  `widgets/claude_history_panel.py`, `widgets/session_view.py`.
- Tests: `tests/test_provider_adapter.py` — flag set/quoting parity, mcp JSON equals
  historical payload (env placeholders intact, no token), notify JSON equals
  `session_notify.hook_settings(...)`, omission cases, worktree prompt.
- Verify: pytest green; run app → Start Claude → MCP tool call from session works,
  marker appears in `<config>/notify/`; close/reopen window → Reconnect keeps tools.

### Stage 2 — Codex history adapter + pricing
- New `src/services/codex_history.py` (`CodexHistoryService`): mtime/size-based
  first-line index at `<config>/codex_session_index.json` (`atomic_write_text` +
  `threading.Lock`; callers are worker threads). Sessions-for-path = filter index by
  `cwd == realpath(project)`. Content mapping (all lines try/except, unknown skipped):
  `message`→TEXT (skip `<environment_context>`/`<user_instructions>` wrappers),
  `reasoning`→THINKING, `function_call`/`local_shell_call`/`custom_tool_call`/
  `web_search_call`→TOOL_USE, `function_call_output`→TOOL_RESULT; `in_progress` when
  tail line unparsable. Insight: last `token_count` cumulative totals →
  `TokenUsage(input=input-cached, cache_read=cached, output=output)`; model from
  turn_context/SessionMeta else `"codex-unknown"`. Existing models fit unchanged.
- New `src/services/adapters/codex_adapter.py`: `CodexAdapter` — history wired; interim
  `build_launch` returns plain `LaunchPlan(command="codex")` until Stage 3.
  `is_available()` = `shutil.which("codex")`. Register `"codex"` in
  `adapter_registry.ADAPTERS`. Add `src/resources/icons/codex.svg` (none exists yet).
- `src/services/model_pricing.py`: per-entry cache multipliers + gpt rows (see decision 7).
- Tests: `tests/test_codex_history.py` (synthetic rollouts: cwd filter, index reuse/
  invalidation, insight, resilience, truncated tail); extend `tests/test_model_pricing.py`.
- Verify: with real `~/.codex`, pick codex → history panel lists this project's Codex
  sessions with token badges and plausible cost.

### Stage 3 — Codex launch (MCP + notify + system prompt)
- Spike first (scripted, no app): `codex -c 'mcp_servers.code-companion.url="…"'
  -c '….bearer_token_env_var="CC_MCP_TOKEN"' exec "list your MCP tools"` against a stub;
  check hyphenated key quoting (fallback `server_id="code_companion"` via
  `McpEndpoint.server_id`); spike `-c hooks.UserPromptSubmit` clears → sets
  `notification_clears`.
- `src/services/session_notify.py`: `ensure_codex_notify_script()` — stable
  `<config>/notify/codex-notify.sh` (0o700, idempotent, content-versioned; atomic
  tmp+mv marker write); `clear_command(session_name)` shared by both providers.
- `codex_adapter.build_launch`: compose `-c mcp_servers…` (literal port) +
  `-c 'notify=["bash","<script>","<session>"]'` + `-c 'developer_instructions="…"'`
  (TOML string-escape helper) + clear hooks if spike passed. `temp_files=[]`.
- `src/project_window.py`: `_mount_claude_pane` connects terminal focus/first-keypress →
  `session_notify.clear_marker(...)` (provider-neutral clear).
- Tests: Codex command composition (TOML quoting, no token in argv); notify script
  idempotence + marker write via `sh`.
- Verify: Start Codex → `tmux show-environment … CC_PROVIDER` = codex; MCP round-trip
  (`get_workspace_state`) with bearer from env; finish turn with PM open → amber dot +
  desktop notification; focus pane → green; reopen window → "Reconnect to Codex CLI".
- Known accepted limitations: no `--strict-mcp-config` equivalent (user's own Codex MCP
  servers also load); Codex first-run trust prompt is interactive in the pane.

### Stage 4 — Knowledge layer (independent; any time after Stage 1)
- `src/widgets/notes_panel.py`: `INSTRUCTION_FILENAMES = ("CLAUDE.md", "AGENTS.md")`;
  `_refresh_docs` (~:306) iterates it; protected/display checks (~:485, ~:516) use
  membership.
- `src/services/file_monitor_service.py` (~:165): monitor both files.
- `src/services/icon_cache.py` (~:312): add `"AGENTS.md"` mapping.
- `src/services/rules_service.py`: docstring wording only.
- `src/services/mcp_server.py`: read-only tool `get_project_memory(name=None)` — list
  (`[{path,size,mtime}]`) or read one file under
  `claude_paths.project_memory_dir(project_path)`; utf-8, ~200 KB cap + truncation flag,
  path-traversal check; pure filesystem, no `call_on_main`.
- Tests: extend `tests/test_mcp_server.py` (fixture under overridden HOME: list/read/
  traversal-rejection/cap).
- Verify: from a running session (either provider) call `get_project_memory`.

### Stage 5 — Agent picker UI + per-project persistence + accurate Reconnect
- `src/services/project_registry.py`: optional `"provider"` in entries
  (`_normalize_entry` carries it like `name`); `get_provider(path)`/`set_provider(path, id)`.
- `src/project_window.py`: `_resolve_initial_provider()` (precedence per decision 5,
  unknown/unavailable → "claude"); `_set_provider(id)` (no-op while terminal live; swap
  adapter, persist, `claude_history_panel.set_adapter`, update open session view,
  rebuild placeholder); `_show_claude_placeholder`: live session ⇒ plain Reconnect
  button with provider name from `CC_PROVIDER` (and sync `self.adapter` to it);
  else >1 available ⇒ `Adw.SplitButton` + `Gio.Menu` of `get_available_adapters()`
  bound to `win.pick-agent(s)`; exactly 1 ⇒ today's plain button.
- `src/widgets/claude_history_panel.py`: `set_adapter()` — reset caches, reload if visible.
- `src/project_manager.py`: token-scan worker resolves provider per project
  (`session_env(name, "CC_PROVIDER")` → registry → setting); genericize "Claude"
  literals in `_process_notifications` (~:772) and `_apply_indicator_state` (~:629).
- `src/widgets/preferences_dialog.py`: retitle to "Default agent" + subtitle; replace
  stale "coming soon" label (~:295-300).
- Tests: registry provider get/set + legacy migration; resolution precedence (factor as
  pure function).
- Verify (GUI harness): `gui_launch` → split button visible; `gui_click` pick Codex →
  starts; relaunch → "Start Codex CLI" remembered; live Claude tmux + default codex →
  "Reconnect to Claude Code".

## Landing order
1 → 2 → 3 strictly ordered; 4 independent after 1; 5 after 1 (best after 3).
No commits without user approval (project rule) — show diffs per stage.

## Verification (end-to-end, after all stages)
1. `uv run pytest tests/` green.
2. Claude regression: start/attach/kill via PM, MCP tools, notification dot, worktree
   delegation prompt — unchanged behavior.
3. Codex: picker start, history panel + cost, MCP round-trip, turn-complete
   notification, reconnect after window restart.
4. Knowledge: `get_project_memory` from both providers; AGENTS.md visible/protected in
   Notes → Docs.
