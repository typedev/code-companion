"""Markdown preview widget using WebKit."""

from pathlib import Path

import gi

gi.require_version("WebKit", "6.0")

from gi.repository import Gtk, WebKit, GLib
import mistune

from ..services import SettingsService


# Vendored highlight.js assets (see src/resources/highlight/). Bundled locally so
# code highlighting works offline and on a clean machine without a CDN request.
_HLJS_DIR = Path(__file__).resolve().parent.parent / "resources" / "highlight"

# Markers replaced (via str.replace, not str.format) after the template is
# formatted — the minified JS is full of "{}" and would break str.format().
_HLJS_JS_MARKER = "/*__HLJS_JS__*/"
_HLJS_CSS_MARKER = "/*__HLJS_CSS__*/"

_hljs_cache: dict[str, str] = {}


def _read_hljs_asset(name: str) -> str:
    """Read a vendored highlight.js asset, cached; empty string if missing."""
    if name not in _hljs_cache:
        try:
            _hljs_cache[name] = (_HLJS_DIR / name).read_text(encoding="utf-8")
        except OSError:
            _hljs_cache[name] = ""
    return _hljs_cache[name]


# HTML template with highlight.js for code syntax highlighting
HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <style>/*__HLJS_CSS__*/</style>
    <script>/*__HLJS_JS__*/</script>
    <style>
        :root {{
            color-scheme: {color_scheme};
        }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
            font-size: {body_font_size}px;
            line-height: {line_height};
            padding: 24px;
            max-width: 900px;
            margin: 0 auto;
            background: {bg_color};
            color: {text_color};
        }}
        h1, h2, h3, h4, h5, h6 {{
            margin-top: 24px;
            margin-bottom: 16px;
            font-weight: 600;
            line-height: 1.25;
            border-bottom: 1px solid {border_color};
            padding-bottom: 0.3em;
        }}
        h1 {{ font-size: 2em; }}
        h2 {{ font-size: 1.5em; }}
        h3 {{ font-size: 1.25em; border-bottom: none; }}
        h4, h5, h6 {{ border-bottom: none; }}
        code {{
            font-family: "{font_family}", ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace;
            font-size: {code_font_size}px;
            background: {code_bg};
            padding: 0.2em 0.4em;
            border-radius: 4px;
        }}
        pre {{
            background: {code_bg};
            padding: 16px;
            border-radius: 8px;
            overflow-x: auto;
        }}
        pre code {{
            background: none;
            padding: 0;
            font-size: {code_font_size}px;
            line-height: {line_height};
        }}
        blockquote {{
            border-left: 4px solid {accent_color};
            margin: 0;
            padding-left: 16px;
            color: {dim_color};
        }}
        a {{
            color: {accent_color};
            text-decoration: none;
        }}
        a:hover {{
            text-decoration: underline;
        }}
        table {{
            border-collapse: collapse;
            width: 100%;
            margin: 16px 0;
        }}
        th, td {{
            border: 1px solid {border_color};
            padding: 8px 12px;
            text-align: left;
        }}
        th {{
            background: {code_bg};
        }}
        img {{
            max-width: 100%;
            height: auto;
        }}
        hr {{
            border: none;
            border-top: 1px solid {border_color};
            margin: 24px 0;
        }}
        ul, ol {{
            padding-left: 24px;
        }}
        li {{
            margin: 4px 0;
        }}
        .hljs {{
            background: transparent !important;
        }}
        /* Issue body + comment cards (used by the Issues detail view) */
        .issue-comments-title {{
            margin-top: 28px;
            font-size: 1.1em;
            color: {dim_color};
        }}
        .issue-comment {{
            border: 1px solid {border_color};
            border-radius: 8px;
            margin: 16px 0;
            overflow: hidden;
        }}
        .comment-head {{
            background: {code_bg};
            padding: 8px 14px;
            font-weight: 600;
            border-bottom: 1px solid {border_color};
        }}
        .comment-head .comment-date {{
            font-weight: normal;
            color: {dim_color};
        }}
        .comment-body {{
            padding: 4px 14px;
        }}
        .comment-body > :first-child {{ margin-top: 8px; }}
    </style>
</head>
<body>
{content}
<script>hljs.highlightAll();</script>
</body>
</html>
"""


class MarkdownPreview(Gtk.Box):
    """A widget for previewing markdown files using WebKit."""

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.set_vexpand(True)
        self.set_hexpand(True)

        self.settings = SettingsService.get_instance()
        self._build_ui()

    def _build_ui(self):
        """Build the preview UI."""
        # WebKit WebView
        self.webview = WebKit.WebView()
        self.webview.set_vexpand(True)
        self.webview.set_hexpand(True)

        # Configure settings for proper rendering
        settings = self.webview.get_settings()
        settings.set_enable_javascript(True)
        settings.set_javascript_can_access_clipboard(False)
        settings.set_enable_developer_extras(False)

        # Disable navigation to external links
        self.webview.connect("decide-policy", self._on_decide_policy)

        self.append(self.webview)

    def _on_decide_policy(self, webview, decision, decision_type):
        """Handle navigation policy - block user-initiated external links."""
        if decision_type == WebKit.PolicyDecisionType.NAVIGATION_ACTION:
            navigation_action = decision.get_navigation_action()

            # Allow all programmatic navigation (initial load, etc.)
            nav_type = navigation_action.get_navigation_type()
            if nav_type != WebKit.NavigationType.LINK_CLICKED:
                decision.use()
                return True

            # For link clicks, block external navigation
            request = navigation_action.get_request()
            uri = request.get_uri() or ""

            # Allow local links only
            if uri.startswith("file://") or uri.startswith("#"):
                decision.use()
            else:
                decision.ignore()
            return True

        # Allow resource loading (highlight.js CSS/JS are inlined locally)
        decision.use()
        return True

    @staticmethod
    def render_markdown(markdown_text: str) -> str:
        """Convert markdown to an HTML fragment (no surrounding document)."""
        return mistune.html(markdown_text or "")

    def update_preview(self, markdown_text: str, base_path: str = None):
        """Update the preview with new markdown content.

        Args:
            markdown_text: The markdown source text
            base_path: Optional base path for resolving relative URLs
        """
        self.update_html(mistune.html(markdown_text), base_path)

    def update_html(self, html_content: str, base_path: str = None):
        """Render a pre-built HTML fragment inside the themed document.

        Args:
            html_content: HTML body fragment (already converted from markdown)
            base_path: Optional base path for resolving relative URLs
        """
        # Get theme settings
        theme = self.settings.get("appearance.theme", "system")
        font_family = self.settings.get("editor.font_family", "Monospace")
        font_size = self.settings.get("editor.font_size", 12)
        line_height = self.settings.get("editor.line_height", 1.4)

        # Editor font size is in points; convert to px (~4/3) so markdown text
        # visually matches the editor and tracks the user's size preference.
        body_font_size = max(11, round(float(font_size) * 4 / 3))
        code_font_size = body_font_size

        # Determine colors based on theme
        if theme == "dark":
            is_dark = True
        elif theme == "light":
            is_dark = False
        else:
            # System theme - check Adw style manager
            try:
                from gi.repository import Adw
                style_manager = Adw.StyleManager.get_default()
                is_dark = style_manager.get_dark()
            except Exception:
                is_dark = False

        if is_dark:
            bg_color = "#1e1e1e"
            text_color = "#d4d4d4"
            code_bg = "#2d2d2d"
            border_color = "#404040"
            dim_color = "#808080"
            accent_color = "#4fc3f7"
            highlight_style = "github-dark"
            color_scheme = "dark"
        else:
            bg_color = "#ffffff"
            text_color = "#24292e"
            code_bg = "#f6f8fa"
            border_color = "#e1e4e8"
            dim_color = "#6a737d"
            accent_color = "#0366d6"
            highlight_style = "github"
            color_scheme = "light"

        # Build full HTML
        full_html = HTML_TEMPLATE.format(
            content=html_content,
            bg_color=bg_color,
            text_color=text_color,
            code_bg=code_bg,
            border_color=border_color,
            dim_color=dim_color,
            accent_color=accent_color,
            highlight_style=highlight_style,
            color_scheme=color_scheme,
            font_family=font_family,
            body_font_size=body_font_size,
            code_font_size=code_font_size,
            line_height=line_height,
        )

        # Inline the vendored highlight.js CSS/JS after formatting (the minified
        # JS contains "{}" that would break str.format()).
        hljs_css = _read_hljs_asset(f"{highlight_style}.min.css")
        hljs_js = _read_hljs_asset("highlight.min.js")
        full_html = full_html.replace(_HLJS_CSS_MARKER, hljs_css)
        full_html = full_html.replace(_HLJS_JS_MARKER, hljs_js)

        # Load HTML into WebView
        self.webview.load_html(full_html, base_path or "file:///")
