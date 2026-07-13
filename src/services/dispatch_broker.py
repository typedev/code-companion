"""Desktop-side local-dispatch broker (one per machine, run by the PM).

Exposes, on the LAN, this machine's live Claude sessions to *paired* devices:

* ``POST /pair``          — bootstrap: unknown device → "Allow this device?" on
                            the desktop → issue a per-device token.
* ``GET  /sessions``      — (bearer) list live ``cc-*`` sessions + held state.
* raw-TCP PTY bridge      — (bearer handshake) attach a chosen session's PTY.

The HTTP API runs under uvicorn on ``dispatch.port``; the PTY bridge is a raw
framed TCP server on ``dispatch.port + 1`` (see :mod:`..dispatch.protocol`). Both
share one background thread + asyncio loop, mirroring ``McpServer``. Binding is
``0.0.0.0`` (LAN) — the broker only starts when the PM holds the ManagerLock and
``dispatch.enabled`` is true.
"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from typing import Awaitable, Callable

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

from ..dispatch.pty_bridge import handle_connection
from ..utils import claude_session
from . import workspace_readonly
from .file_sync import file_index, project_resolver, share_spec, wire
from .paired_devices import PairedDevices
from .remote_tokens import RemoteTokens

# Read-only panel views the laptop may pull. Computed DIRECTLY from the session's
# project path (a dispatched session is free -> its window is closed -> its MCP
# server is stopped, so we can't proxy it). Whitelist-gated.
_PANELS = frozenset({"list_changes", "get_file_diff", "list_files", "read_file", "get_problems"})

# pair_prompt(device_id, device_name) -> await -> allowed?  Injected by the PM so
# the broker stays GTK-agnostic (tests pass an auto-allow/deny coroutine).
PairPrompt = Callable[[str, str], Awaitable[bool]]


def _list_sessions() -> list[dict]:
    """Live ``cc-*`` sessions with project path/name and held (attached) state."""
    out = []
    for name in sorted(claude_session.live_session_names()):
        cwd = claude_session.session_cwd(name)
        held = claude_session.session_clients(name) > 0
        out.append(
            {
                "name": name,
                "project_path": cwd,
                "project_name": cwd.rsplit("/", 1)[-1] if cwd else name,
                "held": held,
            }
        )
    return out


class DispatchBroker:
    def __init__(
        self,
        port: int,
        pair_prompt: PairPrompt,
        *,
        paired: PairedDevices | None = None,
        resolve_project: Callable[[str], str | None] | None = None,
        remote_tokens: RemoteTokens | None = None,
    ) -> None:
        self.http_port = port
        self.pty_port = port + 1
        self._pair_prompt = pair_prompt
        self.paired = paired or PairedDevices()
        # Tokens we hold as a *client* for peers we paired with — needed to pull
        # back from a Give requester.
        self._remote_tokens = remote_tokens or RemoteTokens()
        # Map a machine-independent project_id -> local path (registry-backed by
        # default; injectable for tests). Lets file-sync address a project without
        # a live session.
        self._resolve_project = resolve_project or project_resolver.resolve_path_for_id

        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._server: uvicorn.Server | None = None
        self._pty_server: asyncio.AbstractServer | None = None

    # ---- auth --------------------------------------------------------------
    def _bearer(self, request: Request) -> dict | None:
        header = request.headers.get("authorization", "")
        if not header.lower().startswith("bearer "):
            return None
        return self.paired.device_for_token(header[7:].strip())

    def _authorize_pty(self, token: str) -> bool:
        return self.paired.device_for_token(token) is not None

    # ---- routes ------------------------------------------------------------
    async def _route_pair(self, request: Request) -> JSONResponse:
        try:
            body = await request.json()
        except (ValueError, TypeError):
            return JSONResponse({"error": "bad request"}, status_code=400)
        device_id = str(body.get("device_id", "")).strip()
        device_name = str(body.get("device_name", "")).strip() or "unknown device"
        if not device_id:
            return JSONResponse({"error": "device_id required"}, status_code=400)

        # Mutual pairing: a token the peer issued us to call it back. Store it only
        # once the pairing is authorized (existing or freshly allowed), keyed by the
        # peer's device_id, so file-sync works in both directions.
        callback = str(body.get("callback_token", "")).strip()

        if self.paired.is_paired(device_id):  # idempotent re-pair
            if callback:
                self._remote_tokens.set(device_id, device_name, callback)
            return JSONResponse({"token": self.paired.token_for(device_id)})

        try:
            allowed = await self._pair_prompt(device_id, device_name)
        except Exception:
            allowed = False
        if not allowed:
            return JSONResponse({"error": "denied"}, status_code=403)

        token = self.paired.add(device_id, device_name)
        if callback:
            self._remote_tokens.set(device_id, device_name, callback)
        return JSONResponse({"token": token})

    async def _route_sessions(self, request: Request) -> JSONResponse:
        if self._bearer(request) is None:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        # tmux calls are quick but blocking; keep the loop responsive.
        sessions = await asyncio.get_running_loop().run_in_executor(None, _list_sessions)
        return JSONResponse({"pty_port": self.pty_port, "sessions": sessions})

    async def _route_panel(self, request: Request) -> JSONResponse:
        """Compute a whitelisted read-only panel view from the session's path."""
        if self._bearer(request) is None:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        session = request.path_params["session"]
        tool = request.path_params["tool"]
        if tool not in _PANELS:
            return JSONResponse({"error": "not allowed"}, status_code=403)

        cwd = claude_session.session_cwd(session)
        if not cwd:
            return JSONResponse({"error": "session path unknown"}, status_code=404)

        params = request.query_params
        path = params.get("path", "")
        staged = str(params.get("staged", "")).lower() in ("1", "true", "yes")
        try:
            max_bytes = int(params.get("max_bytes", 1_000_000))
        except ValueError:
            max_bytes = 1_000_000

        # git/linter/file work is blocking — keep it off the broker's event loop.
        try:
            if tool == "get_file_diff":
                data = await asyncio.to_thread(workspace_readonly.get_file_diff, cwd, path, staged)
            elif tool == "read_file":
                data = await asyncio.to_thread(workspace_readonly.read_file, cwd, path, max_bytes)
            else:
                fn = getattr(workspace_readonly, tool)
                data = await asyncio.to_thread(fn, cwd)
        except Exception as exc:
            return JSONResponse({"error": str(exc)}, status_code=502)
        return JSONResponse(data)

    # ---- file-sync (project-addressed, read-only) --------------------------
    async def _filesync_project_path(self, request: Request) -> tuple[str | None, dict]:
        """(local_path | None, body) for a bearer-authorized file-sync request."""
        if self._bearer(request) is None:
            return None, {"_status": 401, "error": "unauthorized"}
        try:
            body = await request.json()
        except (ValueError, TypeError):
            return None, {"_status": 400, "error": "bad request"}
        project_id = str(body.get("project_id", "")).strip()
        if not project_id:
            return None, {"_status": 400, "error": "project_id required"}
        path = await asyncio.to_thread(self._resolve_project, project_id)
        if not path:
            return None, {"_status": 404, "error": "project not found"}
        return path, body

    async def _route_filesync_manifest(self, request: Request) -> JSONResponse:
        path, body = await self._filesync_project_path(request)
        if path is None:
            return JSONResponse({"error": body["error"]}, status_code=body["_status"])
        manifest = await asyncio.to_thread(file_index.build_manifest, path)
        return JSONResponse({"manifest": manifest})

    async def _route_filesync_fetch(self, request: Request):
        path, body = await self._filesync_project_path(request)
        if path is None:
            return JSONResponse({"error": body["error"]}, status_code=body["_status"])
        requested = [r for r in (body.get("rels") or []) if isinstance(r, str)]
        # Sandbox: only serve rels that are actually in the resolved shared set —
        # this excludes git-tracked/.git/.deleted and blocks any path escape.
        allowed = await asyncio.to_thread(share_spec.resolve_shared_files, path)
        safe = [r for r in requested if r in allowed]
        root = Path(path)

        def stream():
            for rel in safe:
                try:
                    data = (root / rel).read_bytes()
                except OSError:
                    continue
                yield wire.encode_file(rel, data)

        return StreamingResponse(stream(), media_type="application/octet-stream")

    async def _route_filesync_pull_request(self, request: Request) -> JSONResponse:
        """Give trigger: a paired requester asks us to Get this project from them.

        We connect back to the requester's broker (their host = the request's
        source address; port + device id from the body) using the token we hold
        for them, and run a Get in the background. Requires mutual pairing.
        """
        path, body = await self._filesync_project_path(request)
        if path is None:
            return JSONResponse({"error": body["error"]}, status_code=body["_status"])
        source_id = str(body.get("source_device_id", "")).strip()
        try:
            source_port = int(body.get("source_port", 0))
        except (TypeError, ValueError):
            source_port = 0
        token = self._remote_tokens.token_for(source_id) if source_id else None
        source_host = request.client.host if request.client else None
        if not token or not source_host or not source_port:
            return JSONResponse({"error": "not paired with requester"}, status_code=403)

        project_id = str(body.get("project_id", "")).strip()
        peer_host, peer_port, peer_token = source_host, source_port, token

        async def _pull_back() -> None:
            # file_sync_service uses blocking urllib — keep it off the event loop.
            from . import file_sync_service
            peer = file_sync_service.Peer(source_id, "", peer_host, peer_port, peer_token)
            try:
                await asyncio.to_thread(
                    file_sync_service.run_get, path, project_id, peer
                )
            except Exception:
                pass

        asyncio.get_running_loop().create_task(_pull_back())
        return JSONResponse({"status": "started"})

    def _build_app(self) -> Starlette:
        return Starlette(
            routes=[
                Route("/pair", self._route_pair, methods=["POST"]),
                Route("/sessions", self._route_sessions, methods=["GET"]),
                Route("/{session}/panel/{tool}", self._route_panel, methods=["GET"]),
                Route("/filesync/manifest", self._route_filesync_manifest, methods=["POST"]),
                Route("/filesync/fetch", self._route_filesync_fetch, methods=["POST"]),
                Route("/filesync/pull-request", self._route_filesync_pull_request, methods=["POST"]),
            ]
        )

    # ---- lifecycle ---------------------------------------------------------
    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run, name="dispatch-broker", daemon=True
        )
        self._thread.start()

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        try:
            loop.run_until_complete(self._amain())
        except (Exception, asyncio.CancelledError):
            pass
        finally:
            loop.close()

    async def _await_ports_free(self, timeout: float = 8.0) -> None:
        """Wait for both ports to be bindable.

        A quick disable/enable or PM restart can leave the previous broker's
        sockets held for a moment; without this the fresh broker would hit
        "address already in use", die silently, and never retry — leaving the
        machine invisible on the LAN. Poll until free (or give up and try anyway).
        """
        import socket as _socket

        def free(port: int) -> bool:
            try:
                with _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM) as probe:
                    probe.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 1)
                    probe.bind(("0.0.0.0", port))
                return True
            except OSError:
                return False

        for _ in range(max(1, int(timeout / 0.3))):
            if free(self.http_port) and free(self.pty_port):
                return
            await asyncio.sleep(0.3)

    async def _amain(self) -> None:
        await self._await_ports_free()
        # start_server begins accepting immediately; it needs no serve_forever(),
        # so uvicorn's server.serve() is the only thing we block on. That lets
        # should_exit shut everything down gracefully (no CancelledError storm).
        pty_server = await asyncio.start_server(
            lambda r, w: handle_connection(r, w, authorize=self._authorize_pty),
            "0.0.0.0",
            self.pty_port,
        )
        self._pty_server = pty_server

        config = uvicorn.Config(
            self._build_app(),
            host="0.0.0.0",
            port=self.http_port,
            log_level="warning",
            access_log=False,
            loop="asyncio",
        )
        server = uvicorn.Server(config)
        server.install_signal_handlers = lambda: None
        self._server = server
        try:
            await server.serve()
        finally:
            pty_server.close()
            try:
                await pty_server.wait_closed()
            except Exception:
                pass

    def stop(self, timeout: float = 5) -> None:
        loop = self._loop
        if loop is not None and self._server is not None:
            server = self._server
            try:
                loop.call_soon_threadsafe(lambda: setattr(server, "should_exit", True))
            except RuntimeError:
                pass
        if self._thread is not None:
            self._thread.join(timeout)
        self._thread = None
        self._loop = None
        self._server = None
        self._pty_server = None
