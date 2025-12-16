"""Markdown preview widget using WebKit."""

import gi

gi.require_version("WebKit", "6.0")

from gi.repository import Gtk, WebKit, GLib
import mistune

from ..services import SettingsService


# HTML template with highlight.js for code syntax highlighting
HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/{highlight_style}.min.css">
    <script src="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/highlight.min.js"></script>
    <style>
        :root {{
            color-scheme: {color_scheme};
        }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
            font-size: 15px;
            line-height: 1.6;
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
            font-size: {font_size}px;
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
            font-size: {font_size}px;
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

        # Allow resource loading (CSS, JS from CDN)
        decision.use()
        return True

    def update_preview(self, markdown_text: str, base_path: str = None):
        """Update the preview with new markdown content.

        Args:
            markdown_text: The markdown source text
            base_path: Optional base path for resolving relative URLs
        """
        # Convert markdown to HTML
        html_content = mistune.html(markdown_text)

        # Get theme settings
        theme = self.settings.get("appearance.theme", "system")
        font_family = self.settings.get("editor.font_family", "Monospace")
        font_size = self.settings.get("editor.font_size", 12)
        line_height = self.settings.get("editor.line_height", 1.4)

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
            font_size=font_size,
            line_height=line_height,
        )

        # Load HTML into WebView
        self.webview.load_html(full_html, base_path or "file:///")
