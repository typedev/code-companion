# Packaging — `.rpm` / `.deb`

Native packages for Code Companion. Goal: a **one-command, dependency-resolving,
DB-registered** install, replacing the compile-from-source `install.sh` for end users.

```bash
sudo dnf install ./code-companion-*.rpm      # Fedora
sudo apt  install ./code-companion_*.deb      # Ubuntu/Debian
```

## How it works

The app splits its Python deps in two:

- **GObject-bound** (`gi`/PyGObject, `pygit2`) — ABI-tied to the distro's C libraries, so they
  are **declared as package dependencies** (`python3-gobject`/`python3-gi`, `python3-pygit2`)
  and taken from the distro, never bundled.
- **Pure-Python / manylinux-wheel PyPI deps** (`mcp` + closure, `mistune`, `pathspec`) — not in
  distro repos, so they are **vendored** into `/usr/lib/code-companion/vendor/`.

The vendored closure includes a compiled wheel (`pydantic_core`) whose ABI is **Python-version
specific**. Fedora and Ubuntu ship different Python versions, so the vendor tree is built
**separately against each target distro's own Python** (`fedora:latest` for the `.rpm`,
`ubuntu:24.04` for the `.deb`); both trees are then wrapped by `fpm` in one Fedora-based builder.
`podman` runs all tools in throwaway containers (host stays clean) and tests installs in fresh
ones. No Rust toolchain is needed — every target Python has a prebuilt `pydantic_core` wheel.

### Installed layout

```
/usr/lib/code-companion/{main.py, src/, vendor/}
/usr/bin/code-companion                         # launcher -> system python3 + vendored path
/usr/share/applications/dev.typedev.CodeCompanion.desktop
/usr/share/icons/hicolor/scalable/apps/dev.typedev.CodeCompanion.svg
```

## Build & test

```bash
packaging/build.sh          # -> dist/code-companion-<v>.x86_64.rpm  +  dist/code-companion_<v>_amd64.deb
packaging/test-install.sh   # fresh fedora:latest + ubuntu:24.04: install, smoke-test, remove
packaging/test-install.sh fedora   # (or ubuntu) to test just one
```

The test asserts, in a clean container: dependency resolution from official repos only, files
landed, `code-companion --help` runs, the full GObject + vendored import graph loads
(`import_smoke.py`), and clean removal.

## Files

| File | Role |
|---|---|
| `build.sh` | Host entry: build the builder image, vendor per target, run the packaging step. |
| `Containerfile.builder` | Fedora + `fpm` (+ ruby, rpm-build, binutils). |
| `vendor.sh` | Runs in a target image; vendors PyPI deps for that Python ABI. |
| `_build-in-container.sh` | Stage tree + the matched vendor, run `fpm` for rpm + deb. |
| `launcher.sh` | Installed as `/usr/bin/code-companion`. |
| `after-install.sh` | Postinst: refresh desktop + icon caches. |
| `test-install.sh` | Clean-install test in fedora + ubuntu containers. |
| `import_smoke.py` | Import-graph smoke test run inside the test containers. |

## Version

Package version is read from `pyproject.toml` (currently `0.8.0`). Bump it there before cutting a
release (the roadmap sits at `v1.0`).

## Not covered by these packages (documented for users)

`claude` CLI (npm) and `uv` are **not** in distro repos and cannot be package dependencies. They
are required regardless of install method (the app shells out to them for the AI session and
per-project venvs/linters). Install separately.

## TODO (deferred)

- [ ] **COPR** (Fedora) + **PPA/OBS** (Ubuntu) hosting → `install`-by-name + auto-updates via the
      normal `dnf/apt upgrade` path. (Explicitly deferred; current flow builds local package files.)
- [ ] Native `.spec` / `debian/` packaging (for official distro submission) instead of `fpm`.
- [ ] AppImage build.
- [ ] Version bump to `1.0.0`; GPG signing of the packages.
- [ ] `arm64`/`aarch64` builds (vendored `pydantic-core` is arch-specific).

> Maintenance note: because a compiled wheel is vendored, packages must be **rebuilt when a
> target distro bumps its Python** (e.g. a new Fedora/Ubuntu release). `build.sh` handles this
> automatically by vendoring against `fedora:latest` / `ubuntu:24.04` at build time — bump those
> base tags to target other releases.
