"""GUI test harness manager (Part B) — drives headless GTK/Qt apps for inspection.

Owns one persistent subprocess tree per launched app
(``dbus-run-session -> cage[headless] -> gui_agent``) and a unix-socket control channel
to the in-compositor agent (see :mod:`.gui_agent`, decision D2). All work here is
blocking subprocess/socket I/O with no GTK, so it runs on the MCP worker thread.

The whole tree cascades down when the ``dbus-run-session`` root PID is killed, which is
how :meth:`GuiHarnessManager.stop` guarantees teardown even if the agent is unresponsive.
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

_AGENT_PATH = str(Path(__file__).parent / "gui_agent.py")

# wlroots headless backend env for cage (validated in the spike).
_CAGE_ENV = [
    "WLR_BACKENDS=headless",
    "WLR_LIBINPUT_NO_DEVICES=1",
    "WLR_HEADLESS_OUTPUTS=1",
]


class GuiHarnessError(Exception):
    """Raised for harness launch/command failures."""


class _Harness:
    """A single running harness: the process tree + its control connection."""

    def __init__(self, handle: str, proc: subprocess.Popen, workdir: str, conn_file):
        self.handle = handle
        self.proc = proc
        self.workdir = workdir
        self._conn_file = conn_file

    def command(self, payload: dict, timeout: float = 20) -> dict:
        """Send one JSON command and return the parsed reply."""
        self._conn_file.write((json.dumps(payload) + "\n").encode())
        self._conn_file.flush()
        line = self._conn_file.readline()
        if not line:
            raise GuiHarnessError("agent closed the connection")
        return json.loads(line)

    def teardown(self, timeout: float = 5) -> None:
        # Graceful stop first, then kill the tree root as a backstop.
        try:
            self.command({"cmd": "stop"}, timeout=2)
        except (OSError, GuiHarnessError, ValueError):
            pass
        try:
            self._conn_file.close()
        except OSError:
            pass
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        shutil.rmtree(self.workdir, ignore_errors=True)


class GuiHarnessManager:
    """Launches and controls headless GUI harnesses, keyed by an opaque handle."""

    def __init__(self):
        self._harnesses: dict[str, _Harness] = {}
        self._counter = 0

    # -- argv (pure, unit-tested) -------------------------------------- #
    def _build_launch_argv(
        self, socket_path: str, cmd: str, width: int, height: int
    ) -> list[str]:
        return [
            "dbus-run-session", "--",
            "env", *_CAGE_ENV,
            "cage", "--",
            sys.executable, _AGENT_PATH,
            "--socket", socket_path,
            "--cmd", cmd,
            "--width", str(width),
            "--height", str(height),
        ]

    # -- connection (real; seam for tests) ----------------------------- #
    def _open_channel(self, socket_path: str, timeout: float):
        """Poll until the agent's socket is connectable; return a rw file object."""
        deadline = time.monotonic() + timeout
        last_err: OSError | None = None
        while time.monotonic() < deadline:
            try:
                sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                sock.connect(socket_path)
                return sock.makefile("rwb")
            except OSError as exc:
                last_err = exc
                time.sleep(0.1)
        raise GuiHarnessError(f"agent socket never became ready: {last_err}")

    # -- lifecycle ----------------------------------------------------- #
    def launch(self, cmd: str, width: int = 1280, height: int = 800,
               timeout: float = 30) -> str:
        self._counter += 1
        handle = f"gui-{self._counter}"
        workdir = tempfile.mkdtemp(prefix="cc-gui-")
        socket_path = os.path.join(workdir, "agent.sock")

        argv = self._build_launch_argv(socket_path, cmd, width, height)
        proc = subprocess.Popen(argv)
        try:
            conn_file = self._open_channel(socket_path, timeout)
        except Exception:
            if proc.poll() is None:
                proc.terminate()
            shutil.rmtree(workdir, ignore_errors=True)
            raise

        self._harnesses[handle] = _Harness(handle, proc, workdir, conn_file)
        return handle

    def screenshot(self, handle: str) -> bytes:
        harness = self._get(handle)
        reply = harness.command({"cmd": "screenshot"})
        if not reply.get("ok"):
            raise GuiHarnessError(reply.get("error", "screenshot failed"))
        import base64
        return base64.b64decode(reply["png_b64"])

    def snapshot_tree(self, handle: str) -> dict:
        reply = self._get(handle).command({"cmd": "tree"})
        if not reply.get("ok"):
            raise GuiHarnessError(reply.get("error", "tree failed"))
        return reply["tree"]

    def click(self, handle: str, role=None, name=None) -> None:
        self._command_ok(handle, {"cmd": "click", "role": role, "name": name})

    def type_text(self, handle: str, role=None, name=None, text: str = "") -> None:
        self._command_ok(
            handle, {"cmd": "type", "role": role, "name": name, "text": text}
        )

    def do_action(self, handle: str, role=None, name=None, action=None) -> None:
        self._command_ok(
            handle, {"cmd": "do_action", "role": role, "name": name, "action": action}
        )

    def _command_ok(self, handle: str, payload: dict) -> None:
        reply = self._get(handle).command(payload)
        if not reply.get("ok"):
            raise GuiHarnessError(reply.get("error", "command failed"))

    def stop(self, handle: str) -> None:
        harness = self._harnesses.pop(handle, None)
        if harness is None:
            raise GuiHarnessError(f"unknown handle: {handle}")
        harness.teardown()

    def stop_all(self) -> None:
        for handle in list(self._harnesses):
            try:
                self.stop(handle)
            except GuiHarnessError:
                pass

    def _get(self, handle: str) -> _Harness:
        harness = self._harnesses.get(handle)
        if harness is None:
            raise GuiHarnessError(f"unknown handle: {handle}")
        return harness
