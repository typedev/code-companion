"""CP2 tests: the pure sync engine (3-way merge, export/import, slice)."""

import json
from pathlib import Path

import pytest

from src.services import sync_engine as E
from src.services.sync_engine import (
    LocalProjectView,
    decide_export,
    decide_import,
    export_project,
    import_project,
    sanitize_jsonl,
)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

def make_view(root: Path, fields=None, abs_path="/home/u/proj") -> LocalProjectView:
    project_dir = root / "claude" / "projects" / "enc"
    memory_dir = project_dir / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)
    return LocalProjectView(
        local_abs_path=abs_path,
        project_dir=project_dir,
        memory_dir=memory_dir,
        claude_json_path=root / "claude.json",
        claude_json_fields=fields or ["allowedTools"],
    )


def write(p: Path, text: str):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def write_bytes(p: Path, data: bytes):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)


# --------------------------------------------------------------------------- #
# decision tables
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("h_local,h_base,h_repo,expected", [
    (None, None, None, "skip"),          # nothing
    (None, "b", "b", "skip"),            # local deleted -> additive skip
    ("x", "y", "x", "skip"),             # local already equals repo -> nothing to do
    ("a", None, None, "write"),          # first contact, seed new
    ("a", None, "x", "skip"),            # first contact, repo diverges -> no clobber
    ("a", "a", "a", "skip"),             # not dirty
    ("a", "a", "z", "skip"),             # not dirty even if repo moved (adopt on import)
    ("b", "a", None, "write"),           # dirty, repo absent
    ("b", "a", "a", "write"),            # dirty, repo == base -> publish
    ("b", "a", "c", "conflict"),         # dirty, repo also moved
])
def test_decide_export(h_local, h_base, h_repo, expected):
    assert decide_export(h_local, h_base, h_repo) == expected


@pytest.mark.parametrize("h_local,h_base,h_repo,expected", [
    ("a", "a", None, "skip"),            # nothing in repo
    (None, None, "r", "materialize"),    # first contact fill
    (None, "r", "r", "skip"),            # intentional local delete, remote unchanged
    (None, "a", "r", "materialize"),     # local deleted but remote advanced -> resurrect
    ("r", "a", "r", "skip"),             # already equal
    ("a", "a", "r", "materialize"),      # remote-only change
    ("l", "a", "a", "keep_local"),       # local-only change
    ("l", "a", "r", "conflict"),         # both changed
])
def test_decide_import(h_local, h_base, h_repo, expected):
    assert decide_import(h_local, h_base, h_repo) == expected


# --------------------------------------------------------------------------- #
# sanitize_jsonl
# --------------------------------------------------------------------------- #

def test_sanitize_jsonl_complete_unchanged():
    data = b'{"a":1}\n{"b":2}\n'
    assert sanitize_jsonl(data) == data


def test_sanitize_jsonl_complete_no_trailing_newline():
    data = b'{"a":1}\n{"b":2}'
    assert sanitize_jsonl(data) == data


def test_sanitize_jsonl_drops_partial_last_line():
    data = b'{"a":1}\n{"b":2}\n{"c":'
    assert sanitize_jsonl(data) == b'{"a":1}\n{"b":2}\n'


# --------------------------------------------------------------------------- #
# export
# --------------------------------------------------------------------------- #

def test_export_first_contact_seeds_new_file(tmp_path):
    view = make_view(tmp_path)
    write(view.memory_dir / "MEMORY.md", "fact\n")
    repo = tmp_path / "repo" / "projects" / "id"
    r = export_project(view, repo, base={})
    assert "memory/MEMORY.md" in r.written
    assert (repo / "memory" / "MEMORY.md").read_text() == "fact\n"


def test_export_first_contact_does_not_clobber_divergent_repo(tmp_path):
    view = make_view(tmp_path)
    write(view.memory_dir / "MEMORY.md", "local\n")
    repo = tmp_path / "repo" / "projects" / "id"
    write(repo / "memory" / "MEMORY.md", "remote\n")
    r = export_project(view, repo, base={})  # no base -> first contact
    assert r.written == []
    assert (repo / "memory" / "MEMORY.md").read_text() == "remote\n"  # untouched


def test_export_dirty_only(tmp_path):
    view = make_view(tmp_path)
    write(view.memory_dir / "a.md", "old\n")
    repo = tmp_path / "repo" / "projects" / "id"
    write(repo / "memory" / "a.md", "old\n")
    base = {"memory/a.md": E.hash_bytes(b"old\n")}
    # not dirty -> skip
    assert export_project(view, repo, base).written == []
    # now edit locally -> dirty -> write
    write(view.memory_dir / "a.md", "new\n")
    r = export_project(view, repo, base)
    assert "memory/a.md" in r.written
    assert (repo / "memory" / "a.md").read_text() == "new\n"


def test_export_conflict_when_both_moved(tmp_path):
    view = make_view(tmp_path)
    write(view.memory_dir / "a.md", "localedit\n")
    repo = tmp_path / "repo" / "projects" / "id"
    write(repo / "memory" / "a.md", "remoteedit\n")
    base = {"memory/a.md": E.hash_bytes(b"orig\n")}
    r = export_project(view, repo, base)
    assert "memory/a.md" in r.conflicts
    assert (repo / "memory" / "a.md").read_text() == "remoteedit\n"  # not overwritten


def test_export_sessions_union(tmp_path):
    view = make_view(tmp_path)
    write(view.project_dir / "s1.jsonl", '{"x":1}\n')
    repo = tmp_path / "repo" / "projects" / "id"
    write(repo / "sessions" / "s2.jsonl", '{"y":1}\n')
    r = export_project(view, repo, base={})
    assert "sessions/s1.jsonl" in r.written
    assert (repo / "sessions" / "s1.jsonl").exists()
    assert (repo / "sessions" / "s2.jsonl").read_text() == '{"y":1}\n'  # kept


# --------------------------------------------------------------------------- #
# import
# --------------------------------------------------------------------------- #

def test_import_materializes_into_empty_local(tmp_path):
    view = make_view(tmp_path)
    repo = tmp_path / "repo" / "projects" / "id"
    write(repo / "memory" / "MEMORY.md", "remote\n")
    write(repo / "sessions" / "s.jsonl", '{"a":1}\n')
    snap = tmp_path / "snap"
    r = import_project(view, repo, base={}, snapshot_dir=snap)
    assert (view.memory_dir / "MEMORY.md").read_text() == "remote\n"
    assert (view.project_dir / "s.jsonl").read_text() == '{"a":1}\n'
    assert "memory/MEMORY.md" in r.materialized


def test_import_is_additive_keeps_local_only_files(tmp_path):
    view = make_view(tmp_path)
    write(view.memory_dir / "local_only.md", "keep\n")
    repo = tmp_path / "repo" / "projects" / "id"
    write(repo / "memory" / "remote.md", "remote\n")
    import_project(view, repo, base={}, snapshot_dir=tmp_path / "snap")
    assert (view.memory_dir / "local_only.md").read_text() == "keep\n"  # not deleted
    assert (view.memory_dir / "remote.md").read_text() == "remote\n"


def test_import_conflict_keeps_local_and_stashes_remote(tmp_path):
    view = make_view(tmp_path)
    write(view.memory_dir / "a.md", "localedit\n")
    repo = tmp_path / "repo" / "projects" / "id"
    write(repo / "memory" / "a.md", "remoteedit\n")
    base = {"memory/a.md": E.hash_bytes(b"orig\n")}
    snap = tmp_path / "snap"
    r = import_project(view, repo, base, snapshot_dir=snap)
    assert "memory/a.md" in r.conflicts
    assert (view.memory_dir / "a.md").read_text() == "localedit\n"  # local kept
    assert (snap / "memory/a.md.remote").read_text() == "remoteedit\n"
    # Both sides sit together so a manual merge has everything in one place.
    assert (snap / "memory/a.md").read_text() == "localedit\n"
    assert r.snapshot_path == str(snap)


# --------------------------------------------------------------------------- #
# snapshots: capture what we overwrite, and nothing else
# --------------------------------------------------------------------------- #

def test_import_snapshots_overwritten_file(tmp_path):
    view = make_view(tmp_path)
    write(view.memory_dir / "a.md", "before\n")
    repo = tmp_path / "repo" / "projects" / "id"
    write(repo / "memory" / "a.md", "after\n")
    base = {"memory/a.md": E.hash_bytes(b"before\n")}  # local agrees with base -> materialize
    snap = tmp_path / "snap"
    r = import_project(view, repo, base, snapshot_dir=snap)
    assert (view.memory_dir / "a.md").read_text() == "after\n"
    assert (snap / "memory/a.md").read_text() == "before\n"
    assert r.snapshot_path == str(snap)


def test_import_does_not_snapshot_untouched_local(tmp_path):
    # Inverse of the old contract: a local file the import never touches used to
    # be copied anyway, which is what grew the snapshot dir to 5.3GB.
    view = make_view(tmp_path)
    write(view.memory_dir / "a.md", "before\n")
    repo = tmp_path / "repo" / "projects" / "id"
    write(repo / "memory" / "b.md", "new\n")
    snap = tmp_path / "snap"
    r = import_project(view, repo, base={}, snapshot_dir=snap)
    assert (view.memory_dir / "b.md").read_text() == "new\n"  # import still happened
    assert not (snap / "memory/a.md").exists()
    assert r.snapshot_path is None


def test_import_materialize_into_empty_local_writes_no_snapshot(tmp_path):
    view = make_view(tmp_path)
    repo = tmp_path / "repo" / "projects" / "id"
    write(repo / "memory" / "x.md", "fresh\n")
    snap = tmp_path / "snap"
    r = import_project(view, repo, base={}, snapshot_dir=snap)
    assert "memory/x.md" in r.materialized
    assert not snap.exists()  # nothing was overwritten -> nothing to preserve
    assert r.snapshot_path is None


def test_import_rejects_invalid_json(tmp_path):
    view = make_view(tmp_path, fields=["allowedTools"])
    repo = tmp_path / "repo" / "projects" / "id"
    write(repo / "claude-config.json", "{ not json")
    snap = tmp_path / "snap"
    r = import_project(view, repo, base={}, snapshot_dir=snap)
    # Invalid slice must not be applied.
    assert not view.claude_json_path.exists() or "projects" not in json.loads(
        view.claude_json_path.read_text()
    )
    # Rejected payload -> no write -> no snapshot.
    assert not snap.exists()
    assert r.snapshot_path is None


# --------------------------------------------------------------------------- #
# .claude.json slice
# --------------------------------------------------------------------------- #

def test_claude_json_slice_extract_and_apply_surgical(tmp_path):
    claude_json = tmp_path / "claude.json"
    claude_json.write_text(json.dumps({
        "installMethod": "brew",  # untouched machine field
        "projects": {
            "/home/u/proj": {
                "allowedTools": ["Bash"],
                "hasTrustDialogAccepted": True,  # not in whitelist
                "lastCost": 1.23,
            }
        },
    }), encoding="utf-8")
    fields = ["allowedTools", "mcpServers"]
    sl = E.extract_claude_json_slice(claude_json, "/home/u/proj", fields)
    assert sl == {"allowedTools": ["Bash"]}  # only whitelisted present

    # Apply a new slice on the *other machine* (different existing content).
    other = tmp_path / "other.json"
    other.write_text(json.dumps({
        "machineID": "xyz",
        "projects": {"/other/path": {"allowedTools": ["Read"], "lastCost": 9.9}},
    }), encoding="utf-8")
    E.apply_claude_json_slice(other, "/other/path", {"allowedTools": ["Bash"]}, fields)
    data = json.loads(other.read_text())
    assert data["machineID"] == "xyz"                     # machine field preserved
    assert data["projects"]["/other/path"]["allowedTools"] == ["Bash"]  # patched
    assert data["projects"]["/other/path"]["lastCost"] == 9.9           # other field kept


def test_config_slice_roundtrips_through_export_import(tmp_path):
    fields = ["allowedTools"]
    # machine A: export its slice into the repo
    a = make_view(tmp_path / "A", fields=fields)
    a.claude_json_path.write_text(json.dumps({
        "projects": {"/home/u/proj": {"allowedTools": ["Bash", "Read"]}}
    }), encoding="utf-8")
    repo = tmp_path / "repo" / "projects" / "id"
    export_project(a, repo, base={})
    assert (repo / "claude-config.json").exists()

    # machine B: import it (empty claude.json)
    b = make_view(tmp_path / "B", fields=fields)
    import_project(b, repo, base={}, snapshot_dir=tmp_path / "snap")
    data = json.loads(b.claude_json_path.read_text())
    assert data["projects"]["/home/u/proj"]["allowedTools"] == ["Bash", "Read"]


# --------------------------------------------------------------------------- #
# cwd placeholder (cross-machine /resume continuity)
# --------------------------------------------------------------------------- #

def _sess_line(cwd: str, **extra) -> bytes:
    return json.dumps({"type": "user", "cwd": cwd, **extra}).encode() + b"\n"


def test_cwd_transform_roundtrip_and_guards():
    P = "/home/u/proj"
    # root + subdirs round-trip exactly
    for cwd in (P, P + "/src", P + "/src/w"):
        line = _sess_line(cwd)
        ph = E._cwd_to_placeholder(line, P)
        assert b"__CC_PROJECT_ROOT__" in ph
        assert E._placeholder_to_cwd(ph, P) == line
    # sibling sharing a path prefix must NOT be rewritten
    sib = _sess_line(P + "-2/foo")
    assert E._cwd_to_placeholder(sib, P) == sib
    # a foreign cwd (other machine's path) does not match on export
    foreign = _sess_line("/home/other/proj")
    assert E._cwd_to_placeholder(foreign, P) == foreign
    # placeholder form is byte-identical regardless of local path length (no churn)
    assert E._cwd_to_placeholder(_sess_line(P), P) == E._cwd_to_placeholder(
        _sess_line("/a/much/longer/home/proj"), "/a/much/longer/home/proj"
    )
    # nested absolute paths (tool inputs) are left untouched
    nested = json.dumps({
        "type": "assistant", "cwd": P,
        "message": {"content": [{"type": "tool_use", "input": {"file_path": P + "/a.py"}}]},
    }).encode() + b"\n"
    out = json.loads(E._cwd_to_placeholder(nested, P))
    assert out["cwd"] == "__CC_PROJECT_ROOT__"
    assert out["message"]["content"][0]["input"]["file_path"] == P + "/a.py"


def test_session_cwd_normalized_in_repo_and_materialized_per_machine(tmp_path):
    # machine A: session recorded with A's absolute project path
    a = make_view(tmp_path / "A", abs_path="/home/alice/proj")
    write(a.project_dir / "s.jsonl", '{"type":"user","cwd":"/home/alice/proj","m":1}\n')
    repo = tmp_path / "repo" / "projects" / "id"
    export_project(a, repo, base={})

    # repo copy is machine-independent: placeholder, no absolute path
    repo_bytes = (repo / "sessions" / "s.jsonl").read_text()
    assert "__CC_PROJECT_ROOT__" in repo_bytes
    assert "/home/alice/proj" not in repo_bytes

    # machine B (different path) imports -> cwd materialized to B's path
    b = make_view(tmp_path / "B", abs_path="/home/bob/work/proj")
    import_project(b, repo, base={}, snapshot_dir=tmp_path / "snap")
    local_bytes = (b.project_dir / "s.jsonl").read_text()
    assert '"cwd":"/home/bob/work/proj"' in local_bytes
    assert "__CC_PROJECT_ROOT__" not in local_bytes


def test_import_session_no_write_no_snapshot(tmp_path):
    # The common case: local is ahead, merge_session_pair keeps local, nothing is
    # written. Snapshotting here would re-create the whole-subtree-per-run waste.
    view = make_view(tmp_path, abs_path="/home/u/proj")
    local = _sess_line("/home/u/proj", m=1) + _sess_line("/home/u/proj", m=2)
    (view.project_dir / "s.jsonl").write_bytes(local)
    repo = tmp_path / "repo" / "projects" / "id"
    write_bytes(repo / "sessions" / "s.jsonl", _sess_line("__CC_PROJECT_ROOT__", m=1))
    snap = tmp_path / "snap"
    r = import_project(view, repo, base={}, snapshot_dir=snap)
    assert (view.project_dir / "s.jsonl").read_bytes() == local  # untouched
    assert not snap.exists()
    assert r.snapshot_path is None


def test_import_session_content_neutral_rewrite_writes_no_snapshot(tmp_path):
    # Legacy repo files predate the cwd placeholder, so local (read as placeholder)
    # never hash-matches the repo copy (real path) and every run "re-imports" them.
    # The bytes landing on disk are identical to what's there, so nothing is lost
    # and nothing needs preserving — this churn is what filled 5.3GB.
    view = make_view(tmp_path, abs_path="/home/u/proj")
    raw = _sess_line("/home/u/proj", m=1)
    (view.project_dir / "s.jsonl").write_bytes(raw)
    repo = tmp_path / "repo" / "projects" / "id"
    write_bytes(repo / "sessions" / "s.jsonl", raw)  # unnormalized: real path, not placeholder
    snap = tmp_path / "snap"
    r = import_project(view, repo, base={}, snapshot_dir=snap)
    assert (view.project_dir / "s.jsonl").read_bytes() == raw  # disk unchanged
    assert not snap.exists()
    assert r.snapshot_path is None


def test_import_session_append_only_catchup_writes_no_snapshot(tmp_path):
    # The other machine appended: local is a prefix of the repo copy, so the
    # replace loses nothing and a snapshot would be a strict subset of what stays
    # on disk. This is the dominant real-world case — 93/93 files in a live run.
    view = make_view(tmp_path, abs_path="/home/u/proj")
    (view.project_dir / "s.jsonl").write_bytes(_sess_line("/home/u/proj", m=1))
    repo = tmp_path / "repo" / "projects" / "id"
    write_bytes(
        repo / "sessions" / "s.jsonl",
        _sess_line("__CC_PROJECT_ROOT__", m=1) + _sess_line("__CC_PROJECT_ROOT__", m=2),
    )
    snap = tmp_path / "snap"
    r = import_project(view, repo, base={}, snapshot_dir=snap)
    assert "sessions/s.jsonl" in r.materialized  # local WAS replaced
    assert '"m": 2' in (view.project_dir / "s.jsonl").read_text()
    assert not snap.exists()
    assert r.snapshot_path is None


def test_import_session_divergence_snapshots_prior_local_raw(tmp_path):
    # Local has a tail the repo copy lacks, so the wholesale replace destroys it —
    # the snapshot is the only recovery. It must hold raw bytes: written through
    # the session read path it would carry a placeholder cwd, and a session
    # restored from that is silently invisible to native /resume.
    view = make_view(tmp_path, abs_path="/home/u/proj")
    (view.project_dir / "s.jsonl").write_bytes(
        _sess_line("/home/u/proj", m=1) + _sess_line("/home/u/proj", mine=True)
    )
    repo = tmp_path / "repo" / "projects" / "id"
    write_bytes(
        repo / "sessions" / "s.jsonl",
        _sess_line("__CC_PROJECT_ROOT__", m=1)
        + _sess_line("__CC_PROJECT_ROOT__", theirs=True)
        + _sess_line("__CC_PROJECT_ROOT__", m=3),
    )
    snap = tmp_path / "snap"
    r = import_project(view, repo, base={}, snapshot_dir=snap)
    assert "sessions/s.jsonl" in r.materialized  # local was replaced
    snapped = (snap / "sessions" / "s.jsonl").read_bytes()
    assert b'"mine": true' in snapped  # the destroyed tail is recoverable
    assert b"/home/u/proj" in snapped
    assert b"__CC_PROJECT_ROOT__" not in snapped
    assert r.snapshot_path == str(snap)


def test_session_export_is_churn_free_after_roundtrip(tmp_path):
    a = make_view(tmp_path / "A", abs_path="/home/alice/proj")
    write(a.project_dir / "s.jsonl", '{"type":"user","cwd":"/home/alice/proj","m":1}\n')
    repo = tmp_path / "repo" / "projects" / "id"
    export_project(a, repo, base={})
    # base = what this machine now considers synced; a second export must be a no-op
    base = a.local_hashes()
    assert export_project(a, repo, base).written == []
