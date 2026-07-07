"""In-compositor session agent for the GUI test harness (Part B, decision D2).

Runs as ``cage``'s client, so it inherits cage's ``WAYLAND_DISPLAY``. It brings up the
AT-SPI accessibility stack, launches the target app (which cage kiosk-fullscreens), and
serves a line-delimited JSON control protocol over a unix socket. ``grim`` (capture) and
``Atspi`` (semantic inspection/action) both run here, where the display + a11y bus are
reachable.

The a11y bus and registry are launched **manually** (D-Bus autostart of ``org.a11y.Bus``
fails under the private ``dbus-run-session`` bus), which is why this agent owns them.

Protocol (one JSON object per line, request → response):
- ``{"cmd":"screenshot"}``                    -> ``{"ok":true,"png_b64":…}``
- ``{"cmd":"tree"}``                           -> ``{"ok":true,"tree":<node>}``
- ``{"cmd":"do_action","role":…,"name":…,"action":…?}`` -> ``{"ok":true}``
- ``{"cmd":"click","role":…,"name":…}``        -> ``{"ok":true}``
- ``{"cmd":"type","role":…,"name":…,"text":…}``-> ``{"ok":true}``
- ``{"cmd":"ping"}`` / ``{"cmd":"stop"}``
Errors -> ``{"ok":false,"error":"…"}``. ``<node>`` = ``{role,name,extents:[x,y,w,h]|null,
children:[…]}``.
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
import time

import gi

gi.require_version("Atspi", "2.0")
from gi.repository import Atspi  # noqa: E402

# Desktop children that are infrastructure, not the app under test.
_INFRA_APPS = {"xdg-desktop-portal-gtk", "gnome-shell", ""}


def _log(msg: str) -> None:
    print(f"[gui-agent] {msg}", file=sys.stderr, flush=True)


# --------------------------------------------------------------------------- #
# AT-SPI helpers — instance-method style so tests can pass duck-typed fakes.
# --------------------------------------------------------------------------- #
def _node_extents(node):
    """Return [x, y, w, h] in screen coords, or None if the node has no geometry."""
    try:
        ext = Atspi.Component.get_extents(node, Atspi.CoordType.SCREEN)
        return [ext.x, ext.y, ext.width, ext.height]
    except Exception:  # noqa: BLE001 - many container/app nodes lack Component
        return None


def serialize_tree(node, max_depth: int = 40) -> dict:
    """Recursively serialize an AT-SPI node to plain dicts."""
    result = {
        "role": node.get_role_name(),
        "name": node.get_name(),
        "extents": _node_extents(node),
        "children": [],
    }
    if max_depth > 0:
        for i in range(node.get_child_count()):
            child = node.get_child_at_index(i)
            if child is not None:
                result["children"].append(serialize_tree(child, max_depth - 1))
    return result


def find_node(node, role=None, name=None):
    """Depth-first search for the first node matching role and/or name."""
    if (role is None or node.get_role_name() == role) and \
            (name is None or node.get_name() == name):
        return node
    for i in range(node.get_child_count()):
        child = node.get_child_at_index(i)
        if child is not None:
            hit = find_node(child, role, name)
            if hit is not None:
                return hit
    return None


def find_target_app(desktop):
    """Return the single non-infrastructure desktop child (the app under test)."""
    for i in range(desktop.get_child_count()):
        child = desktop.get_child_at_index(i)
        if child is not None and child.get_name() not in _INFRA_APPS:
            return child
    return None


class SessionAgent:
    def __init__(self, socket_path: str, cmd: str, width: int, height: int):
        self.socket_path = socket_path
        self.cmd = cmd
        self.width = width
        self.height = height
        self.app: subprocess.Popen | None = None
        self.a11y_procs: list[subprocess.Popen] = []
        self._atspi_ready = False

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

    def _start_a11y(self) -> None:
        """Launch the AT-SPI bus + registry manually (autostart fails on a private bus)."""
        self.a11y_procs.append(subprocess.Popen(
            ["/usr/libexec/at-spi-bus-launcher", "--launch-immediately", "--a11y=1"]
        ))
        time.sleep(1.5)
        self.a11y_procs.append(subprocess.Popen(["/usr/libexec/at-spi2-registryd"]))
        time.sleep(1.5)
        _log("a11y stack started")

    def _launch_app(self) -> None:
        env = dict(os.environ, GDK_BACKEND="wayland")
        self.app = subprocess.Popen(shlex.split(self.cmd), env=env)
        _log(f"launched app pid={self.app.pid}: {self.cmd}")

    def _ensure_atspi(self):
        """Init AT-SPI once and return the desktop root."""
        if not self._atspi_ready:
            Atspi.init()
            self._atspi_ready = True
        return Atspi.get_desktop(0)

    # -- commands ------------------------------------------------------- #
    def _screenshot(self) -> dict:
        result = subprocess.run(["grim", "-"], capture_output=True, timeout=15)
        if result.returncode != 0:
            return {"ok": False,
                    "error": f"grim failed: {result.stderr.decode(errors='replace')}"}
        return {"ok": True, "png_b64": base64.b64encode(result.stdout).decode()}

    def _tree(self) -> dict:
        app = find_target_app(self._ensure_atspi())
        if app is None:
            return {"ok": False, "error": "target app not found in a11y tree"}
        return {"ok": True, "tree": serialize_tree(app)}

    def _locate(self, role, name):
        app = find_target_app(self._ensure_atspi())
        if app is None:
            return None, {"ok": False, "error": "target app not found in a11y tree"}
        node = find_node(app, role, name)
        if node is None:
            return None, {"ok": False,
                          "error": f"no node matching role={role!r} name={name!r}"}
        return node, None

    def _do_action(self, role, name, action) -> dict:
        node, err = self._locate(role, name)
        if err:
            return err
        n_actions = Atspi.Action.get_n_actions(node)
        if n_actions == 0:
            return {"ok": False, "error": "node has no actions"}
        idx = 0
        if action is not None:
            idx = next(
                (i for i in range(n_actions)
                 if Atspi.Action.get_action_name(node, i) == action),
                None,
            )
            if idx is None:
                return {"ok": False, "error": f"no action named {action!r}"}
        Atspi.Action.do_action(node, idx)
        return {"ok": True}

    def _type(self, role, name, text) -> dict:
        node, err = self._locate(role, name)
        if err:
            return err
        try:
            Atspi.EditableText.set_text_contents(node, text)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"cannot set text: {exc}"}
        return {"ok": True}

    def _handle(self, request: dict) -> tuple[dict, bool]:
        """Return (response, should_stop)."""
        cmd = request.get("cmd")
        try:
            if cmd == "ping":
                return {"ok": True}, False
            if cmd == "screenshot":
                return self._screenshot(), False
            if cmd == "tree":
                return self._tree(), False
            if cmd == "do_action":
                return self._do_action(
                    request.get("role"), request.get("name"), request.get("action")
                ), False
            if cmd == "click":
                return self._do_action(request.get("role"), request.get("name"), None), False
            if cmd == "type":
                return self._type(
                    request.get("role"), request.get("name"), request.get("text", "")
                ), False
            if cmd == "stop":
                return {"ok": True}, True
        except (OSError, subprocess.SubprocessError) as exc:
            return {"ok": False, "error": f"{cmd} error: {exc}"}, False
        except Exception as exc:  # noqa: BLE001 - surface AT-SPI failures as tool errors
            return {"ok": False, "error": f"{cmd} error: {exc}"}, False
        return {"ok": False, "error": f"unknown command: {cmd}"}, False

    # -- serve ---------------------------------------------------------- #
    def run(self) -> int:
        self._set_canvas()
        self._start_a11y()
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
        procs = ([self.app] if self.app else []) + list(reversed(self.a11y_procs))
        for proc in procs:
            if proc.poll() is None:
                proc.terminate()
        for proc in procs:
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
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

    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))

    agent = SessionAgent(args.socket, args.cmd, args.width, args.height)
    return agent.run()


if __name__ == "__main__":
    sys.exit(main())
