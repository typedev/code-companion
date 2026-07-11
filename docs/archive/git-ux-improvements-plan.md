# Git UX Improvements Plan

This plan covers three related improvements to the Git integration:
1. External Git changes monitoring
2. Material Design default folder icons
3. Tree view for Git changes panel

---

## Problem 1: External Git Changes Not Detected

**Issue**: When commits are made by external programs (Claude Code, terminal, IDE), the Git Changes panel and File Tree don't update automatically.

**Root cause**: `GitChangesPanel._on_file_changed()` filters out `.git/` internal files, and the `.git` monitor only watches the root directory, not nested files like `index` or `refs/`.

**Solution**: Add targeted monitoring of specific git files (like `GitHistoryPanel` does).

---

## Problem 2: Default Folder Icons

**Issue**: Default folders use GTK4 system icons instead of Material Design icons.

**Solution**: Copy `folder-base.svg` and `folder-base-open.svg` from vscode-material-icon-theme as default `folder.svg` and `folder-open.svg`.

---

## Problem 3: Flat File List in Git Changes

**Issue**: Git Changes shows files as a flat list, making it hard to navigate when many files changed.

**Solution**: Group files by directory with collapsible sections (Variant B from discussion).

---

## Implementation Plan

### Checkpoint 1: Default Folder Icons
**Status**: [x] Completed

**Files to modify**:
- `src/resources/icons/` — add new icons

**Tasks**:
- [ ] 1.1 Copy `folder-base.svg` → `folder.svg`
- [ ] 1.2 Copy `folder-base-open.svg` → `folder-open.svg`
- [ ] 1.3 Test that default folders now show Material Design icons

**Verification**:
- Open app, expand any folder not in FOLDER_MAP (e.g., random named folder)
- Should show brown Material Design folder icon instead of GTK system icon

---

### Checkpoint 2: External Git Monitoring
**Status**: [x] Completed

**Files to modify**:
- `src/widgets/git_changes_panel.py`
- `src/widgets/file_tree.py`

**Tasks**:
- [ ] 2.1 Study `GitHistoryPanel` monitoring pattern (lines 38-86)
- [ ] 2.2 Add `.git/index` monitoring to `GitChangesPanel`
- [ ] 2.3 Add `.git/refs/heads/` monitoring to `GitChangesPanel`
- [ ] 2.4 Add `.git/logs/HEAD` monitoring to `GitChangesPanel`
- [ ] 2.5 Update `FileTree` to also refresh on git index changes
- [ ] 2.6 Test with external commit (Claude Code or terminal)

**Implementation details**:

```python
# In GitChangesPanel._setup_file_monitor()

def _setup_file_monitor(self):
    """Setup file monitoring for git changes."""
    self._monitors = []
    self._refresh_pending = False

    git_dir = self.project_path / ".git"
    if not git_dir.exists():
        return

    # Monitor .git/index for stage/unstage changes
    index_file = git_dir / "index"
    if index_file.exists():
        self._add_monitor(index_file, is_file=True)

    # Monitor .git/refs/heads/ for new commits
    refs_dir = git_dir / "refs" / "heads"
    if refs_dir.exists():
        self._add_monitor(refs_dir)

    # Monitor .git/logs/HEAD for all operations
    logs_head = git_dir / "logs" / "HEAD"
    if logs_head.exists():
        self._add_monitor(logs_head, is_file=True)

    # Keep working tree monitor for local file changes
    self._add_monitor(self.project_path)

def _add_monitor(self, path: Path, is_file: bool = False):
    """Add a file monitor."""
    try:
        gfile = Gio.File.new_for_path(str(path))
        if is_file:
            monitor = gfile.monitor_file(Gio.FileMonitorFlags.NONE, None)
        else:
            monitor = gfile.monitor_directory(Gio.FileMonitorFlags.NONE, None)
        monitor.connect("changed", self._on_file_changed)
        self._monitors.append(monitor)
    except GLib.Error:
        pass
```

**Verification**:
1. Open app with git repo
2. In separate terminal: `echo "test" >> test.txt && git add test.txt`
3. Git Changes panel should auto-update showing staged file
4. Run: `git commit -m "test"`
5. Git Changes panel should show "No changes"

---

### Checkpoint 3: Tree View for Git Changes
**Status**: [x] Completed

**Files to modify**:
- `src/widgets/git_changes_panel.py`

**Tasks**:
- [ ] 3.1 Create `_group_files_by_directory()` helper method
- [ ] 3.2 Create `_create_directory_row()` for collapsible directory headers
- [ ] 3.3 Modify `_add_section()` to use grouped structure
- [ ] 3.4 Add expand/collapse state management
- [ ] 3.5 Style directory headers (indent, arrow icon)
- [ ] 3.6 Test with multiple files in different directories

**Implementation details**:

```python
def _group_files_by_directory(self, files: list[GitFileStatus]) -> dict[str, list[GitFileStatus]]:
    """Group files by their parent directory.

    Returns:
        Dict mapping directory path to list of files in that directory.
        Empty string key "" for root-level files.
    """
    groups: dict[str, list[GitFileStatus]] = {}
    for file_status in files:
        path = Path(file_status.path)
        parent = str(path.parent) if path.parent != Path(".") else ""
        if parent not in groups:
            groups[parent] = []
        groups[parent].append(file_status)

    # Sort directories, root first
    return dict(sorted(groups.items(), key=lambda x: (x[0] != "", x[0])))

def _create_directory_row(self, dir_path: str, file_count: int, is_expanded: bool) -> Gtk.Box:
    """Create a collapsible directory header row."""
    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
    box.set_margin_start(4)
    box.set_margin_top(4)
    box.set_margin_bottom(2)

    # Expand/collapse arrow
    arrow = Gtk.Image.new_from_icon_name(
        "pan-down-symbolic" if is_expanded else "pan-end-symbolic"
    )
    arrow.add_css_class("dim-label")
    box.append(arrow)

    # Folder icon (from IconCache)
    folder_icon = self._get_folder_icon(dir_path)
    box.append(folder_icon)

    # Directory name with file count
    label = Gtk.Label(label=f"{dir_path}/ ({file_count})")
    label.set_xalign(0)
    label.add_css_class("dim-label")
    box.append(label)

    return box
```

**UI Structure**:
```
Staged (5)
  ▾ src/widgets/ (3)
      M file_tree.py
      M git_changes_panel.py
      A new_widget.py
  ▾ src/services/ (2)
      M git_service.py
      M icon_cache.py

Changes (2)
    M README.md           ← root level files (no directory header)
  ▾ docs/ (1)
      ? new-plan.md
```

**State management**:
- Store expanded directories in `self._expanded_dirs: set[str]`
- Default: all directories expanded
- Click on directory row toggles expand/collapse
- Preserve state across refreshes

**Verification**:
1. Modify files in different directories
2. Git Changes should show tree structure
3. Click directory header to collapse/expand
4. Stage/unstage should work correctly
5. Refresh should preserve expand state

---

## Progress Tracking

| Checkpoint | Status | Notes |
|------------|--------|-------|
| 1. Folder Icons | [x] | Copied folder-base.svg → folder.svg |
| 2. Git Monitoring | [x] | Added .git/index, refs/heads, logs/HEAD monitors |
| 3. Tree View | [x] | Grouped by directory with collapse/expand |

---

## Testing Checklist

- [ ] Default folder icons show Material Design style
- [ ] External `git add` detected
- [ ] External `git commit` detected
- [ ] External `git reset` detected
- [ ] Claude Code commits detected
- [ ] Tree view shows correct hierarchy
- [ ] Collapse/expand works
- [ ] Stage/unstage buttons work in tree view
- [ ] File click opens diff in tree view
- [ ] No performance regression with many files
