"""In-compositor session agent for the GUI test harness (Part B, decision D2).

Runs as ``cage``'s client, so it inherits cage's ``WAYLAND_DISPLAY``. It launches the
target app (which cage kiosk-fullscreens) and serves a line-delimited JSON control
protocol over a unix socket, running ``grim`` locally where the display is reachable.

Intentionally free of any ``gi``/GTK imports — only the standard library — so it starts
fast and cannot fail on GTK init inside the headless compositor.

Protocol (one JSON object per line, request → response):
- ``{"cmd": "screenshot"}`` -> ``{"ok": true, "png_b64": "<base64 PNG>"}``
- ``{"cmd": "ping"}``       -> ``{"ok": true}``
- ``{"cmd": "stop"}``       -> ``{"ok": true}`` then the agent exits.
Errors -> ``{"ok": false, "error": "..."}``.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import shlex
import signal
import socket
import subprocess
import sys


def _log(msg: str) -> None:
    print(f"[gui-agent] {msg}", file=sys.stderr, flush=True)


class SessionAgent:
    def __init__(self, socket_path: str, cmd: str, width: int, height: int):
        self.socket_path = socket_path
        self.cmd = cmd
        self.width = width
        self.height = height
        self.app: subprocess.Popen | None = None

    # -- setup ---------------------------------------------------------- #
    def _set_canvas(self) -> None:
        """Resize the headless output to the requested canvas (best-effort)."""
        try:
            subprocess.run(
                ["wlr-randr", "--output", "HEADLESS-1",
                 "--custom-mode", f"{self.width}x{self.height}"],
                check=False, capture_output=True, timeout=10,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            _log(f"wlr-randr failed (continuing): {exc}")

    def _launch_app(self) -> None:
        env = dict(os.environ, GDK_BACKEND="wayland")
        self.app = subprocess.Popen(shlex.split(self.cmd), env=env)
        _log(f"launched app pid={self.app.pid}: {self.cmd}")

    # -- commands ------------------------------------------------------- #
    def _screenshot(self) -> dict:
        result = subprocess.run(
            ["grim", "-"], capture_output=True, timeout=15,
        )
        if result.returncode != 0:
            return {"ok": False,
                    "error": f"grim failed: {result.stderr.decode(errors='replace')}"}
        return {"ok": True, "png_b64": base64.b64encode(result.stdout).decode()}

    def _handle(self, request: dict) -> tuple[dict, bool]:
        """Return (response, should_stop)."""
        cmd = request.get("cmd")
        if cmd == "ping":
            return {"ok": True}, False
        if cmd == "screenshot":
            try:
                return self._screenshot(), False
            except (OSError, subprocess.SubprocessError) as exc:
                return {"ok": False, "error": f"screenshot error: {exc}"}, False
        if cmd == "stop":
            return {"ok": True}, True
        return {"ok": False, "error": f"unknown command: {cmd}"}, False

    # -- serve ---------------------------------------------------------- #
    def run(self) -> int:
        self._set_canvas()
        self._launch_app()

        if os.path.exists(self.socket_path):
            os.unlink(self.socket_path)
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(self.socket_path)
        srv.listen(1)
        _log(f"listening on {self.socket_path}")

        try:
            while True:
                conn, _ = srv.accept()
                with conn:
                    if self._serve_conn(conn):
                        break
        finally:
            self._teardown(srv)
        return 0

    def _serve_conn(self, conn: socket.socket) -> bool:
        """Serve one connection; return True if a stop was requested."""
        conn_file = conn.makefile("rwb")
        while True:
            line = conn_file.readline()
            if not line:
                return False
            try:
                request = json.loads(line)
            except ValueError:
                self._send(conn_file, {"ok": False, "error": "invalid JSON"})
                continue
            response, should_stop = self._handle(request)
            self._send(conn_file, response)
            if should_stop:
                return True

    @staticmethod
    def _send(conn_file, payload: dict) -> None:
        conn_file.write((json.dumps(payload) + "\n").encode())
        conn_file.flush()

    def _teardown(self, srv: socket.socket) -> None:
        if self.app is not None and self.app.poll() is None:
            self.app.terminate()
            try:
                self.app.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.app.kill()
        srv.close()
        if os.path.exists(self.socket_path):
            os.unlink(self.socket_path)
        _log("stopped")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="GUI harness session agent")
    parser.add_argument("--socket", required=True)
    parser.add_argument("--cmd", required=True)
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=800)
    args = parser.parse_args(argv)

    # Exit cleanly if cage/parent goes away.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))

    agent = SessionAgent(args.socket, args.cmd, args.width, args.height)
    return agent.run()


if __name__ == "__main__":
    sys.exit(main())
