#!/bin/sh
# Code Companion launcher (installed as /usr/bin/code-companion by the system package).
#
# Unlike the dev launcher (bin/code-companion, which uses `uv run`), this runs the
# system python3 so it picks up the distro-built gi/pygit2 (declared as package
# dependencies), and prepends the app dir + the vendored PyPI-only deps (mcp, mistune,
# pathspec) that are shipped inside the package.
APP=/usr/lib/code-companion
export PYTHONPATH="$APP:$APP/vendor${PYTHONPATH:+:$PYTHONPATH}"
exec python3 "$APP/main.py" "$@"
