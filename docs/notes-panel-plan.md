# Notes Panel Implementation Plan

## Overview

Add a new "Notes" tab to the sidebar with three sections:
1. **My Notes** - user's markdown notes from `notes/` folder
2. **Docs** - project documentation from `docs/` folder + `CLAUDE.md`
3. **TODOs** - TODO/FIXME/etc found in code and markdown files

## Checkpoints

### Checkpoint 1: Basic NotesPanel widget
- [ ] Create `src/widgets/notes_panel.py`
- [ ] Basic structure with header and 3 expanders
- [ ] Add to `src/widgets/__init__.py`

### Checkpoint 2: My Notes section
- [ ] Scan `notes/*.md` files
- [ ] Display as clickable list
- [ ] Create `notes/` folder if not exists
- [ ] Emit `open-file` signal on click

### Checkpoint 3: Docs section
- [ ] Scan `docs/*.md` files
- [ ] Include `CLAUDE.md` from project root
- [ ] Display as clickable list
- [ ] Emit `open-file` signal on click

### Checkpoint 4: TODOs section
- [ ] Grep pattern: `TODO:|FIXME:|HACK:|XXX:|NOTE:`
- [ ] Search in: `*.py, *.js, *.ts, *.tsx, *.jsx, *.md, *.txt`
- [ ] Group results by file
- [ ] Show line number + preview
- [ ] Emit `open-file-at-line` signal on click

### Checkpoint 5: Integration
- [ ] Add NotesPanel to sidebar in `project_window.py`
- [ ] Connect `open-file` signal
- [ ] Connect `open-file-at-line` signal
- [ ] Add as 4th tab after Claude

### Checkpoint 6: New Note functionality
- [ ] Add "+ New note" button in header
- [ ] Dialog to enter filename
- [ ] Create file in `notes/` folder
- [ ] Open in editor after creation

### Checkpoint 7: Polish
- [ ] Auto-refresh on file changes (file monitor)
- [ ] Manual refresh button
- [ ] Empty state messages
- [ ] CSS styling

## Technical Details

### Signals
```python
__gsignals__ = {
    "open-file": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    "open-file-at-line": (GObject.SignalFlags.RUN_FIRST, None, (str, int, str)),
}
```

### Grep command (ripgrep)
```bash
rg --line-number --no-heading "TODO:|FIXME:|HACK:|XXX:|NOTE:" \
   --type py --type js --type ts --type md \
   --glob "!node_modules" --glob "!.venv" --glob "!.git"
```

### UI Structure
```
┌─ Notes ─────────────────────┐
│ [+ New note]    [⟳ Refresh] │
│                             │
│ ▼ My Notes (3)              │
│   roadmap.md                │
│   ideas.md                  │
│   bugs.md                   │
│                             │
│ ▼ Docs (5)                  │
│   CLAUDE.md                 │
│   architecture.md           │
│   api-reference.md          │
│                             │
│ ▼ TODOs (12)                │
│   ┌ src/main.py (3)         │
│   │  42: TODO: refactor     │
│   │  89: FIXME: race cond   │
│   └ docs/plan.md (2)        │
│      15: TODO: add section  │
└─────────────────────────────┘
```

## Progress

- Started: 2025-12-12
- Status: Completed

### Implementation
- `src/widgets/notes_panel.py` - Main widget
- `src/project_window.py` - Integration (4th sidebar tab)
