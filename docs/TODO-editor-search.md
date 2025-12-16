# TODO: Editor Search & Replace

## Overview
Add in-file search and replace functionality to the code editor, similar to VS Code's Ctrl+F.

## Technical Approach

### Use GtkSource Built-in Search
GtkSourceView has native search support via:
- `GtkSource.SearchContext` - manages search state and highlighting
- `GtkSource.SearchSettings` - configuration (case, regex, word boundaries)

### UI Components

**EditorSearchBar widget** (collapsible, appears above editor):
```
┌─────────────────────────────────────────────────────────────┐
│ 🔍 [Search field_______] [↑] [↓]  3 of 15   [Aa] [W] [.*] X │
│ ↔  [Replace field______] [Replace] [Replace All]            │
└─────────────────────────────────────────────────────────────┘
```

- Search field with placeholder "Search (Ctrl+F)"
- Navigation: Previous (↑), Next (↓)
- Match counter: "3 of 15" or "No results"
- Options toggles:
  - `[Aa]` - Case sensitive
  - `[W]` - Whole word
  - `[.*]` - Regex mode
- Replace row (expandable via Ctrl+H or toggle)
- Close button (X) or Escape key

### Keyboard Shortcuts
| Shortcut | Action |
|----------|--------|
| Ctrl+F | Open search bar, focus search field |
| Ctrl+H | Open search bar with replace visible |
| Enter | Find next |
| Shift+Enter | Find previous |
| Escape | Close search bar |
| Ctrl+Shift+Enter | Replace and find next |
| Ctrl+Alt+Enter | Replace all |

### Implementation Steps

1. **Create `EditorSearchBar` widget** (`src/widgets/editor_search_bar.py`)
   - Gtk.Revealer for show/hide animation
   - Search/replace entries
   - Option toggle buttons
   - Match counter label

2. **Integrate with FileEditor**
   - Add search bar above scrolled window
   - Create GtkSource.SearchContext on demand
   - Handle keyboard shortcuts (Ctrl+F, Ctrl+H)
   - Sync search highlighting with buffer

3. **Search logic**
   ```python
   self.search_settings = GtkSource.SearchSettings()
   self.search_settings.set_search_text(query)
   self.search_settings.set_case_sensitive(case_sensitive)
   self.search_settings.set_regex_enabled(use_regex)
   self.search_settings.set_at_word_boundaries(whole_word)

   self.search_context = GtkSource.SearchContext(
       buffer=self.buffer,
       settings=self.search_settings
   )

   # Navigate
   self.search_context.forward(cursor_iter)  # Returns (found, start, end, wrapped)
   self.search_context.backward(cursor_iter)

   # Replace
   self.search_context.replace(start, end, replacement, -1)
   self.search_context.replace_all(replacement, -1)
   ```

4. **Undo/Redo integration**
   - Replace operations should be undoable
   - "Replace All" should be single undo action (use `buffer.begin_user_action()` / `end_user_action()`)

### Additional Features (Future)

- [ ] Search history (dropdown with recent searches)
- [ ] Preserve search across file switches
- [ ] Find in selection only
- [ ] Incremental search highlighting as you type
- [ ] Status bar integration (show match count)

## Priority
Medium - useful for editing but global search covers most use cases.

## Dependencies
- GtkSource.SearchContext (already available via gtksourceview5)
- No external dependencies needed
