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
- ``{"cmd":"do_action","role":…,"name":…,"action":…?,"nth":…?}`` -> ``{"ok":true}``
- ``{"cmd":"click","role":…,"name":…,"nth":…?}``   -> ``{"ok":true}``
- ``{"cmd":"type","role":…,"name":…,"text":…,"nth":…?}`` -> ``{"ok":true}``
- ``{"cmd":"pointer","x":…,"y":…,"button":…?,"action":…?,"dy":…?}`` -> ``{"ok":true}``
- ``{"cmd":"key","combo":…?}`` or ``{"cmd":"key","text":…?}`` -> ``{"ok":true}``
- ``{"cmd":"ping"}`` / ``{"cmd":"stop"}``
Errors -> ``{"ok":false,"error":"…"}``. ``<node>`` = ``{role,name,extents:[x,y,w,h]|null,
children:[…]}``.

Pointer/key inject input through the compositor's wlroots virtual-input protocols:
a built-in raw-wire client (:class:`VirtualPointerClient`, holds one persistent
zwlr_virtual_pointer_v1) and ``wtype`` for the virtual keyboard. uinput-based tools
(ydotool) cannot work here: with ``WLR_BACKENDS=headless`` cage reads no input
devices at all. Coordinates are screenshot pixels — cage kiosk-fullscreens the app,
so screenshot, output and surface coordinates all coincide.
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

# Common aliases -> the role names GTK4 actually reports over AT-SPI.
_ROLE_ALIASES = {
    "push button": "button",
    "toggle-button": "toggle button",
    "text box": "text",
    "entry": "text",
}

_POINTER_BUTTONS = {"left", "right", "middle"}
_KEY_MODIFIERS = {"ctrl", "shift", "alt", "logo", "win", "altgr", "capslock"}


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


def find_nodes(node, role=None, name=None, out=None):
    """Depth-first search for ALL nodes matching role and/or name (DFS order)."""
    if out is None:
        out = []
    if role is not None:
        role = _ROLE_ALIASES.get(role, role)
    if (role is None or node.get_role_name() == role) and \
            (name is None or node.get_name() == name):
        out.append(node)
    for i in range(node.get_child_count()):
        child = node.get_child_at_index(i)
        if child is not None:
            find_nodes(child, role, name, out)
    return out


def find_node(node, role=None, name=None):
    """Depth-first search for the first node matching role and/or name."""
    hits = find_nodes(node, role, name)
    return hits[0] if hits else None


def _n_actions(node) -> int:
    n = getattr(node, "n_actions", None)  # duck-typed fakes in tests
    if n is not None:
        return n
    try:
        return Atspi.Action.get_n_actions(node)
    except Exception:  # noqa: BLE001 - nodes without the Action interface
        return 0


def pick_node(matches: list, nth: int = 0, require_action: bool = False):
    """Pick the nth match, preferring actionable nodes when require_action.

    Wrapper widgets (e.g. a MenuButton shell) often match by name but carry no
    AT-SPI actions — preferring actionable matches means click/do_action land
    on the node that can actually respond.
    """
    pool = matches
    if require_action:
        actionable = [m for m in matches if _n_actions(m) > 0]
        if actionable:
            pool = actionable
    if 0 <= nth < len(pool):
        return pool[nth]
    return None


# --------------------------------------------------------------------------- #
# Wayland wire helpers (pure, unit-tested).
# --------------------------------------------------------------------------- #
def _wl_msg(obj_id: int, opcode: int, payload: bytes = b"") -> bytes:
    """One Wayland wire message: object id, (size << 16) | opcode, payload."""
    import struct
    size = 8 + len(payload)
    return struct.pack("<II", obj_id, (size << 16) | opcode) + payload


def _wl_uint(value: int) -> bytes:
    import struct
    return struct.pack("<I", value & 0xFFFFFFFF)


def _wl_fixed(value: float) -> bytes:
    """Wayland fixed-point: signed 24.8."""
    import struct
    return struct.pack("<i", int(value * 256))


def _wl_string(text: str) -> bytes:
    """Length-prefixed NUL-terminated string, padded to 32 bits."""
    raw = text.encode() + b"\0"
    pad = (-len(raw)) % 4
    return _wl_uint(len(raw)) + raw + b"\0" * pad


_BTN_CODES = {"left": 0x110, "right": 0x111, "middle": 0x112}


class VirtualPointerClient:
    """Minimal raw-wire Wayland client holding ONE persistent virtual pointer.

    Why not ``wlrctl``: it creates a device, sends events and destroys it all in
    one short-lived process — the seat's pointer capability flaps per invocation,
    the GTK client never has a bound ``wl_pointer`` at event time, and every
    click is silently dropped. A persistent device keeps the capability (and the
    pointer focus that motion establishes) alive for the harness lifetime.
    Absolute positioning comes free via ``motion_absolute``.

    Wire ids: 1 = wl_display (fixed), 2 = wl_registry, 3 = wl_callback (sync),
    4 = wl_seat, 5 = zwlr_virtual_pointer_manager_v1, 6 = zwlr_virtual_pointer_v1.
    """

    def __init__(self, width: int, height: int):
        import struct
        self.width = width
        self.height = height
        run_dir = os.environ["XDG_RUNTIME_DIR"]
        display = os.environ["WAYLAND_DISPLAY"]
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.connect(os.path.join(run_dir, display))
        self._sock.settimeout(5)

        # wl_display.get_registry(new id 2) + wl_display.sync(new id 3)
        self._send(_wl_msg(1, 1, _wl_uint(2)) + _wl_msg(1, 0, _wl_uint(3)))
        globals_ = {}   # interface -> (name, version)
        done = False
        while not done:
            obj_id, opcode, payload = self._read_event()
            if obj_id == 1 and opcode == 0:  # wl_display.error
                raise RuntimeError(f"wayland error: {payload!r}")
            if obj_id == 2 and opcode == 0:  # wl_registry.global
                name = struct.unpack_from("<I", payload, 0)[0]
                slen = struct.unpack_from("<I", payload, 4)[0]
                iface = payload[8:8 + slen - 1].decode()
                version = struct.unpack_from(
                    "<I", payload, 8 + slen + ((-slen) % 4))[0]
                globals_[iface] = (name, version)
            if obj_id == 3 and opcode == 0:  # wl_callback.done (sync)
                done = True

        if "zwlr_virtual_pointer_manager_v1" not in globals_:
            raise RuntimeError("compositor lacks zwlr_virtual_pointer_manager_v1")
        if "wl_seat" not in globals_:
            raise RuntimeError("compositor lacks wl_seat")

        # wl_registry.bind(name, interface, version, new_id) for seat + manager,
        # then manager.create_virtual_pointer(seat, new id 6).
        def bind(iface: str, new_id: int, max_version: int) -> bytes:
            name, version = globals_[iface]
            version = min(version, max_version)
            return _wl_msg(2, 0, _wl_uint(name) + _wl_string(iface)
                           + _wl_uint(version) + _wl_uint(new_id))

        self._send(bind("wl_seat", 4, 1)
                   + bind("zwlr_virtual_pointer_manager_v1", 5, 1)
                   + _wl_msg(5, 0, _wl_uint(4) + _wl_uint(6)))
        # Give the app a beat to see the new pointer capability and bind.
        time.sleep(0.3)

    # -- wire I/O -------------------------------------------------------- #
    def _send(self, data: bytes) -> None:
        self._sock.sendall(data)

    def _read_event(self):
        import struct
        header = self._recv_exact(8)
        obj_id, size_op = struct.unpack("<II", header)
        size, opcode = size_op >> 16, size_op & 0xFFFF
        return obj_id, opcode, self._recv_exact(size - 8)

    def _recv_exact(self, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = self._sock.recv(n - len(buf))
            if not chunk:
                raise RuntimeError("wayland connection closed")
            buf += chunk
        return buf

    @staticmethod
    def _now() -> int:
        return int(time.monotonic() * 1000) & 0xFFFFFFFF

    # -- pointer events (id 6) ------------------------------------------- #
    def _frame(self) -> bytes:
        return _wl_msg(6, 4)

    def move(self, x: int, y: int) -> None:
        payload = (_wl_uint(self._now()) + _wl_uint(int(x)) + _wl_uint(int(y))
                   + _wl_uint(self.width) + _wl_uint(self.height))
        self._send(_wl_msg(6, 1, payload) + self._frame())

    def click(self, button: str, times: int = 1) -> None:
        code = _BTN_CODES[button]
        for _ in range(times):
            for state in (1, 0):
                payload = _wl_uint(self._now()) + _wl_uint(code) + _wl_uint(state)
                self._send(_wl_msg(6, 2, payload) + self._frame())
                time.sleep(0.05)

    def scroll(self, dy: float) -> None:
        payload = _wl_uint(self._now()) + _wl_uint(0) + _wl_fixed(dy * 15)
        self._send(_wl_msg(6, 3, payload) + self._frame())

    def close(self) -> None:
        try:
            self._send(_wl_msg(6, 8))  # destroy
            self._sock.close()
        except OSError:
            pass


def _key_argv(combo: str) -> list[str]:
    """wtype argv for a key combo like 'Return' or 'ctrl+shift+t'.

    Modifiers are pressed (-M) before and released (-m, reverse order) after the
    key, which itself is a press+release (-P/-p) of an xkb keysym name.
    """
    parts = [p for p in combo.strip().split("+") if p]
    if not parts:
        raise ValueError("empty key combo")
    *mods, key = parts
    argv = ["wtype"]
    for mod in mods:
        if mod.lower() not in _KEY_MODIFIERS:
            raise ValueError(f"unknown modifier: {mod!r}")
        argv += ["-M", mod.lower()]
    argv += ["-P", key, "-p", key]
    for mod in reversed(mods):
        argv += ["-m", mod.lower()]
    return argv


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
        self._vptr: VirtualPointerClient | None = None

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
        # GTK_USE_PORTAL=0: the harness bus has no activatable services
        # (see resources/dbus/harness-session.conf), so don't even try portals.
        env = dict(os.environ, GDK_BACKEND="wayland", GTK_USE_PORTAL="0")
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

    def _locate(self, role, name, nth=0, require_action=False):
        app = find_target_app(self._ensure_atspi())
        if app is None:
            return None, {"ok": False, "error": "target app not found in a11y tree"}
        matches = find_nodes(app, role, name)
        if not matches:
            return None, {"ok": False,
                          "error": f"no node matching role={role!r} name={name!r}"}
        node = pick_node(matches, nth=nth, require_action=require_action)
        if node is None:
            detail = "actionable " if require_action else ""
            return None, {"ok": False,
                          "error": f"no {detail}match #{nth} for role={role!r} "
                                   f"name={name!r} ({len(matches)} total match(es))"}
        return node, None

    def _do_action(self, role, name, action, nth=0) -> dict:
        node, err = self._locate(role, name, nth=nth, require_action=True)
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

    def _type(self, role, name, text, nth=0) -> dict:
        node, err = self._locate(role, name, nth=nth)
        if err:
            return err
        try:
            Atspi.EditableText.set_text_contents(node, text)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"cannot set text: {exc}"}
        return {"ok": True}

    def _run_input_cmds(self, cmds: list[list[str]], tool: str) -> dict:
        """Run a sequence of virtual-input commands, surfacing failures."""
        for argv in cmds:
            try:
                result = subprocess.run(argv, capture_output=True, timeout=10)
            except FileNotFoundError:
                return {"ok": False,
                        "error": f"{tool} not installed (install {tool} for "
                                 "coordinate/keyboard input)"}
            if result.returncode != 0:
                stderr = result.stderr.decode(errors="replace").strip()
                return {"ok": False, "error": f"{tool} failed: {stderr}"}
        return {"ok": True}

    def _ensure_pointer(self) -> "VirtualPointerClient":
        if self._vptr is None:
            self._vptr = VirtualPointerClient(self.width, self.height)
        return self._vptr

    def _pointer(self, x, y, button, action, dy) -> dict:
        button = button or "left"
        action = action or "click"
        if button not in _POINTER_BUTTONS:
            return {"ok": False, "error": f"unknown button: {button!r}"}
        if action not in ("click", "double", "move", "scroll"):
            return {"ok": False, "error": f"unknown pointer action: {action!r}"}
        try:
            ptr = self._ensure_pointer()
            ptr.move(int(x), int(y))
            time.sleep(0.1)  # let focus/enter settle before pressing
            if action in ("click", "double"):
                ptr.click(button, times=2 if action == "double" else 1)
            elif action == "scroll":
                ptr.scroll(float(dy or 0))
        except (OSError, RuntimeError, ValueError, TypeError) as exc:
            self._vptr = None  # reconnect on next call
            return {"ok": False, "error": f"virtual pointer: {exc}"}
        return {"ok": True}

    def _key(self, combo, text) -> dict:
        if text:
            return self._run_input_cmds([["wtype", str(text)]], "wtype")
        if combo:
            try:
                argv = _key_argv(combo)
            except ValueError as exc:
                return {"ok": False, "error": str(exc)}
            return self._run_input_cmds([argv], "wtype")
        return {"ok": False, "error": "key needs 'combo' or 'text'"}

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
                    request.get("role"), request.get("name"), request.get("action"),
                    nth=request.get("nth", 0),
                ), False
            if cmd == "click":
                return self._do_action(
                    request.get("role"), request.get("name"), None,
                    nth=request.get("nth", 0),
                ), False
            if cmd == "type":
                return self._type(
                    request.get("role"), request.get("name"), request.get("text", ""),
                    nth=request.get("nth", 0),
                ), False
            if cmd == "pointer":
                return self._pointer(
                    request.get("x", 0), request.get("y", 0), request.get("button"),
                    request.get("action"), request.get("dy"),
                ), False
            if cmd == "key":
                return self._key(request.get("combo"), request.get("text")), False
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
        if self._vptr is not None:
            self._vptr.close()
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
