"""Cached per-session observability index (Phase 8.1).

Parsing a session's JSONL for tokens / touched files / timing is cheap but not
free, and panels re-request the same sessions constantly. This service caches one
``SessionInsight`` per session, keyed by the file's ``(mtime_ns, size)`` so an
unchanged session is never re-parsed; a still-writing (growing) session is.

Storage mirrors ``session_summary_service``: one JSON index per project under
``<config>/session-insights/<project_key>.json`` (``project_key`` is the
machine-independent git identity), written atomically. In-memory state is guarded
by a lock like ``ProjectStatusService`` — workers parse, the main thread reads.

Parsing itself is delegated to the active ``HistoryAdapter`` (provider-agnostic);
callers run these methods off the GTK thread via ``run_async``.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

from ..models import Session, SessionInsight
from ..utils.atomic_write import atomic_write_text
from ..utils.text_files import capture_stat
from . import session_summary_service
from .config_path import get_config_dir

# Bump when the parser's extraction logic changes so stale cache entries (keyed
# only on file stat) are discarded instead of served with the old semantics.
_SCHEMA_VERSION = 3


class SessionInsightService:
    """Singleton cache of ``SessionInsight`` records, one index file per project."""

    _instance: "SessionInsightService | None" = None

    def __init__(self):
        self._dir = get_config_dir() / "session-insights"
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        self._lock = threading.Lock()
        self._indexes: dict[str, dict] = {}  # project_key -> {session_id: entry}
        self._loaded: set[str] = set()
        self._key_cache: dict[str, str] = {}  # resolved project path -> project_key

    @classmethod
    def get_instance(cls) -> "SessionInsightService":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # -- public API -------------------------------------------------------

    def get_insight(
        self, session: Session, adapter, project_path, project_id: str | None = None
    ) -> SessionInsight:
        """Return the cached insight for ``session``, re-parsing if the file changed."""
        key = self._key(project_path, project_id)
        stat = capture_stat(session.path)

        with self._lock:
            index = self._ensure_loaded(key)
            hit = self._cached(index, session, stat)
        if hit is not None:
            return hit

        insight = adapter.get_session_insight(session)
        if stat is not None:
            with self._lock:
                index = self._ensure_loaded(key)
                index[session.id] = {"stat": list(stat), "insight": insight.to_dict()}
                self._save_index(key)
        return insight

    def get_project_insights(
        self, adapter, project_path, project_id: str | None = None
    ) -> list[SessionInsight]:
        """Insights for every session of a project (cache-or-parse), one disk write."""
        key = self._key(project_path, project_id)
        sessions = adapter.get_sessions_for_path(Path(project_path))

        results: dict[str, SessionInsight] = {}
        misses: list[tuple[Session, tuple[int, int] | None]] = []
        with self._lock:
            index = self._ensure_loaded(key)
            for session in sessions:
                stat = capture_stat(session.path)
                hit = self._cached(index, session, stat)
                if hit is not None:
                    results[session.id] = hit
                else:
                    misses.append((session, stat))

        for session, stat in misses:
            results[session.id] = adapter.get_session_insight(session)

        fresh = [(s, st) for s, st in misses if st is not None]
        if fresh:
            with self._lock:
                index = self._ensure_loaded(key)
                for session, stat in fresh:
                    index[session.id] = {
                        "stat": list(stat),
                        "insight": results[session.id].to_dict(),
                    }
                self._save_index(key)

        return [results[s.id] for s in sessions if s.id in results]

    def get_latest_insight(
        self, adapter, project_path, project_id: str | None = None
    ) -> SessionInsight | None:
        """Insight for the most recently active session (newest mtime), or None."""
        sessions = adapter.get_sessions_for_path(Path(project_path))
        if not sessions:
            return None
        newest = max(sessions, key=self._session_mtime)
        return self.get_insight(newest, adapter, project_path, project_id)

    # -- internals --------------------------------------------------------

    @staticmethod
    def _session_mtime(session: Session) -> float:
        try:
            return session.path.stat().st_mtime
        except OSError:
            return 0.0

    @staticmethod
    def _cached(index: dict, session: Session, stat) -> SessionInsight | None:
        """Return the stored insight iff the file's stat matches the cached stamp."""
        entry = index.get(session.id)
        if not entry or stat is None:
            return None
        if tuple(entry.get("stat") or []) != stat:
            return None
        try:
            return SessionInsight.from_dict(entry["insight"], session.path)
        except (KeyError, TypeError, ValueError):
            return None

    def _key(self, project_path, project_id: str | None) -> str:
        if project_id:
            return project_id
        cache_key = str(Path(project_path).resolve())
        with self._lock:
            cached = self._key_cache.get(cache_key)
        if cached is not None:
            return cached
        key = session_summary_service.project_key(project_path)
        with self._lock:
            self._key_cache[cache_key] = key
        return key

    def _ensure_loaded(self, key: str) -> dict:
        """Load a project index from disk once; return the in-memory dict. Locked."""
        if key not in self._loaded:
            entries: dict = {}
            path = self._index_path(key)
            if path.is_file():
                try:
                    loaded = json.loads(path.read_text(encoding="utf-8"))
                    # Discard entries written by an older parser (stat-only keying
                    # can't detect a logic change).
                    if isinstance(loaded, dict) and loaded.get("_schema") == _SCHEMA_VERSION:
                        stored = loaded.get("entries")
                        if isinstance(stored, dict):
                            entries = stored
                except (OSError, json.JSONDecodeError):
                    entries = {}
            self._indexes[key] = entries
            self._loaded.add(key)
        return self._indexes[key]

    def _index_path(self, key: str) -> Path:
        return self._dir / f"{key}.json"

    def _save_index(self, key: str) -> None:
        try:
            payload = {"_schema": _SCHEMA_VERSION, "entries": self._indexes.get(key, {})}
            atomic_write_text(self._index_path(key), json.dumps(payload, indent=2))
        except OSError:
            pass
