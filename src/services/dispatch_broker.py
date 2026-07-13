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
from typing import Awaitable, Callable

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from ..dispatch.pty_bridge import handle_connection
from ..utils import claude_session
from .paired_devices import PairedDevices

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
    ) -> None:
        self.http_port = port
        self.pty_port = port + 1
        self._pair_prompt = pair_prompt
        self.paired = paired or PairedDevices()

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

        if self.paired.is_paired(device_id):  # idempotent re-pair
            return JSONResponse({"token": self.paired.token_for(device_id)})

        try:
            allowed = await self._pair_prompt(device_id, device_name)
        except Exception:
            allowed = False
        if not allowed:
            return JSONResponse({"error": "denied"}, status_code=403)

        token = self.paired.add(device_id, device_name)
        return JSONResponse({"token": token})

    async def _route_sessions(self, request: Request) -> JSONResponse:
        if self._bearer(request) is None:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        # tmux calls are quick but blocking; keep the loop responsive.
        sessions = await asyncio.get_running_loop().run_in_executor(None, _list_sessions)
        return JSONResponse({"pty_port": self.pty_port, "sessions": sessions})

    def _build_app(self) -> Starlette:
        return Starlette(
            routes=[
                Route("/pair", self._route_pair, methods=["POST"]),
                Route("/sessions", self._route_sessions, methods=["GET"]),
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

    async def _amain(self) -> None:
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
