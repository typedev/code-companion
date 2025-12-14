# FileMonitorService Refactoring Plan

## Problem Analysis

### Current State: Duplicated Monitors

Each widget creates its own file monitors, leading to:
- **Duplication**: Same files monitored multiple times
- **Inconsistency**: Different debounce times, different event handling
- **Hard to debug**: Monitor logic scattered across 6 files
- **Bug**: file_tree doesn't monitor `.git/logs/HEAD`, so commits don't update git status icons

### Current Monitor Map

| Component | What it monitors | Debounce | Updates |
|-----------|------------------|----------|---------|
| **file_tree.py** | `.git/index`, expanded directories | 200ms | Tree structure + git status icons |
| **git_changes_panel.py** | `.git/index`, `.git/refs/heads/`, `.git/logs/HEAD`, project root, first-level dirs | 300ms + 3s polling | Staged/unstaged files list |
| **git_history_panel.py** | `.git/refs/heads/`, `.git/HEAD`, `.git/logs/HEAD` | 300ms | Commit history list |
| **notes_panel.py** | `notes/`, `docs/` | 300ms | Notes list |
| **tasks_panel.py** | `.vscode/` | 100ms | Tasks list |
| **snippets_service.py** | `~/.config/claude-companion/snippets/` | - | Snippets list |

### Overlap Analysis

```
.git/index:
  ├── file_tree.py        ✓
  └── git_changes_panel.py ✓

.git/refs/heads/:
  ├── git_changes_panel.py ✓
  └── git_history_panel.py ✓

.git/logs/HEAD:
  ├── git_changes_panel.py ✓
  ├── git_history_panel.py ✓
  └── file_tree.py        ✗ MISSING! (bug)

Working tree directories:
  ├── file_tree.py        ✓ (expanded dirs)
  └── git_changes_panel.py ✓ (root + first-level)
```

---

## Proposed Solution: FileMonitorService

### Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    FileMonitorService                        │
│                      (singleton)                             │
├─────────────────────────────────────────────────────────────┤
│  Monitors:                                                   │
│  ├── Git monitors (if git repo)                             │
│  │   ├── .git/index                                         │
│  │   ├── .git/refs/heads/                                   │
│  │   └── .git/logs/HEAD                                     │
│  ├── Working tree monitors                                   │
│  │   └── Dynamic: expanded directories from file_tree       │
│  └── Special directories                                     │
│      ├── notes/                                              │
│      ├── docs/                                               │
│      └── .vscode/                                            │
├─────────────────────────────────────────────────────────────┤
│  Signals (GObject):                                          │
│  ├── "git-index-changed"    → file_tree, git_changes        │
│  ├── "git-refs-changed"     → git_changes, git_history      │
│  ├── "git-log-changed"      → file_tree, git_changes,       │
│  │                            git_history                    │
│  ├── "working-tree-changed" → file_tree, git_changes        │
│  ├── "notes-changed"        → notes_panel                   │
│  └── "tasks-changed"        → tasks_panel                   │
├─────────────────────────────────────────────────────────────┤
│  Methods:                                                    │
│  ├── add_directory_monitor(path)                            │
│  ├── remove_directory_monitor(path)                         │
│  └── pause() / resume()  # for bulk operations              │
└─────────────────────────────────────────────────────────────┘
           │
           │ emits signals
           ▼
┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐
│    file_tree     │  │ git_changes_panel│  │ git_history_panel│
│                  │  │                  │  │                  │
│ connects to:     │  │ connects to:     │  │ connects to:     │
│ - git-index      │  │ - git-index      │  │ - git-refs       │
│ - git-log        │  │ - git-refs       │  │ - git-log        │
│ - working-tree   │  │ - git-log        │  │                  │
│                  │  │ - working-tree   │  │                  │
└──────────────────┘  └──────────────────┘  └──────────────────┘
```

### Signal Types

| Signal | Triggered by | Subscribers |
|--------|-------------|-------------|
| `git-index-changed` | `.git/index` modified | file_tree (icons), git_changes (list) |
| `git-refs-changed` | `.git/refs/heads/*` modified | git_changes, git_history |
| `git-log-changed` | `.git/logs/HEAD` modified | file_tree (icons), git_changes, git_history |
| `working-tree-changed(path)` | File created/deleted/modified | file_tree, git_changes |
| `notes-changed` | `notes/` or `docs/` modified | notes_panel |
| `tasks-changed` | `.vscode/tasks.json` modified | tasks_panel |

### Debounce Strategy

Single debounce per signal type (not per monitor):
- Git signals: 200ms
- Working tree: 150ms
- Notes/tasks: 300ms

This prevents multiple refreshes when one operation touches multiple files.

---

## Implementation Plan

### Phase 1: Create FileMonitorService (low risk)

**File:** `src/services/file_monitor_service.py`

```python
class FileMonitorService(GObject.Object):
    """Centralized file system monitoring service."""

    __gsignals__ = {
        "git-index-changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "git-refs-changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "git-log-changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "working-tree-changed": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        "notes-changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "tasks-changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self, project_path: Path):
        ...
```

**Tasks:**
- [ ] Create service class with GObject signals
- [ ] Implement git monitors (.git/index, refs, logs)
- [ ] Implement working tree monitors (dynamic add/remove)
- [ ] Implement notes/tasks monitors
- [ ] Add debounce logic per signal type
- [ ] Add cleanup on destroy

### Phase 2: Migrate git_changes_panel (medium risk)

**Why first:** Most complex monitoring, good test case.

**Tasks:**
- [ ] Remove internal monitors from git_changes_panel
- [ ] Remove 3-second polling (service provides reliable monitoring)
- [ ] Connect to service signals: git-index, git-refs, git-log, working-tree
- [ ] Test: stage, unstage, commit, file edit

### Phase 3: Migrate file_tree (medium risk)

**Tasks:**
- [ ] Remove `_git_index_monitor`
- [ ] Keep `_file_monitors` for expanded directories but delegate to service
- [ ] Connect to service signals: git-index, git-log, working-tree
- [ ] Test: expand folder, edit file, commit
- [ ] **This fixes the bug:** commits will now update icons

### Phase 4: Migrate git_history_panel (low risk)

**Tasks:**
- [ ] Remove internal monitors
- [ ] Connect to service signals: git-refs, git-log
- [ ] Test: commit, branch switch

### Phase 5: Migrate notes_panel (low risk)

**Tasks:**
- [ ] Remove internal monitors
- [ ] Connect to service signal: notes-changed
- [ ] Test: create/edit/delete note

### Phase 6: Migrate tasks_panel (low risk)

**Tasks:**
- [ ] Remove internal monitor
- [ ] Connect to service signal: tasks-changed
- [ ] Test: edit tasks.json

### Phase 7: Cleanup

- [ ] Remove unused imports from migrated files
- [ ] Update CLAUDE.md architecture docs
- [ ] Performance test: compare memory/CPU before/after

---

## Not in Scope

**snippets_service.py** monitors `~/.config/claude-companion/snippets/` which is:
- Outside project directory
- App-wide, not per-project
- Already a service

Keep it separate - it's a different scope.

---

## Risk Assessment

| Risk | Mitigation |
|------|------------|
| Breaking existing functionality | Migrate one component at a time, test each |
| Race conditions | Single debounce per signal prevents duplicate refreshes |
| Memory leaks | Proper cleanup in service destroy, weak refs if needed |
| Performance regression | Benchmark before/after |

---

## Quick Fix Alternative

If full refactoring is delayed, fix the immediate bug:

**Add to file_tree.py** (5 min fix):
```python
# Monitor .git/logs/HEAD for commit operations
git_logs_head = self.root_path / ".git" / "logs" / "HEAD"
if git_logs_head.exists():
    gfile = Gio.File.new_for_path(str(git_logs_head))
    self._git_logs_monitor = gfile.monitor_file(...)
```

This fixes commits not updating file_tree, but doesn't address the architectural issues.

---

## Decision Needed

1. **Full refactoring** - cleaner architecture, ~2-3 hours work
2. **Quick fix only** - just fix the bug, ~5 min work
3. **Hybrid** - quick fix now, refactoring later

Recommendation: **Option 3 (Hybrid)** - fix the bug immediately, plan refactoring for later session.
