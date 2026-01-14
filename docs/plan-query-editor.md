# Query Editor Implementation Plan

## Overview

A collapsible multi-line text editor below the Claude terminal for composing large queries with Markdown syntax highlighting and spell checking.

## Problem

Writing large, complex queries directly in the Claude Code terminal is inconvenient:
- No syntax highlighting
- No spell checking (typos waste tokens)
- Hard to edit multi-line text
- No way to review before sending

## Solution

Add a collapsible Query Editor panel below the terminal with:
- GtkSourceView with Markdown highlighting
- libspelling integration for spell checking
- Language auto-detection with manual override
- Send button to paste text into terminal

## UI Design

### Collapsed State (default)
```
┌─────────────────────────────────────────────────────┐
│  VTE Terminal (Claude Code)                         │
│  ...                                                │
│  > claude                                           │
├─────────────────────────────────────────────────────┤
│ [▶ Query Editor]                                    │
├─────────────────────────────────────────────────────┤
│ [Snippet1] [Snippet2] [Snippet3]        ← snippets  │
└─────────────────────────────────────────────────────┘
```

### Expanded State
```
┌─────────────────────────────────────────────────────┐
│  VTE Terminal (Claude Code)                         │
│  ...                                                │
├─────────────────────────────────────────────────────┤
│ [▼ Query Editor]                        [🌐 auto▾] │
├─────────────────────────────────────────────────────┤
│ │ # Implement feature X                           │ │
│ │                                                 │ │
│ │ Please add authentication to the API:          │ │
│ │ - JWT tokens                                   │ │
│ │ - Refresh token rotation                       │ │
│ │                                                 │ │
│ │ ```python                                       │ │
│ │ # expected interface                           │ │
│ │ ```                                            │ │
│ │                                                 │ │  ← 10 lines, resizable
├─────────────────────────────────────────────────────┤
│                                   [Clear] [⏎ Send] │
├─────────────────────────────────────────────────────┤
│ [Snippet1] [Snippet2] [Snippet3]        ← snippets  │
└─────────────────────────────────────────────────────┘
```

## Requirements

| Feature | Implementation |
|---------|----------------|
| **Syntax highlighting** | GtkSourceView + Markdown scheme |
| **Spell checking** | libspelling (TextBufferAdapter) |
| **Language detection** | Auto by default, manual override |
| **Language persistence** | SettingsService: `editor.spellcheck_language` |
| **Default height** | 10 lines (~200px) |
| **Resizable** | Gtk.Paned or drag handle |
| **Initial state** | Collapsed |
| **Send action** | Paste to terminal via feed_child() |
| **After send** | Keep text (don't clear) |
| **Clear button** | Empty the editor |

## Checkpoints

### Checkpoint 1: Basic QueryEditor Widget
- [ ] Create `src/widgets/query_editor.py`
- [ ] GtkSourceView with Markdown language
- [ ] Use existing syntax scheme from settings
- [ ] 10 lines default height (~200px)
- [ ] ScrolledWindow for overflow
- [ ] Test: widget displays and accepts text input

### Checkpoint 2: Collapsible Container
- [ ] Create expander header with toggle button
- [ ] Collapsed state: show only header bar
- [ ] Expanded state: show editor + buttons
- [ ] Animate expand/collapse (Gtk.Revealer)
- [ ] Remember expanded state in session (not persisted)
- [ ] Test: toggle works, smooth animation

### Checkpoint 3: Spell Checking Integration
- [ ] Add libspelling TextBufferAdapter
- [ ] Enable spell checking by default
- [ ] Red underline for misspelled words
- [ ] Right-click menu with suggestions
- [ ] Test: misspelled words are underlined, suggestions work

### Checkpoint 4: Language Selection
- [ ] Add language dropdown button in header
- [ ] List available languages from Spelling.Provider
- [ ] "Auto" option for auto-detection
- [ ] Manual language selection
- [ ] Save selection to SettingsService (`editor.spellcheck_language`)
- [ ] Load saved language on startup
- [ ] Test: language switch works, persists across restart

### Checkpoint 5: Action Buttons
- [ ] Add "Clear" button - clears editor text
- [ ] Add "Send" button - emits signal with text
- [ ] Button bar at bottom of editor
- [ ] Style: suggested-action for Send
- [ ] Test: Clear empties editor, Send emits signal

### Checkpoint 6: Terminal Integration
- [ ] Add QueryEditor to terminal_view.py layout
- [ ] Position: between terminal and snippets bar
- [ ] Connect Send signal to terminal.feed_child()
- [ ] Add newline after text when sending
- [ ] Return focus to terminal after send
- [ ] Test: text appears in terminal, can execute

### Checkpoint 7: Resizable Height
- [ ] Add drag handle or use Gtk.Paned
- [ ] Min height: 5 lines (~100px)
- [ ] Max height: 50% of terminal area
- [ ] Test: can resize editor height

### Checkpoint 8: Polish & Edge Cases
- [ ] Handle empty text on Send (do nothing or warning?)
- [ ] Escape special characters if needed
- [ ] Handle very long text (paste in chunks?)
- [ ] Keyboard shortcut for expand/collapse (optional)
- [ ] Test: all edge cases handled

## Technical Details

### New Setting
```python
# In SettingsService
"editor.spellcheck_language": "auto"  # or "en_US", "ru_RU", etc.
```

### QueryEditor Widget
```python
class QueryEditor(Gtk.Box):
    """Collapsible multi-line editor with spell checking."""

    __gsignals__ = {
        "send-requested": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    def __init__(self):
        # Header bar with toggle + language selector
        # Revealer with editor content
        # GtkSourceView with Markdown
        # libspelling adapter
        # Button bar (Clear, Send)

    def get_text(self) -> str: ...
    def set_text(self, text: str): ...
    def clear(self): ...
    def set_expanded(self, expanded: bool): ...
    def is_expanded(self) -> bool: ...
    def set_language(self, code: str): ...  # "auto" or language code
```

### libspelling Integration
```python
import gi
gi.require_version('Spelling', '1')
from gi.repository import Spelling

# Setup
checker = Spelling.Checker.get_default()
adapter = Spelling.TextBufferAdapter.new(source_buffer, checker)
adapter.set_enabled(True)
source_view.set_extra_menu(adapter.get_menu_model())

# Change language
provider = Spelling.Provider.get_default()
if language_code == "auto":
    # Use default/system language
    checker.set_language(None)
else:
    language = provider.get_language(language_code)
    if language:
        checker.set_language(language)
```

### Terminal Integration
```python
# In terminal_view.py or project_window.py
def on_send_requested(self, editor, text):
    if text.strip():
        # Send text to terminal
        self.terminal.feed_child(text.encode() + b"\n")
        self.terminal.grab_focus()
```

## Dependencies

- libspelling (already installed: `libspelling-0.4.9`)
- hunspell dictionaries (en_US installed, ru needs `dnf install hunspell-ru`)

## Files to Create/Modify

| File | Action |
|------|--------|
| `src/widgets/query_editor.py` | **Create** - new widget |
| `src/widgets/terminal_view.py` | **Modify** - add QueryEditor |
| `src/services/settings_service.py` | **Modify** - add spellcheck_language setting |

## Progress

- Created: 2025-01-14
- Status: **Completed**

### Checkpoint Status
- [x] Checkpoint 1: Basic QueryEditor Widget
- [x] Checkpoint 2: Collapsible Container
- [x] Checkpoint 3: Spell Checking Integration
- [x] Checkpoint 4: Language Selection
- [x] Checkpoint 5: Action Buttons
- [x] Checkpoint 6: Terminal Integration
- [x] Checkpoint 7: Resizable Height (scroll-based, min 200px, max 400px)
- [x] Checkpoint 8: Polish & Edge Cases

### Implementation Notes
- Created `src/widgets/query_editor.py` with all features
- Integrated into `src/project_window.py` between terminal and snippets bar
- Added `editor.spellcheck_language` setting (default: "auto")
- Uses libspelling for spell checking with right-click suggestions
- Visual separator and frame around editor for clarity
