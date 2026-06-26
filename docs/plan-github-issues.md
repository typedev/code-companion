# GitHub Issues Integration

## Context

The app currently has no structured way to track tasks. Local `notes/*.md` files and
code-comment `TODO:` scanning (NotesPanel) are flat — no status, no "open/closed", easy to
forget. Two desired flows:

1. **Take an issue → hand it to Claude.** Open a list of issues, pick one, and send it to the
   running Claude Code session as a "study this and propose a solution" task.
2. **Record a problem → defer it.** Quickly create an issue from the app instead of leaving a
   `TODO:` in code that gets lost.

**Decision:** back this with **GitHub Issues via REST API**, not a local file store. The repo is on
GitHub, and a valid PAT (scope `repo`) is already stored in the git credential helper — auth needs
**zero new infrastructure**.

## Progress (checkpoints)

- [x] **C1** — `issues_service.py` (API client + `Issue` model). Verified against
  `typedev/code-companion`: `is_github_repo()` True, `get_owner_repo()==("typedev","code-companion")`,
  `list_issues` returns open=`[]`, all=`[#1 closed]` (PR-filtering + pagination exercised).
- [x] **C2** — read-only `issues_panel` + `issue_detail_view` + 5 sidebar spots + `_on_issue_selected`.
  Verified: compiles, imports, both widgets construct + load real data headlessly; app starts clean.
- [x] **C3** — New-issue dialog + `create_issue` + Close/Reopen + `set_issue_state`. Code complete;
  GUI write-path needs interactive verification.
- [x] **C4** — `_on_send_issue_to_claude` + prompt template; cold-start and warm paths. Code complete;
  needs interactive verification.
- [x] **C5** — badge (`_update_issues_badge` + blue css + signal wiring + startup call); tab-close
  cleanup; shared auth retry (`github_auth.py`). Code complete.

> Remaining: interactive GUI verification (create/close/reopen round-trip, Send to Claude, badge).

## Redesign round (after first UI review)

- [x] **R1** — `comments` count on `Issue`; `IssueComment` model + `list_comments()` endpoint.
- [x] **R2** — list switched to `boxed-list` with commit-style multi-line rows (state dot, #num,
  title, label chips, 💬 comment count, relative time). Open/Closed/All filter is now a native
  `Adw.ToggleGroup` segmented control.
- [x] **R3** — issue body + comments rendered as one markdown document via `MarkdownPreview`
  (WebKit). Comments fetched async with stale-token guard; re-fetched on `update()`.

Verified headlessly against `paratype-git/cyrillic-languages` issue #5 (2 comments load + render).

## UI tweaks round 2

- [x] Comments visually separated as bordered cards (`.issue-comment` / `.comment-head`) in the
  shared `markdown_preview.py` template; each comment body rendered via `MarkdownPreview.render_markdown`.
- [x] Close/Reopen button moved to the right (spacer in the action bar).
- [x] Added `src/resources/icons/github.svg` (GitHub mark); "Open on GitHub" button uses it.
- [x] "Send to Claude" is now a text button (icon removed).
- [x] `MarkdownPreview` body + code font-size now derive from `editor.font_size` (pt→px ×4/3) and
  `editor.line_height` — applies to ALL markdown rendering (issues + .md preview). Bump
  `editor.font_size` in Settings to enlarge globally.

## Locked design decisions

| Aspect | Decision |
|---|---|
| Backend | GitHub Issues REST API v3. HTTP via **stdlib `urllib` only** — no new deps. |
| Auth | Reuse `GitService._get_stored_credentials(remote_url)` → PAT. On missing/401/403, reuse the push/pull credentials dialog. |
| Statuses | **open / closed only** (GitHub-native). Buttons: Close/Reopen. Filter: Open/Closed/All. |
| Send to Claude | Feed prompt into singleton `self.claude_terminal` via `feed_child` + `GLib.timeout_add(50, _send_enter_to_terminal)`. Cold-start session first if `None`. |
| Sidebar icon | Existing `src/resources/icons/todo.svg`. |
| Threading | All API calls off main thread: `threading.Thread(daemon=True)` + `GLib.idle_add`. |
| Badge | Open-issues count on the toolbar button; new `.toolbar-badge-blue` css class. |
| Remote URL | Read via `git remote get-url` subprocess (thread-safe), not pygit2. |

## New file: `src/services/issues_service.py`

`Issue` dataclass — `number, title, body, state, labels: list[str], html_url, user, created_at,
updated_at`; `from_json()` defends against null fields and **skips dicts with `"pull_request"` key**.
`GitHubError(Exception)` for non-auth failures. Reuse `AuthenticationRequired` (from `.git_service`)
for 401/403.

```python
class IssuesService:
    def __init__(self, repo_path): ...
    def is_github_repo(self) -> bool
    def get_owner_repo(self) -> tuple[str,str] | None
    def list_issues(self, state="open") -> list[Issue]   # per_page=100 + Link pagination, cap 10
    def get_issue(self, number) -> Issue
    def create_issue(self, title, body="") -> Issue
    def set_issue_state(self, number, state) -> Issue
```

owner/repo parsed from remote URL (https / ssh / `ssh://` forms; strip `.git`; non-github → None).
Token via `git_service._get_stored_credentials(remote.url)` → PAT. `_request()` builds
`https://api.github.com/repos/{owner}/{repo}{path}` with `Authorization: token <PAT>`,
`Accept: application/vnd.github+json`, `X-GitHub-Api-Version: 2022-11-28`, `User-Agent`. Errors:
401/403 → `AuthenticationRequired`; other HTTPError → `GitHubError`; network → `GitHubError(0, ...)`.

## New file: `src/widgets/issues_panel.py`

Modeled on `ProblemsPanel`. Signals `"issue-selected"(object)`, `"issues-changed"()`. Header
(title + New + refresh), linked Open/Closed/All filter, `Gtk.ListBox` (navigation-sidebar) of rows
(state dot, `#number`, ellipsized title, label chips; closed dimmed), empty/error states, background
load with auth-dialog retry. New-issue dialog: `Adw.AlertDialog` + `set_extra_child` (NO
EventControllerKey), title Entry + body TextView.

## New file: `src/widgets/issue_detail_view.py`

Modeled on `ProblemsDetailView` with `.update(issue)`. Header (`#number` + title + state pill),
action bar (Close/Reopen, Send to Claude, Open in browser), label chips, non-editable body TextView,
meta caption. Signals `"send-to-claude"(object)`, `"issue-changed"(object)`.

## Wiring: `src/project_window.py`

Init-to-None (`issue_detail_page/view`, `issues_service`); create `self.issues_service` after
`self.git_service`; 5 sidebar spots (toolbar button `todo`/Issues, placeholder, lazy switch,
`_build_issues_page`, lazy load on show); `_on_issue_selected` (single-tab reuse);
`_on_send_issue_to_claude` (cold-start + feed); `_update_issues_badge` (bg thread, blue badge);
tab-close cleanup. Badge refresh on startup + `issues-changed`/`issue-changed` only (not git-status).

### Send-to-Claude prompt

```
Please help me work on GitHub issue #{number}: "{title}".

State: {state}
URL: {html_url}
Labels: {labels or "none"}

Issue description:
{body or "(no description provided)"}

Analyze this issue in the context of the current project, propose an
implementation plan, and identify the files that need to change. Do not
write code yet — start with the plan.
```

## Verification

`uv run python -m src.main --project /home/alexander/WORK/claude-companion`. Per-checkpoint manual
checks against `typedev/code-companion` (0 issues now): empty-states, create/close/reopen round-trip,
Send-to-Claude cold/warm, badge reflects open count and clears at 0.
