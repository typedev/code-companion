# Settings Implementation Plan

## Overview

Add settings system with preferences dialog. Store in `~/.config/claude-companion/settings.json`.

## Settings Scope

1. **Theme**: dark / light / system
2. **Syntax scheme**: GtkSourceView schemes (Dracula, Monokai, Solarized, etc.)
3. **Font**: family, size, line height (applies to editor + terminal)
4. **Editor**: tab size, spaces vs tabs
5. **File tree**: show hidden files
6. **Window**: remember size/position

## Storage

```json
{
  "appearance": {
    "theme": "system",
    "syntax_scheme": "Adwaita-dark"
  },
  "editor": {
    "font_family": "Monospace",
    "font_size": 12,
    "line_height": 1.4,
    "tab_size": 4,
    "insert_spaces": true
  },
  "file_tree": {
    "show_hidden": false
  },
  "window": {
    "width": 1200,
    "height": 800,
    "x": null,
    "y": null,
    "maximized": false
  }
}
```

## Checkpoints

### Checkpoint 1: Settings Service
- [x] Create `src/services/settings_service.py`
- [x] Load/save JSON from `~/.config/claude-companion/settings.json`
- [x] Default values for all settings
- [x] Singleton pattern (like ToastService)
- [x] Signal emission on setting change

### Checkpoint 2: Apply Theme
- [x] Read `appearance.theme` on startup
- [x] Apply via `Adw.StyleManager.set_color_scheme()`
- [x] Options: ADW_COLOR_SCHEME_DEFAULT (system), FORCE_DARK, FORCE_LIGHT

### Checkpoint 3: Apply Syntax Scheme
- [x] Get available schemes from `GtkSource.StyleSchemeManager`
- [x] Apply to all SourceView widgets (FileEditor, CodeView)
- [x] Store scheme ID in settings

### Checkpoint 4: Apply Font Settings
- [x] Apply font to FileEditor (GtkSourceView)
- [x] Apply font to Terminal (VTE)
- [x] Apply line height via CSS or Pango attributes
- [x] Font format: "Monospace 12" or CSS "font-family: ...; font-size: ...px"

### Checkpoint 5: Apply Editor Settings
- [x] Tab size: `source_view.set_tab_width()`
- [x] Spaces vs tabs: `source_view.set_insert_spaces_instead_of_tabs()`

### Checkpoint 6: Apply File Tree Settings
- [x] Show/hide hidden files in FileTree
- [x] Add method `set_show_hidden(bool)`
- [x] Re-scan tree on change

### Checkpoint 7: Window State
- [x] Save window size on close
- [x] Save maximized state
- [x] Restore on open

### Checkpoint 8: Preferences Dialog
- [x] Create `src/widgets/preferences_dialog.py`
- [x] Use `Adw.PreferencesDialog`
- [x] Groups: Appearance, Editor, Files
- [x] Theme: `Adw.ComboRow` (System/Light/Dark)
- [x] Syntax scheme: `Adw.ComboRow` (list from StyleSchemeManager)
- [x] Font: `Adw.EntryRow`
- [x] Font size: `Adw.SpinRow`
- [x] Line height: `Adw.SpinRow`
- [x] Tab size: `Adw.SpinRow`
- [x] Insert spaces: `Adw.SwitchRow`
- [x] Show hidden: `Adw.SwitchRow`

### Checkpoint 9: Integration
- [x] Add "Preferences" button to header
- [x] Apply settings on startup
- [x] Apply settings live on change (no restart needed)
- [x] Connect SettingsService to all affected widgets

## Technical Details

### SettingsService API
```python
class SettingsService:
    _instance = None

    @classmethod
    def get_instance(cls) -> "SettingsService":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def get(self, key: str, default=None) -> Any:
        """Get setting by dot-notation key: 'appearance.theme'"""

    def set(self, key: str, value: Any) -> None:
        """Set and save setting, emit 'changed' signal"""

    def connect(self, signal: str, callback) -> None:
        """Connect to 'changed::appearance.theme' signal"""
```

### GtkSourceView Schemes
```python
manager = GtkSource.StyleSchemeManager.get_default()
scheme_ids = manager.get_scheme_ids()  # ['Adwaita', 'Adwaita-dark', 'classic', ...]
scheme = manager.get_scheme(scheme_id)
buffer.set_style_scheme(scheme)
```

### Adw Color Schemes
```python
style_manager = Adw.StyleManager.get_default()
# Options:
style_manager.set_color_scheme(Adw.ColorScheme.DEFAULT)      # system
style_manager.set_color_scheme(Adw.ColorScheme.FORCE_DARK)   # dark
style_manager.set_color_scheme(Adw.ColorScheme.FORCE_LIGHT)  # light
```

### VTE Font
```python
terminal.set_font(Pango.FontDescription.from_string("Monospace 12"))
```

### Preferences Dialog Structure
```
┌─ Preferences ─────────────────────────────────┐
│                                               │
│ ┌─ Appearance ─────────────────────────────┐  │
│ │ Theme              [System ▼]            │  │
│ │ Syntax Scheme      [Adwaita-dark ▼]      │  │
│ └──────────────────────────────────────────┘  │
│                                               │
│ ┌─ Editor ─────────────────────────────────┐  │
│ │ Font               [Monospace        ]   │  │
│ │ Font Size          [12    ]              │  │
│ │ Line Height        [1.4   ]              │  │
│ │ Tab Size           [4     ]              │  │
│ │ Insert Spaces      [====○]               │  │
│ └──────────────────────────────────────────┘  │
│                                               │
│ ┌─ Files ──────────────────────────────────┐  │
│ │ Show Hidden Files  [○====]               │  │
│ └──────────────────────────────────────────┘  │
│                                               │
└───────────────────────────────────────────────┘
```

## Progress

- Started: 2025-12-12
- Status: Completed

### Implementation
- `src/services/settings_service.py` - Settings singleton with JSON storage
- `src/widgets/preferences_dialog.py` - Adw.PreferencesDialog with 3 pages
- `src/project_window.py` - Theme, window state, preferences button
- `src/widgets/file_editor.py` - Font, syntax scheme, tab settings
- `src/widgets/terminal_view.py` - Font from settings
- `src/widgets/file_tree.py` - show_hidden setting
