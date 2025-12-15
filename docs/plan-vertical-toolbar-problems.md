# Plan: Vertical Toolbar + Problems Panel

## Status: ✅ COMPLETED

## Overview

Refactor sidebar tab switching from horizontal buttons to vertical toolbar, and add Problems panel for ruff/mypy diagnostics.

## Implementation Summary

All checkpoints completed:
- ✅ Vertical toolbar with F/G/C/N/P buttons
- ✅ ProblemsService with ruff/mypy integration
- ✅ ProblemsPanel in sidebar
- ✅ ProblemsDetailView in main content
- ✅ Copy functionality (single/all problems)
- ✅ Documentation updated

## Files Created/Modified

**New files**:
- `src/services/problems_service.py` - Linter runner and JSON parser
- `src/widgets/problems_panel.py` - Sidebar panel with file list
- `src/widgets/problems_detail_view.py` - Detail view with problems + code

**Modified files**:
- `src/project_window.py` - Vertical toolbar, Problems integration
- `src/services/__init__.py` - Export new classes
- `src/widgets/__init__.py` - Export new classes
- `CLAUDE.md` - Updated documentation

## Final UI Structure

```
┌───┬──────────────┬─────────────────────────┐
│ F │              │                         │
│ G │  Sidebar     │  Main content           │
│ C │  content     │                         │
│ N │              │                         │
│ P │              │                         │
└───┴──────────────┴─────────────────────────┘
```

Problems detail view:
```
┌───┬──────────────┬─────────────────────────┐
│   │ Files:       │ Problems:         [Copy]│
│   │ ─────────    │  :45 F401 unused import │
│ P │ file.py (3)  │  :67 E501 line too long │
│   │ main.py (2)  │                         │
│   │              ├─────────────────────────┤
│   │              │ Code preview            │
│   │              │ with highlighted line   │
└───┴──────────────┴─────────────────────────┘
```

---

## Checkpoints

### Checkpoint 1: Vertical Toolbar Structure ✅
- [x] Created `_build_vertical_toolbar()` method
- [x] Modified `_build_sidebar()` to remove horizontal switcher
- [x] Updated main layout: `Box[Toolbar | Paned[Sidebar | Content]]`
- [x] Verified all tabs work correctly

### Checkpoint 2: Problems Service ✅
- [x] Created `Problem` and `FileProblems` dataclasses
- [x] Implemented `ProblemsService.run_ruff()` with JSON parsing
- [x] Implemented `ProblemsService.run_mypy()` with JSON parsing
- [x] Added `get_all_problems()` method with file grouping

### Checkpoint 3: Problems Panel (Sidebar) ✅
- [x] Created `ProblemsPanel` class with file list
- [x] Added `file-selected` GObject signal
- [x] Implemented lazy loading and refresh logic
- [x] Added "P" button to vertical toolbar

### Checkpoint 4: Problems Detail View ✅
- [x] Created `ProblemsDetailView` with vertical paned layout
- [x] Implemented problems list with severity icons
- [x] Implemented code preview with syntax highlighting
- [x] Added problem line highlighting (background color)
- [x] Connected to ProblemsPanel with single-tab reuse

### Checkpoint 5: Copy Functionality ✅
- [x] Copy single problem (button in row)
- [x] Copy all problems for file (button in header)
- [x] Copy all problems (button in sidebar)

### Checkpoint 6: Polish & Integration ✅
- [x] Updated CLAUDE.md with new features
- [x] Added v0.7.2 milestone

---

## Future (not in this plan)

- Rules/Snippets refactoring
- Pre-commit hook generation
- Icons instead of letters in toolbar
- Auto-refresh on file save
- Problem count badge on "P" button
