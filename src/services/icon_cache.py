"""Icon cache service for fast file/folder icon lookup."""

from pathlib import Path
from typing import ClassVar

import gi
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib


class IconCache:
    """Singleton cache for file and folder icons.

    Pre-loads SVG icons at startup for O(1) lookup performance.
    Uses Material Design icons from vscode-material-icon-theme.
    """

    _instance: ClassVar["IconCache | None"] = None
    _initialized: bool = False

    # Extension to icon name mapping
    EXTENSION_MAP: ClassVar[dict[str, str]] = {
        # Python
        ".py": "python",
        ".pyw": "python",
        ".pyi": "python",
        ".pyx": "python",
        ".pxd": "python",

        # JavaScript/TypeScript
        ".js": "javascript",
        ".mjs": "javascript",
        ".cjs": "javascript",
        ".jsx": "react",
        ".ts": "typescript",
        ".mts": "typescript",
        ".cts": "typescript",
        ".tsx": "react_ts",
        ".d.ts": "typescript-def",

        # Web
        ".html": "html",
        ".htm": "html",
        ".css": "css",
        ".scss": "sass",
        ".sass": "sass",
        ".less": "less",
        ".vue": "vue",
        ".svelte": "svelte",

        # Data formats
        ".json": "json",
        ".jsonc": "json",
        ".json5": "json",
        ".jsonl": "json",
        ".yaml": "yaml",
        ".yml": "yaml",
        ".toml": "toml",
        ".xml": "xml",
        ".csv": "database",
        ".tsv": "database",

        # Markup
        ".md": "markdown",
        ".markdown": "markdown",
        ".mdx": "markdown",
        ".rst": "markdown",
        ".txt": "text",
        ".text": "text",

        # Shell/Scripts
        ".sh": "console",
        ".bash": "console",
        ".zsh": "console",
        ".fish": "console",
        ".ps1": "console",
        ".bat": "console",
        ".cmd": "console",

        # Systems programming
        ".c": "c",
        ".h": "h",
        ".cpp": "cpp",
        ".cxx": "cpp",
        ".cc": "cpp",
        ".hpp": "h",
        ".hxx": "h",
        ".hh": "h",
        ".rs": "rust",
        ".go": "go",
        ".zig": "zig",
        ".nim": "nim",
        ".asm": "assembly",
        ".s": "assembly",
        ".wasm": "wasm",

        # JVM
        ".java": "java",
        ".kt": "kotlin",
        ".kts": "kotlin",
        ".scala": "scala",
        ".groovy": "gradle",
        ".gradle": "gradle",
        ".clj": "clojure",
        ".cljs": "clojure",
        ".cljc": "clojure",

        # .NET
        ".cs": "csharp",
        ".fs": "fsharp",
        ".vb": "visualbasic",

        # Other languages
        ".rb": "ruby",
        ".php": "php",
        ".pl": "perl",
        ".pm": "perl",
        ".lua": "lua",
        ".swift": "swift",
        ".dart": "dart",
        ".r": "r",
        ".R": "r",
        ".jl": "julia",
        ".ex": "elixir",
        ".exs": "elixir",
        ".erl": "erlang",
        ".hrl": "erlang",
        ".hs": "haskell",
        ".lhs": "haskell",
        ".ml": "ocaml",
        ".mli": "ocaml",
        ".gleam": "gleam",

        # Config
        ".ini": "settings",
        ".cfg": "settings",
        ".conf": "settings",
        ".config": "settings",
        ".env": "env",
        ".envrc": "env",

        # Database
        ".sql": "sql",
        ".sqlite": "database",
        ".db": "database",
        ".graphql": "graphql",
        ".gql": "graphql",

        # Build/Package
        ".lock": "lock",

        # Docker
        ".dockerfile": "docker",

        # Documents
        ".pdf": "pdf",
        ".doc": "word",
        ".docx": "word",
        ".xls": "excel",
        ".xlsx": "excel",

        # Media
        ".png": "image",
        ".jpg": "image",
        ".jpeg": "image",
        ".gif": "image",
        ".svg": "image",
        ".ico": "image",
        ".webp": "image",
        ".bmp": "image",
        ".tiff": "image",
        ".mp4": "video",
        ".webm": "video",
        ".avi": "video",
        ".mov": "video",
        ".mkv": "video",
        ".mp3": "audio",
        ".wav": "audio",
        ".ogg": "audio",
        ".flac": "audio",
        ".ttf": "font",
        ".otf": "font",
        ".woff": "font",
        ".woff2": "font",
        ".eot": "font",

        # Archives
        ".zip": "zip",
        ".tar": "zip",
        ".gz": "zip",
        ".bz2": "zip",
        ".xz": "zip",
        ".7z": "zip",
        ".rar": "zip",

        # Certificates
        ".pem": "certificate",
        ".crt": "certificate",
        ".cer": "certificate",
        ".key": "certificate",

        # Logs
        ".log": "log",

        # Shaders
        ".glsl": "shader",
        ".hlsl": "shader",
        ".vert": "shader",
        ".frag": "shader",
        ".shader": "shader",
    }

    # Exact filename to icon name mapping
    FILENAME_MAP: ClassVar[dict[str, str]] = {
        # Package managers
        "package.json": "nodejs",
        "package-lock.json": "lock",
        "yarn.lock": "yarn",
        "pnpm-lock.yaml": "pnpm",
        "bun.lockb": "bun",
        "deno.json": "deno",
        "deno.jsonc": "deno",

        # Python
        "pyproject.toml": "python",
        "setup.py": "python",
        "setup.cfg": "python",
        "requirements.txt": "python",
        "Pipfile": "python",
        "Pipfile.lock": "lock",
        "poetry.lock": "lock",
        "uv.lock": "lock",

        # Rust
        "Cargo.toml": "cargo",
        "Cargo.lock": "lock",

        # Build
        "Makefile": "makefile",
        "makefile": "makefile",
        "GNUmakefile": "makefile",
        "CMakeLists.txt": "makefile",
        "meson.build": "meson",
        "meson_options.txt": "meson",
        "build.gradle": "gradle",
        "build.gradle.kts": "gradle",
        "pom.xml": "maven",

        # Docker
        "Dockerfile": "docker",
        "dockerfile": "docker",
        "docker-compose.yml": "docker",
        "docker-compose.yaml": "docker",
        "compose.yml": "docker",
        "compose.yaml": "docker",
        ".dockerignore": "docker",

        # Git
        ".gitignore": "git",
        ".gitattributes": "git",
        ".gitmodules": "git",
        ".gitkeep": "git",

        # Editor configs
        ".editorconfig": "editorconfig",
        ".prettierrc": "prettier",
        ".prettierrc.json": "prettier",
        ".prettierrc.yaml": "prettier",
        ".prettierrc.yml": "prettier",
        ".prettierrc.js": "prettier",
        ".prettierrc.cjs": "prettier",
        "prettier.config.js": "prettier",
        "prettier.config.cjs": "prettier",
        ".eslintrc": "eslint",
        ".eslintrc.json": "eslint",
        ".eslintrc.js": "eslint",
        ".eslintrc.cjs": "eslint",
        ".eslintrc.yaml": "eslint",
        ".eslintrc.yml": "eslint",
        "eslint.config.js": "eslint",
        "eslint.config.mjs": "eslint",
        "eslint.config.cjs": "eslint",

        # Bundlers
        "webpack.config.js": "webpack",
        "webpack.config.ts": "webpack",
        "vite.config.js": "vite",
        "vite.config.ts": "vite",
        "vite.config.mjs": "vite",
        "vite.config.mts": "vite",

        # Frameworks
        "angular.json": "angular",
        "svelte.config.js": "svelte",
        "nuxt.config.js": "vue",
        "nuxt.config.ts": "vue",
        "next.config.js": "react",
        "next.config.mjs": "react",
        "next.config.ts": "react",
        "flutter.yaml": "flutter",
        "pubspec.yaml": "dart",
        "pubspec.lock": "lock",

        # Config files
        "tsconfig.json": "typescript",
        "jsconfig.json": "javascript",
        ".babelrc": "javascript",
        "babel.config.js": "javascript",
        "babel.config.json": "javascript",

        # Claude
        "CLAUDE.md": "claude",
        "claude.md": "claude",
        ".claude": "claude",

        # Docs
        "README": "readme",
        "README.md": "readme",
        "README.txt": "readme",
        "readme.md": "readme",
        "CHANGELOG": "changelog",
        "CHANGELOG.md": "changelog",
        "HISTORY.md": "changelog",
        "LICENSE": "license",
        "LICENSE.md": "license",
        "LICENSE.txt": "license",
        "COPYING": "license",
        "TODO": "todo",
        "TODO.md": "todo",

        # Environment
        ".env": "env",
        ".env.local": "env",
        ".env.development": "env",
        ".env.production": "env",
        ".env.test": "env",
        ".env.example": "env",
        ".env.sample": "env",

        # CI/CD
        ".travis.yml": "settings",
        ".gitlab-ci.yml": "settings",
        "Jenkinsfile": "settings",

        # Web
        "robots.txt": "robots",
        "sitemap.xml": "xml",
        "favicon.ico": "image",

        # Nginx
        "nginx.conf": "nginx",
    }

    # Folder name to icon name mapping (without "folder-" prefix)
    FOLDER_MAP: ClassVar[dict[str, str]] = {
        # Source
        "src": "src",
        "srcs": "src",
        "source": "src",
        "sources": "src",
        "code": "src",
        "lib": "lib",
        "libs": "lib",
        "library": "lib",
        "libraries": "lib",
        "vendor": "lib",
        "packages": "packages",
        "pkg": "packages",

        # Build/Output
        "dist": "dist",
        "out": "dist",
        "output": "dist",
        "build": "dist",
        "builds": "dist",
        "bin": "dist",
        "target": "target",
        "release": "dist",
        "debug": "dist",

        # Tests
        "test": "test",
        "tests": "test",
        "spec": "test",
        "specs": "test",
        "__tests__": "test",
        "__test__": "test",
        "testing": "test",
        "coverage": "coverage",
        "benchmark": "benchmark",
        "benchmarks": "benchmark",

        # Documentation
        "doc": "docs",
        "docs": "docs",
        "documentation": "docs",
        "wiki": "docs",

        # Config
        "config": "config",
        "configs": "config",
        "conf": "config",
        "configuration": "config",
        "settings": "config",
        ".config": "config",

        # Resources
        "resource": "resource",
        "resources": "resource",
        "res": "resource",
        "asset": "asset",
        "assets": "asset",
        "static": "asset",
        "public": "public",
        "www": "public",
        "wwwroot": "public",
        "private": "private",

        # Media
        "images": "images",
        "image": "images",
        "img": "images",
        "imgs": "images",
        "icons": "images",
        "icon": "images",
        "pictures": "images",
        "photos": "images",
        "textures": "images",

        # Code structure
        "components": "components",
        "component": "components",
        "widgets": "components",
        "ui": "components",
        "views": "views",
        "view": "views",
        "pages": "views",
        "screens": "views",
        "templates": "templates",
        "template": "templates",
        "layouts": "templates",
        "models": "models",
        "model": "models",
        "entities": "models",
        "schemas": "models",
        "controllers": "controllers",
        "controller": "controllers",
        "handlers": "controllers",
        "services": "services",
        "service": "services",
        "providers": "services",
        "middleware": "middleware",
        "middlewares": "middleware",
        "utils": "utils",
        "util": "utils",
        "utilities": "utils",
        "helpers": "utils",
        "helper": "utils",
        "tools": "utils",
        "api": "api",
        "apis": "api",
        "routes": "routes",
        "router": "routes",
        "routing": "routes",
        "hooks": "hook",
        "hook": "hook",
        "plugins": "plugin",
        "plugin": "plugin",
        "extensions": "plugin",
        "addons": "plugin",

        # Languages/Frameworks
        "node_modules": "node",
        "python": "python",
        "__pycache__": "python",
        ".venv": "python",
        "venv": "python",
        "env": "python",
        "js": "javascript",
        "javascript": "javascript",
        "ts": "typescript",
        "typescript": "typescript",
        "sass": "sass",
        "scss": "sass",
        "css": "css",
        "styles": "css",
        "stylesheets": "css",

        # Scripts
        "scripts": "scripts",
        "script": "scripts",
        "bin": "scripts",

        # Docker
        "docker": "docker",
        ".docker": "docker",

        # Database
        "db": "database",
        "database": "database",
        "databases": "database",
        "data": "database",
        "migrations": "database",
        "seeds": "database",
        "fixtures": "database",

        # Logs
        "logs": "log",
        "log": "log",

        # Temp
        "tmp": "temp",
        "temp": "temp",
        "cache": "temp",
        ".cache": "temp",

        # Backup
        "backup": "backup",
        "backups": "backup",

        # Git
        ".git": "git",
        ".github": "github",
        ".gitlab": "git",

        # Claude
        ".claude": "claude",

        # IDE
        ".vscode": "vscode",
        ".idea": "idea",

        # i18n
        "i18n": "i18n",
        "locales": "i18n",
        "locale": "i18n",
        "lang": "i18n",
        "languages": "i18n",
        "translations": "i18n",

        # Tasks
        "tasks": "tasks",
        "jobs": "tasks",

        # Examples
        "examples": "examples",
        "example": "examples",
        "samples": "examples",
        "sample": "examples",
        "demo": "examples",
        "demos": "examples",
    }

    def __new__(cls) -> "IconCache":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True

        self._cache: dict[str, Gdk.Texture] = {}
        self._icons_dir = Path(__file__).parent.parent / "resources" / "icons"
        self._load_icons()

    def _load_icons(self) -> None:
        """Pre-load all SVG icons into memory."""
        if not self._icons_dir.exists():
            return

        for svg_file in self._icons_dir.glob("*.svg"):
            name = svg_file.stem
            try:
                gfile = Gio.File.new_for_path(str(svg_file))
                texture = Gdk.Texture.new_from_file(gfile)
                self._cache[name] = texture
            except GLib.Error:
                pass

    def get_file_icon(self, path: Path) -> Gdk.Texture | None:
        """Get icon texture for a file path.

        Args:
            path: Path to the file

        Returns:
            Gdk.Texture for the icon, or None if not found
        """
        # Check exact filename first (highest priority)
        if path.name in self.FILENAME_MAP:
            icon_name = self.FILENAME_MAP[path.name]
            if icon_name in self._cache:
                return self._cache[icon_name]

        # Check for test files
        name_lower = path.name.lower()
        if ".test." in name_lower or ".spec." in name_lower or "_test." in name_lower:
            suffix = path.suffix.lower()
            test_icon = None
            if suffix in (".ts", ".mts", ".cts"):
                test_icon = "test-ts"
            elif suffix in (".tsx",):
                test_icon = "test-tsx"
            elif suffix in (".js", ".mjs", ".cjs"):
                test_icon = "test-js"
            elif suffix in (".jsx",):
                test_icon = "test-jsx"
            if test_icon and test_icon in self._cache:
                return self._cache[test_icon]

        # Check compound extensions (e.g., .d.ts)
        if path.name.endswith(".d.ts"):
            return self._cache.get("typescript-def")

        # Check extension
        suffix = path.suffix.lower()
        if suffix in self.EXTENSION_MAP:
            icon_name = self.EXTENSION_MAP[suffix]
            if icon_name in self._cache:
                return self._cache[icon_name]

        # Default file icon
        return self._cache.get("file")

    def get_folder_icon(self, path: Path, is_open: bool = False) -> Gdk.Texture | None:
        """Get icon texture for a folder path.

        Args:
            path: Path to the folder
            is_open: Whether the folder is expanded

        Returns:
            Gdk.Texture for the icon, or None if not found
        """
        folder_name = path.name.lower()

        # Look up folder-specific icon
        if folder_name in self.FOLDER_MAP:
            icon_base = f"folder-{self.FOLDER_MAP[folder_name]}"
        else:
            icon_base = "folder"

        # Add -open suffix for expanded folders
        icon_name = f"{icon_base}-open" if is_open else icon_base

        # Return specific icon or fall back to default folder
        if icon_name in self._cache:
            return self._cache[icon_name]

        # Try without -open suffix
        if is_open and icon_base in self._cache:
            return self._cache[icon_base]

        # Fall back to default folder
        fallback = "folder-open" if is_open else "folder"
        return self._cache.get(fallback)

    def has_icon(self, name: str) -> bool:
        """Check if an icon is in the cache."""
        return name in self._cache

    @property
    def icon_count(self) -> int:
        """Return number of cached icons."""
        return len(self._cache)

    def get_file_gicon(self, path: Path) -> Gio.Icon | None:
        """Get Gio.Icon for a file (for use in tab icons, etc.)

        Args:
            path: Path to the file

        Returns:
            Gio.FileIcon pointing to the SVG, or None if not found
        """
        # Check exact filename first
        if path.name in self.FILENAME_MAP:
            icon_name = self.FILENAME_MAP[path.name]
            icon_path = self._icons_dir / f"{icon_name}.svg"
            if icon_path.exists():
                return Gio.FileIcon.new(Gio.File.new_for_path(str(icon_path)))

        # Check for test files
        name_lower = path.name.lower()
        if ".test." in name_lower or ".spec." in name_lower or "_test." in name_lower:
            suffix = path.suffix.lower()
            test_icon = None
            if suffix in (".ts", ".mts", ".cts"):
                test_icon = "test-ts"
            elif suffix in (".tsx",):
                test_icon = "test-tsx"
            elif suffix in (".js", ".mjs", ".cjs"):
                test_icon = "test-js"
            elif suffix in (".jsx",):
                test_icon = "test-jsx"
            if test_icon:
                icon_path = self._icons_dir / f"{test_icon}.svg"
                if icon_path.exists():
                    return Gio.FileIcon.new(Gio.File.new_for_path(str(icon_path)))

        # Check compound extensions
        if path.name.endswith(".d.ts"):
            icon_path = self._icons_dir / "typescript-def.svg"
            if icon_path.exists():
                return Gio.FileIcon.new(Gio.File.new_for_path(str(icon_path)))

        # Check extension
        suffix = path.suffix.lower()
        if suffix in self.EXTENSION_MAP:
            icon_name = self.EXTENSION_MAP[suffix]
            icon_path = self._icons_dir / f"{icon_name}.svg"
            if icon_path.exists():
                return Gio.FileIcon.new(Gio.File.new_for_path(str(icon_path)))

        # Default file icon
        icon_path = self._icons_dir / "file.svg"
        if icon_path.exists():
            return Gio.FileIcon.new(Gio.File.new_for_path(str(icon_path)))

        return None

    def get_folder_gicon(self, path: Path, is_open: bool = False) -> Gio.Icon | None:
        """Get Gio.Icon for a folder (renders at correct size).

        Args:
            path: Path to the folder
            is_open: Whether the folder is expanded

        Returns:
            Gio.FileIcon pointing to the SVG, or None if not found
        """
        folder_name = path.name.lower()

        # Look up folder-specific icon
        if folder_name in self.FOLDER_MAP:
            icon_base = f"folder-{self.FOLDER_MAP[folder_name]}"
        else:
            icon_base = "folder"

        # Add -open suffix for expanded folders
        icon_name = f"{icon_base}-open" if is_open else icon_base

        # Return specific icon or fall back to default folder
        icon_path = self._icons_dir / f"{icon_name}.svg"
        if icon_path.exists():
            return Gio.FileIcon.new(Gio.File.new_for_path(str(icon_path)))

        # Try without -open suffix
        if is_open:
            icon_path = self._icons_dir / f"{icon_base}.svg"
            if icon_path.exists():
                return Gio.FileIcon.new(Gio.File.new_for_path(str(icon_path)))

        # Fall back to default folder
        fallback = "folder-open" if is_open else "folder"
        icon_path = self._icons_dir / f"{fallback}.svg"
        if icon_path.exists():
            return Gio.FileIcon.new(Gio.File.new_for_path(str(icon_path)))

        return None

    def get_gicon(self, path: Path, is_open: bool = False) -> Gio.Icon | None:
        """Get Gio.Icon for any path (file or folder).

        Args:
            path: Path to the file or folder
            is_open: Whether the folder is expanded (ignored for files)

        Returns:
            Gio.FileIcon pointing to the SVG, or None if not found
        """
        if path.is_dir():
            return self.get_folder_gicon(path, is_open)
        else:
            return self.get_file_gicon(path)

    def get_provider_texture(self, icon_name: str) -> Gdk.Texture | None:
        """Get Gdk.Texture for AI provider icon.

        Args:
            icon_name: Icon name (e.g., "claude", "gemini", "codex")

        Returns:
            Gdk.Texture for the icon, or None if not found
        """
        return self._cache.get(icon_name)

    def get_provider_gicon(self, icon_name: str) -> Gio.Icon | None:
        """Get Gio.Icon for AI provider icon.

        Args:
            icon_name: Icon name (e.g., "claude", "gemini", "codex")

        Returns:
            Gio.FileIcon pointing to the SVG, or None if not found
        """
        if not icon_name:
            return None
        icon_path = self._icons_dir / f"{icon_name}.svg"
        if icon_path.exists():
            return Gio.FileIcon.new(Gio.File.new_for_path(str(icon_path)))
        return None

    # Backward compatible aliases
    def get_claude_texture(self) -> Gdk.Texture | None:
        """Get Gdk.Texture for Claude icon (deprecated, use get_provider_texture)."""
        return self.get_provider_texture("claude")

    def get_claude_gicon(self) -> Gio.Icon | None:
        """Get Gio.Icon for Claude icon (deprecated, use get_provider_gicon)."""
        return self.get_provider_gicon("claude")
