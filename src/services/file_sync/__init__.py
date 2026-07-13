"""LAN project file-sync: mirror a project's gitignored working files between
two machines on the same network (directional, opt-in via ``.shared``/``shared/``).

Phase 1 (this package's local core): the include resolver (``share_spec``), the
persistent hash index (``file_index``), and the pure directional-mirror engine
(``file_sync_engine``). Transport and UI live in later phases.
"""
