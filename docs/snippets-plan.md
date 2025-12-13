# Snippets Implementation Plan

## Overview

Quick text snippets for Claude terminal. Management in Notes panel, usage buttons below Claude terminal.

## User Flow

1. **Add snippet**: Notes → Snippets section → [+] button → enter name → opens in editor
2. **Edit snippet**: Notes → click snippet → opens .md file in editor
3. **Delete snippet**: Delete .md file from snippets folder
4. **Use snippet**: Claude terminal → click button → text inserted (no Enter)

## Storage

Individual .md files in `~/.config/claude-companion/snippets/`:
- Filename (without .md) = button label (spaces allowed)
- File content = text to insert (with markdown formatting)

```
~/.config/claude-companion/snippets/
├── Plan.md            # содержит: "создай детальный план..."
├── Commit.md          # содержит: "сделай саммари..."
├── Fix.md             # содержит: "исправь ошибку"
└── My Custom.md       # пробелы в имени разрешены
```

## UI Design

### Notes Panel - Snippets Section
```
▼ Snippets (4)                    [+]
  ┌─────────────────────────────────┐
  │ 📄 Plan                         │  ← click to open in editor
  │ 📄 Commit                       │
  │ 📄 Fix                          │
  │ 📄 My Custom                    │
  └─────────────────────────────────┘
```

### Claude Terminal - Snippets Bar
Horizontal scrollable bar below terminal (~36px height):
```
┌─ Claude ──────────────────────────┐
│                                   │
│  Terminal content...              │
│                                   │
├───────────────────────────────────┤
│ ◀ [Plan] [Commit] [Fix] [My C..] ▶│
└───────────────────────────────────┘
```
- No icons, text only
- Horizontal scroll when many snippets
- Click inserts text + returns focus to terminal

## Checkpoints

### Checkpoint 1: SnippetsService (file-based)
- [x] Create `src/services/snippets_service.py`
- [x] Store snippets as .md files in `~/.config/claude-companion/snippets/`
- [x] Default snippets (Plan, Commit, Fix, Summary)
- [x] Methods: get_all(), add(), delete(), get_snippets_dir()
- [x] GObject signal on change
- [x] File monitor for external changes

### Checkpoint 2: Snippets Section in Notes Panel
- [x] Add "Snippets" expander to NotesPanel
- [x] List snippets as clickable file rows (like notes)
- [x] [+] button creates new .md file and opens in editor
- [x] Click on snippet opens in editor

### Checkpoint 3: Snippets Bar Widget
- [x] Create `src/widgets/snippets_bar.py`
- [x] Horizontal ScrolledWindow with button box
- [x] Compact style (small buttons, ~36px height)
- [x] Horizontal scroll when overflow
- [x] Signal: `snippet-clicked(text)`
- [x] Refresh on snippets change

### Checkpoint 4: Integration with Claude Terminal
- [x] Add SnippetsBar below Claude terminal in project_window.py
- [x] Connect `snippet-clicked` → `terminal.feed_child()`
- [x] Return focus to terminal after click (grab_focus)
- [x] Bar updates when snippets change

## Technical Details

### SnippetsService
```python
class SnippetsService(GObject.Object):
    __gsignals__ = {
        "changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def get_all(self) -> list[dict]  # returns {label, text, path}
    def add(self, label: str, text: str) -> str  # returns path
    def delete(self, label: str) -> bool
    def get_snippets_dir(self) -> Path
```

### SnippetsBar
```python
class SnippetsBar(Gtk.Box):
    __gsignals__ = {
        "snippet-clicked": (GObject.SignalFlags.RUN_FIRST, None, (str,)),  # text
    }

    # Horizontal ScrolledWindow + Box with buttons
    # Height: ~36px fixed
    # Scroll policy: AUTOMATIC horizontal, NEVER vertical
```

### Default Snippets
```python
DEFAULT_SNIPPETS = {
    "Plan": "create a detailed plan with checkpoints in /docs",
    "Commit": "make a summary of the changes and commit",
    "Fix": "fix a bug",
    "Summary": "make a short summary of what was done",
}
```

## Progress

- Started: 2025-12-13
- Status: Completed (v2 - file-based)

### Implementation
- `src/services/snippets_service.py` - Singleton with .md file storage
- `src/widgets/snippets_bar.py` - Horizontal scrollable button bar
- `src/widgets/notes_panel.py` - Snippets section as file list
- `src/project_window.py` - Integrated bar below Claude terminal

### v2 Changes (file-based)
- Switched from JSON to individual .md files
- Filename = label (spaces allowed)
- File content = text to insert (markdown supported)
- Edit by opening file in editor (no dialogs)
- File monitor watches for external changes
- Fixed focus bug: grab_focus() after snippet click
