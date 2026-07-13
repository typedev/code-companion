"""Phase 1 tests for LAN file-sync local core: resolver, index, mirror engine."""

import subprocess
from pathlib import Path

from src.services import file_sync_service as svc
from src.services.file_sync import file_index, file_sync_engine as E, share_spec


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

def write(p: Path, text: str = "x"):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def git(root: Path, *args: str):
    subprocess.run(["git", *args], cwd=str(root), capture_output=True, check=True)


# --------------------------------------------------------------------------- #
# share_spec — the include resolver
# --------------------------------------------------------------------------- #

def test_shared_folder_is_included_by_default(tmp_path):
    write(tmp_path / "shared" / "a.txt")
    write(tmp_path / "shared" / "sub" / "b.txt")
    write(tmp_path / "elsewhere" / "c.txt")
    got = share_spec.resolve_shared_files(tmp_path)
    assert got == {"shared/a.txt", "shared/sub/b.txt"}


def test_dot_shared_allowlist_with_negation(tmp_path):
    write(tmp_path / ".shared", "sources/**\n!sources/**/*.tmp\n")
    write(tmp_path / "sources" / "font.ufo" / "glyph.glif")
    write(tmp_path / "sources" / "scratch.tmp")
    write(tmp_path / "other" / "d.txt")
    got = share_spec.resolve_shared_files(tmp_path)
    assert "sources/font.ufo/glyph.glif" in got
    assert "sources/scratch.tmp" not in got   # negated
    assert "other/d.txt" not in got            # not matched


def test_skip_dirs_and_git_never_descended(tmp_path):
    write(tmp_path / ".shared", "**/*.txt\n")
    write(tmp_path / "node_modules" / "pkg" / "x.txt")
    write(tmp_path / ".venv" / "y.txt")
    write(tmp_path / ".git" / "z.txt")
    write(tmp_path / ".deleted" / "old.txt")
    write(tmp_path / "keep.txt")
    got = share_spec.resolve_shared_files(tmp_path)
    assert got == {"keep.txt"}


def test_git_tracked_files_are_excluded(tmp_path):
    git(tmp_path, "init")
    git(tmp_path, "config", "user.email", "t@t")
    git(tmp_path, "config", "user.name", "t")
    write(tmp_path / "shared" / "tracked.txt")
    write(tmp_path / "shared" / "untracked.txt")
    git(tmp_path, "add", "shared/tracked.txt")
    git(tmp_path, "commit", "-m", "init")
    got = share_spec.resolve_shared_files(tmp_path)
    assert "shared/untracked.txt" in got
    assert "shared/tracked.txt" not in got   # git already moves it


# --------------------------------------------------------------------------- #
# file_index — manifest with a stat cache
# --------------------------------------------------------------------------- #

def test_manifest_hashes_shared_set(tmp_path, monkeypatch):
    monkeypatch.setattr(file_index, "get_config_dir", lambda: tmp_path / "cfg")
    write(tmp_path / "proj" / "shared" / "a.txt", "hello")
    m = file_index.build_manifest(tmp_path / "proj")
    assert set(m) == {"shared/a.txt"}
    assert len(m["shared/a.txt"]) == 64  # sha256 hex


def test_manifest_reuses_cache_for_unchanged_files(tmp_path, monkeypatch):
    monkeypatch.setattr(file_index, "get_config_dir", lambda: tmp_path / "cfg")
    root = tmp_path / "proj"
    write(root / "shared" / "a.txt", "hello")
    file_index.build_manifest(root)  # first build populates the cache

    calls = {"n": 0}
    real_hash = file_index.hash_file

    def counting_hash(p):
        calls["n"] += 1
        return real_hash(p)

    monkeypatch.setattr(file_index, "hash_file", counting_hash)
    file_index.build_manifest(root)  # unchanged -> stat cache hit, no re-hash
    assert calls["n"] == 0

    (root / "shared" / "a.txt").write_text("changed", encoding="utf-8")
    file_index.build_manifest(root)  # changed -> re-hashed
    assert calls["n"] == 1


# --------------------------------------------------------------------------- #
# mirror engine — diff, plan, apply, .deleted recovery
# --------------------------------------------------------------------------- #

def test_diff_manifests():
    local = {"same": "h1", "changed": "hL", "onlyL": "h2"}
    remote = {"same": "h1", "changed": "hR", "onlyR": "h3"}
    d = E.diff_manifests(local, remote)
    assert d.only_remote == {"onlyR"}
    assert d.only_local == {"onlyL"}
    assert d.changed == {"changed"}
    assert not d.identical


def test_plan_get_destructive_count():
    d = E.diff_manifests(
        local={"changed": "hL", "onlyL": "h"},
        remote={"changed": "hR", "onlyR": "h"},
    )
    plan = E.plan_get(d, local={"changed": "hL", "onlyL": "h"})
    assert plan.fetch == {"onlyR", "changed"}
    assert plan.remove == {"onlyL"}
    assert plan.overwrite == {"changed"}
    assert plan.destructive_count == 2  # 1 removed + 1 overwritten


def test_apply_get_backs_up_then_writes(tmp_path):
    root = tmp_path
    write(root / "changed.txt", "old-local")
    write(root / "onlyL.txt", "local-only")
    # peer has: changed.txt (new content) + onlyR.txt; lacks onlyL.txt
    local = {"changed.txt": "hLocal", "onlyL.txt": "hL"}
    remote = {"changed.txt": "hRemote", "onlyR.txt": "hR"}
    d = E.diff_manifests(local, remote)
    plan = E.plan_get(d, local)

    peer_bytes = {"changed.txt": b"new-remote", "onlyR.txt": b"brand-new"}
    E.apply_get(root, plan, lambda r: peer_bytes.get(r), stamp="STAMP")

    # new/overwritten files applied
    assert (root / "changed.txt").read_text() == "new-remote"
    assert (root / "onlyR.txt").read_text() == "brand-new"
    # only-local file removed from the tree
    assert not (root / "onlyL.txt").exists()
    # both destroyed local versions recoverable from .deleted/
    assert (root / ".deleted" / "STAMP" / "onlyL.txt").read_text() == "local-only"
    assert (root / ".deleted" / "STAMP" / "changed.txt").read_text() == "old-local"


def test_git_operation_in_progress(tmp_path):
    (tmp_path / ".git").mkdir()
    assert svc.git_operation_in_progress(str(tmp_path)) is False
    (tmp_path / ".git" / "MERGE_HEAD").write_text("x", encoding="utf-8")
    assert svc.git_operation_in_progress(str(tmp_path)) is True


def test_ensure_deleted_gitignored(tmp_path):
    gi = tmp_path / ".gitignore"
    # no gitignore -> created with the entry
    svc.ensure_deleted_gitignored(str(tmp_path))
    assert ".deleted/" in gi.read_text().splitlines()
    # idempotent -> no duplicate
    svc.ensure_deleted_gitignored(str(tmp_path))
    assert gi.read_text().count(".deleted/") == 1
    # appends to an existing gitignore, preserving prior lines
    gi.write_text("node_modules/\n", encoding="utf-8")
    svc.ensure_deleted_gitignored(str(tmp_path))
    lines = gi.read_text().splitlines()
    assert "node_modules/" in lines and ".deleted/" in lines


def test_apply_get_new_file_needs_no_backup(tmp_path):
    root = tmp_path
    local = {}
    remote = {"n/new.txt": "h"}
    d = E.diff_manifests(local, remote)
    plan = E.plan_get(d, local)
    assert plan.overwrite == set()
    E.apply_get(root, plan, lambda r: b"data", stamp="S")
    assert (root / "n" / "new.txt").read_text() == "data"
    assert not (root / ".deleted").exists()  # nothing was destroyed
