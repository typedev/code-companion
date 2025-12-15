# Plan: Rules Management

## Status: COMPLETED

## Overview

Add Rules functionality alongside Snippets:

**Snippets** (existing):
- Quick command templates for Claude
- Buttons that insert text
- Stored in `~/.config/claude-companion/snippets/*.md`

**Rules** (new):
- Longer guidelines/policies for CLAUDE.md
- User browses, reads, copies manually
- Stored in `~/.config/claude-companion/rules/*.md`
- Examples: language policy, linter rules, coding standards

## UI Design

Add "Rules" section to Notes panel (alongside My Notes, Docs, TODOs):

```
Notes Panel:
┌─────────────────────┐
│ [My Notes] [Docs]   │
│ [TODOs]   [Rules]   │  ← New tab
├─────────────────────┤
│ Rules:              │
│  Language Policy    │
│  Linter Rules       │
│  Code Style         │
│  + Add Rule         │
└─────────────────────┘
```

When rule selected → show in main content area with:
- Rule content (markdown rendered or plain)
- "Copy to Clipboard" button
- "Open in Editor" button (to edit)
- User pastes into CLAUDE.md manually

## Default Rules

Provide starter rules:
1. **Language Policy** - "All code comments and documentation in English"
2. **Linter Rules** - "Run ruff and mypy before committing"
3. **Planning** - "Create detailed plans in docs/ before implementing"

## Checkpoints

### Checkpoint 1: RulesService
- [ ] Create `src/services/rules_service.py`
- [ ] Similar to SnippetsService but for `rules/` directory
- [ ] Default rules with useful templates
- [ ] File monitoring for external changes

### Checkpoint 2: Rules in Notes Panel
- [ ] Add "Rules" tab to NotesPanel
- [ ] List rules with click to view
- [ ] Right-click to delete (like snippets)

### Checkpoint 3: Rules Detail View
- [ ] Show rule content when selected
- [ ] Copy button with toast notification
- [ ] Edit button to open in file editor

### Checkpoint 4: Add Rule Dialog
- [ ] "+" button to create new rule
- [ ] Name + content input
- [ ] Save to rules directory

---

## Files to Create/Modify

**New:**
- `src/services/rules_service.py`

**Modified:**
- `src/widgets/notes_panel.py` - add Rules tab
- `src/services/__init__.py` - export RulesService
