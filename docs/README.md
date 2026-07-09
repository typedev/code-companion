# docs/ — design plans & history

These are **point-in-time design documents**, not living reference. For the current state of the
app see the top-level [README.md](../README.md) (features) and [CLAUDE.md](../CLAUDE.md)
(architecture, milestones, settings). Cross-project coordination-hub design lives in the agent
memory (`memory/project_coordination_hub.md`), not here.

Status legend: **Shipped** (implemented; kept as the design record) · **Active** (partially done /
still guiding work) · **Historical** (early milestone plan, superseded by CLAUDE.md).

## Active
| Doc | About | Status |
|-----|-------|--------|
| [plan-stability-roadmap.md](plan-stability-roadmap.md) | 6-phase hardening (async, git status, monitors, freeze fix, keyring, ports) | Mostly done; a few items open |
| [plan-session-supervisor.md](plan-session-supervisor.md) | Claude survives IDE restart via tmux | Tier 1 shipped; idle-reaping / Tier 2 deferred |

## Shipped
| Doc | Feature |
|-----|---------|
| [plan-code-companion-refactor.md](plan-code-companion-refactor.md) | Rename + multi-provider adapter architecture (v0.8) |
| [plan-mcp-integration.md](plan-mcp-integration.md) | Per-window MCP server + tools + `/refresh` hook, GUI test harness |
| [plan-sync-across-machines.md](plan-sync-across-machines.md) | Cross-machine git-backed sync |
| [plan-ui-persistent-claude-pane.md](plan-ui-persistent-claude-pane.md) | Bottom Claude pane + header activity bar |
| [plan-git-centric-project-manager.md](plan-git-centric-project-manager.md) | PM status badges + New Project (v0.7.5) |
| [plan-github-issues.md](plan-github-issues.md) | GitHub Issues panel + detail view |
| [plan-rules.md](plan-rules.md) | CLAUDE.md rules management |
| [plan-query-editor.md](plan-query-editor.md) | GtkSourceView editor + spellcheck |
| [plan-vertical-toolbar-problems.md](plan-vertical-toolbar-problems.md) | Vertical toolbar + Problems panel (v0.7.2) |
| [settings-plan.md](settings-plan.md) | Settings & Preferences (v0.7) |
| [snippets-plan.md](snippets-plan.md) | Snippets bar |
| [notes-panel-plan.md](notes-panel-plan.md) | Notes panel |
| [file-monitor-service-plan.md](file-monitor-service-plan.md) | Centralized file monitoring |
| [git-ux-improvements-plan.md](git-ux-improvements-plan.md) | Git UX improvements |

## Historical (early milestone plans — see CLAUDE.md “MVP Milestones”)
`v0.1-project-session-list.md` · `v0.2-session-viewer.md` · `v0.2.1-viewer-improvements.md` ·
`v0.3-active-sessions.md` · `v0.4-project-workspace.md` · `v0.4.1-vscode-tasks.md` ·
`v0.5-git-integration.md` · `v0.5.1-git-history.md` · `v0.6-improvements-plan.md`
