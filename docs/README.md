# docs/

Design & reference documents. For the current state of the app see the top-level
[README.md](../README.md) (features) and [CLAUDE.md](../CLAUDE.md) (architecture, milestones,
settings). Cross-project coordination-hub design lives in the agent memory
(`memory/project_coordination_hub.md`).

Historical plans for shipped features have moved to [`archive/`](archive/); this folder now holds
only **reference** docs and **active** (still-guiding) plans.

## Reference
| Doc | About |
|-----|-------|
| [mcp.md](mcp.md) | **MCP control-surface reference** — enabling, transport/auth, and the full tool catalog |

## Active (still guiding work)
| Doc | Status |
|-----|--------|
| [plan-stability-roadmap.md](plan-stability-roadmap.md) | 6-phase hardening; a few items still open |
| [plan-session-supervisor.md](plan-session-supervisor.md) | Tier 1 shipped (tmux survival); idle-reaping / Tier 2 deferred |

## Shipped — canonical design records (cited from CLAUDE.md)
| Doc | Feature |
|-----|---------|
| [plan-mcp-integration.md](plan-mcp-integration.md) | Per-window MCP server + tools + `/refresh` hook, GUI test harness |
| [plan-worktrees-multiagent.md](plan-worktrees-multiagent.md) | v1.0 multi-agent Git worktrees |
| [plan-code-companion-refactor.md](plan-code-companion-refactor.md) | v0.8 rename + multi-provider adapter architecture |
| [plan-git-centric-project-manager.md](plan-git-centric-project-manager.md) | v0.7.5 PM status badges + New Project |
| [plan-ui-persistent-claude-pane.md](plan-ui-persistent-claude-pane.md) | Bottom Claude pane + header activity bar |
| [plan-sync-across-machines.md](plan-sync-across-machines.md) | Cross-machine git-backed sync |
| [plan-github-issues.md](plan-github-issues.md) | GitHub Issues panel + detail view |
| [plan-rules.md](plan-rules.md) | CLAUDE.md rules management |

## Archive
Historical milestone plans (`v0.1`–`v0.6`, under the old "Claude Companion" name) and
completed-feature plans that are not cited as design records (observability, linters, run/env
registries, settings, notes, snippets, query editor, problems toolbar, file monitor, …) live in
[`archive/`](archive/). Kept for history; superseded by CLAUDE.md for the current state.
