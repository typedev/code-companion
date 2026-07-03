# Plan: Git-Centric Project Manager

Status: **Implemented** (2026-07-03)
Author discussion date: 2026-07-03

## Resolved decisions

1. **Network markers**: **Refresh button + cache + "last updated" relative label.**
   The cached remote status stores a timestamp; the row/header shows a smart
   relative date ("just now", "yesterday", "a week ago", "a year ago").
2. **New Project**: **`git init` + project name** (custom label captured in the
   dialog). No auto `.gitignore` / initial commit, no folder creation for now
   (pick an existing folder).

Make the Project Manager window git-aware: show per-project status markers,
support custom project labels (decoupled from folder name), and add a
"New Project" flow that creates a folder + `git init`.

## Goals

1. **Per-project git markers** in the project list:
   - Has local repo
   - Has remote (origin configured)
   - Dirty tree (uncommitted / unstaged changes)
   - Unpushed commits (ahead N)
   - Updates available (behind N) — requires fetch
   - Open PR count — requires network + GitHub auth
   - Open Issue count — requires network + GitHub auth
2. **Custom project label** — editable name shown in Project Manager, stored
   separately from the folder path (folder is never renamed).
3. **New Project** button — dialog: pick/create folder → `git init` → register → open.

## Key design decision: local vs network markers

| Marker | Cost | Offline |
|---|---|---|
| Has repo / has remote | cheap | ✅ |
| Dirty tree | cheap | ✅ |
| Unpushed (ahead) | cheap | ✅ |
| Needs pull (behind) | **`git fetch`** | ❌ |
| PR count | network + GitHub PAT | ❌ |
| Issue count | network + GitHub PAT | ❌ |

`GitService.get_ahead_behind()` computes `behind` against `@{upstream}`, i.e.
**local** data from the last fetch. Honest "updates available" requires a real
`git fetch` (network). Fetching all projects on every window open is heavy and
hits GitHub rate limits.

**Chosen strategy:**
- **Local markers**: computed automatically in a background thread on list load.
  Fast, offline, non-blocking.
- **Network markers** (fetch for `behind`, PR/Issue counts): behind a
  **Refresh** button + cache, with graceful degradation (no net / no token →
  badges simply hidden, no errors).
- **"Last updated" label**: cache stores the refresh timestamp; UI renders a
  smart relative date via a `humanize_relative(dt)` helper
  ("just now" / "yesterday" / "3 days ago" / "a week ago" / "a year ago").
  Cache persists across window sessions so the label is meaningful on reopen.

## Reused building blocks

- `GitService`: `is_git_repo`, `has_uncommitted_changes`, `get_ahead_behind`,
  `get_remote`, `get_status`.
- `IssuesService`: GitHub REST via stdlib, PAT from git credential helper,
  `list_issues`, `is_github_repo`, `parse_github_remote`.
- `ProjectRegistry`: project storage (needs format v2 — see below).

## Checkpoints

### 1. Registry v2 (custom names + migration)
- [x] New `projects.json` format: `registered_projects: [{path, name}]`.
- [x] Backward-compat read: old `list[str]` auto-migrates on load
      (empty name → fall back to `Path(path).name`).
- [x] `ProjectRegistry.set_name(path, name)` / `get_name(path)` / `get_projects()`.
- [x] Keep `register_project` / `unregister_project` signatures working.

### 2. ProjectStatusService
- [x] New `src/services/project_status_service.py`.
- [x] `LocalStatus` dataclass: has_repo, has_remote, dirty, ahead.
- [x] `RemoteStatus` dataclass: behind, pr_count, issue_count, refreshed_at (all optional).
- [x] Compute local status off the GTK main thread (background thread + idle_add).
- [x] Compute remote status only on demand (Refresh); cache per repo + persist
      to disk (e.g. `~/.config/code-companion/project_status_cache.json`) so the
      "last updated" label survives window reopen.
- [x] `humanize_relative(dt)` helper for the smart relative date label.
- [x] Graceful degradation: no network / no token / not-a-github-repo → None.

### 3. Pull requests in GitHub layer
- [x] Add `list_pull_requests(state="open")` via `/pulls` (extend `IssuesService`
      or thin `PullRequestsService` reusing the same auth/request plumbing).

### 4. Project row redesign
- [x] Badges: dirty ●, ahead ↑N, behind ↓N, PR, Issue (icons + counts).
- [x] Custom name as title, path as subtitle.
- [x] Rename action (context menu or inline edit) — edits name only.
- [x] Refresh button in header (triggers network markers).
- [x] "Last updated" relative label (per row or header, from cache timestamp).

### 5. New Project flow
- [x] "New Project" button → dialog (folder picker + project name entry).
- [x] Pick an existing folder.
- [x] `git init` in the folder.
- [x] Custom project label input (defaults to folder name).
- [x] Register (with name) + open.

### 6. Docs / housekeeping
- [x] Update `CLAUDE.md` milestones.
- [x] Keep this plan's checkboxes updated as work proceeds.

## Notes / risks

- GitHub rate limits: cache remote status; never fetch-all-on-open by default.
- Threading: all git/network calls off the GTK main thread (see
  `widgets/issues_panel.py` threading pattern).
- Removed/missing folders: rows for non-existent paths are already skipped in
  `_load_projects`; keep that behavior.
