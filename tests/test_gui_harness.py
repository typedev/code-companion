"""Logic-level tests for the GUI harness manager (Part B increment 1).

No cage/display here — the subprocess launch and socket connect are stubbed. The real
end-to-end (launch a GTK4 app in cage, screenshot, stop) lives in the scratchpad harness
since it needs a compositor.
"""
import base64
import sys

import pytest

from src.services import gui_harness
from src.services.gui_harness import GuiHarnessError, GuiHarnessManager


class _FakeProc:
    def __init__(self):
        self._alive = True
        self.terminated = False
        self.killed = False

    def poll(self):
        return None if self._alive else 0

    def terminate(self):
        self.terminated = True
        self._alive = False

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self.killed = True
        self._alive = False


class _FakeConn:
    def __init__(self, replies=None):
        self._replies = list(replies or [b'{"ok": true}\n'])
        self.written = []
        self.closed = False

    def write(self, data):
        self.written.append(data)

    def flush(self):
        pass

    def readline(self):
        return self._replies.pop(0) if self._replies else b""

    def close(self):
        self.closed = True


@pytest.fixture
def stub_launch(monkeypatch):
    """Stub Popen + _open_channel so launch() never spawns a real compositor."""
    procs = []

    def fake_popen(argv):
        proc = _FakeProc()
        procs.append(proc)
        return proc

    monkeypatch.setattr(gui_harness.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        GuiHarnessManager, "_open_channel",
        lambda self, socket_path, timeout: _FakeConn(),
    )
    return procs


# -- argv (pure) ----------------------------------------------------------- #
def test_build_launch_argv():
    mgr = GuiHarnessManager()
    argv = mgr._build_launch_argv("/tmp/x.sock", "myapp --flag", 800, 600)

    assert argv[0] == "dbus-run-session"
    assert "cage" in argv
    assert sys.executable in argv
    # env vars for the wlroots headless backend
    assert "WLR_BACKENDS=headless" in argv
    assert "WLR_HEADLESS_OUTPUTS=1" in argv
    # agent flags, each value right after its flag
    assert argv[argv.index("--socket") + 1] == "/tmp/x.sock"
    assert argv[argv.index("--cmd") + 1] == "myapp --flag"
    assert argv[argv.index("--width") + 1] == "800"
    assert argv[argv.index("--height") + 1] == "600"


# -- lifecycle ------------------------------------------------------------- #
def test_launch_registers_handle(stub_launch):
    mgr = GuiHarnessManager()
    handle = mgr.launch("sample")
    assert handle == "gui-1"
    assert handle in mgr._harnesses
    # second launch gets a fresh handle
    assert mgr.launch("sample2") == "gui-2"


def test_stop_tears_down_and_unregisters(stub_launch):
    mgr = GuiHarnessManager()
    handle = mgr.launch("sample")
    proc = stub_launch[0]

    mgr.stop(handle)
    assert handle not in mgr._harnesses
    assert proc.terminated  # root PID killed -> tree cascades


def test_stop_all_clears_everything(stub_launch):
    mgr = GuiHarnessManager()
    mgr.launch("a")
    mgr.launch("b")
    assert len(mgr._harnesses) == 2

    mgr.stop_all()
    assert mgr._harnesses == {}
    assert all(p.terminated for p in stub_launch)


def test_stop_unknown_handle_raises():
    mgr = GuiHarnessManager()
    with pytest.raises(GuiHarnessError, match="unknown handle"):
        mgr.stop("nope")


def test_launch_failure_kills_proc_and_reraises(monkeypatch):
    proc = _FakeProc()
    monkeypatch.setattr(gui_harness.subprocess, "Popen", lambda argv: proc)

    def boom(self, socket_path, timeout):
        raise GuiHarnessError("socket never ready")

    monkeypatch.setattr(GuiHarnessManager, "_open_channel", boom)

    mgr = GuiHarnessManager()
    with pytest.raises(GuiHarnessError):
        mgr.launch("sample")
    assert proc.terminated          # partial tree cleaned up
    assert mgr._harnesses == {}     # nothing registered


# -- screenshot ------------------------------------------------------------ #
def test_screenshot_decodes_png():
    mgr = GuiHarnessManager()
    png = b"\x89PNG\r\n\x1a\n fake bytes"

    class _FakeHarness:
        def command(self, payload, timeout=20):
            return {"ok": True, "png_b64": base64.b64encode(png).decode()}

    mgr._harnesses["gui-1"] = _FakeHarness()
    assert mgr.screenshot("gui-1") == png


def test_screenshot_agent_error_raises():
    mgr = GuiHarnessManager()

    class _FakeHarness:
        def command(self, payload, timeout=20):
            return {"ok": False, "error": "grim failed"}

    mgr._harnesses["gui-1"] = _FakeHarness()
    with pytest.raises(GuiHarnessError, match="grim failed"):
        mgr.screenshot("gui-1")


def test_screenshot_unknown_handle_raises():
    mgr = GuiHarnessManager()
    with pytest.raises(GuiHarnessError, match="unknown handle"):
        mgr.screenshot("nope")


# --------------------------------------------------------------------------- #
# AT-SPI tree helpers (fake nodes) — src/services/gui_agent.py
# --------------------------------------------------------------------------- #
from src.services.gui_agent import (  # noqa: E402
    find_node, find_target_app, serialize_tree,
)


class _FakeNode:
    def __init__(self, role, name, children=None):
        self._role = role
        self._name = name
        self._children = children or []

    def get_role_name(self):
        return self._role

    def get_name(self):
        return self._name

    def get_child_count(self):
        return len(self._children)

    def get_child_at_index(self, i):
        return self._children[i]


def _sample_app_tree():
    button = _FakeNode("button", "Click Me", [_FakeNode("label", "Click Me")])
    frame = _FakeNode("frame", "", [_FakeNode("label", "HELLO"), button])
    return _FakeNode("application", "python3", [frame])


def test_serialize_tree_structure():
    tree = serialize_tree(_sample_app_tree())
    assert tree["role"] == "application"
    assert tree["name"] == "python3"
    assert tree["extents"] is None  # fake nodes have no Component interface
    frame = tree["children"][0]
    assert frame["role"] == "frame"
    button = frame["children"][1]
    assert button["role"] == "button"
    assert button["name"] == "Click Me"
    assert button["children"][0]["role"] == "label"


def test_find_node_by_role_and_name():
    app = _sample_app_tree()
    node = find_node(app, role="button", name="Click Me")
    assert node is not None and node.get_name() == "Click Me"


def test_find_node_by_name_only():
    app = _sample_app_tree()
    assert find_node(app, name="HELLO").get_role_name() == "label"


def test_find_node_absent_returns_none():
    assert find_node(_sample_app_tree(), role="button", name="Nope") is None


def test_find_target_app_skips_infra():
    portal = _FakeNode("application", "xdg-desktop-portal-gtk")
    app = _FakeNode("application", "python3")
    desktop = _FakeNode("desktop frame", "main", [portal, app])
    assert find_target_app(desktop) is app


def test_find_target_app_none_when_only_infra():
    desktop = _FakeNode("desktop frame", "main",
                        [_FakeNode("application", "xdg-desktop-portal-gtk")])
    assert find_target_app(desktop) is None


# --------------------------------------------------------------------------- #
# Manager semantic passthroughs (stubbed connection)
# --------------------------------------------------------------------------- #
class _FakeCmdHarness:
    def __init__(self, reply):
        self._reply = reply
        self.sent = []

    def command(self, payload, timeout=20):
        self.sent.append(payload)
        return self._reply


def _mgr_with(reply):
    mgr = GuiHarnessManager()
    harness = _FakeCmdHarness(reply)
    mgr._harnesses["gui-1"] = harness
    return mgr, harness


def test_snapshot_tree_returns_tree():
    mgr, _ = _mgr_with({"ok": True, "tree": {"role": "application"}})
    assert mgr.snapshot_tree("gui-1") == {"role": "application"}


def test_snapshot_tree_error_raises():
    mgr, _ = _mgr_with({"ok": False, "error": "app not found"})
    with pytest.raises(GuiHarnessError, match="app not found"):
        mgr.snapshot_tree("gui-1")


def test_click_sends_command():
    mgr, harness = _mgr_with({"ok": True})
    mgr.click("gui-1", role="button", name="Click Me")
    assert harness.sent == [{"cmd": "click", "role": "button", "name": "Click Me"}]


def test_click_error_raises():
    mgr, _ = _mgr_with({"ok": False, "error": "no node matching"})
    with pytest.raises(GuiHarnessError, match="no node matching"):
        mgr.click("gui-1", role="button", name="X")


def test_type_text_sends_command():
    mgr, harness = _mgr_with({"ok": True})
    mgr.type_text("gui-1", role="text", name="field", text="hello")
    assert harness.sent == [
        {"cmd": "type", "role": "text", "name": "field", "text": "hello"}
    ]


def test_do_action_sends_command():
    mgr, harness = _mgr_with({"ok": True})
    mgr.do_action("gui-1", role="button", name="Save", action="click")
    assert harness.sent == [
        {"cmd": "do_action", "role": "button", "name": "Save", "action": "click"}
    ]
