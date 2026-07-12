"""Post-install smoke test: prove the packaged app can import everything it needs.

Run with the packaged paths on PYTHONPATH, e.g.:
    PYTHONPATH=/usr/lib/code-companion:/usr/lib/code-companion/vendor python3 import_smoke.py

Confirms that the distro-provided GObject bindings (gi/pygit2) and every GI namespace the
app uses have their typelibs installed, and that the vendored PyPI-only deps load alongside
them. Exits non-zero (via the import error) if any dependency is missing.
"""

import gi

for ns, ver in [
    ("Gtk", "4.0"),
    ("Adw", "1"),
    ("GtkSource", "5"),
    ("Vte", "3.91"),
    ("WebKit", "6.0"),
    ("Spelling", "1"),
    ("Secret", "1"),
    ("GdkPixbuf", "2.0"),
]:
    gi.require_version(ns, ver)

from gi.repository import (  # noqa: E402,F401
    Adw,
    GdkPixbuf,
    GtkSource,
    Gtk,
    Secret,
    Spelling,
    Vte,
    WebKit,
)

import mcp  # noqa: E402,F401
import mistune  # noqa: E402,F401
import pathspec  # noqa: E402,F401
import pygit2  # noqa: E402,F401

print("import-graph-ok")
