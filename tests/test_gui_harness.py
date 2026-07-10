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
    def __init__(self, wait_timeouts=0):
        """wait_timeouts: how many wait() calls raise TimeoutExpired first."""
        self._alive = True
        self.pid = 4242
        self.terminated = False
        self.killed = False
        self.wait_calls = 0
        self._wait_timeouts = wait_timeouts

    def poll(self):
        return None if self._alive else 0

    def terminate(self):
        self.terminated = True
        self._alive = False

    def wait(self, timeout=None):
        self.wait_calls += 1
        if self.wait_calls <= self._wait_timeouts:
            raise gui_harness.subprocess.TimeoutExpired("proc", timeout)
        self._alive = False
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

    def fake_popen(argv, **kwargs):
        assert kwargs.get("start_new_session") is True  # killpg backstop needs it
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
    # minimal bus config: no activatable services -> no portal stack (issue #4)
    conf = argv[argv.index("--config-file") + 1]
    assert conf.endswith("resources/dbus/harness-session.conf")
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


def test_stop_tears_down_and_unregisters(stub_launch, monkeypatch):
    killpg_calls = []
    monkeypatch.setattr(gui_harness.os, "killpg", lambda *a: killpg_calls.append(a))
    mgr = GuiHarnessManager()
    handle = mgr.launch("sample")
    proc = stub_launch[0]

    mgr.stop(handle)
    assert handle not in mgr._harnesses
    # Graceful path: wait for the cascade (dbus-run-session reaps its daemon),
    # never signal the tree — that's what leaked session buses (issue #4).
    assert proc.wait_calls >= 1
    assert not proc.terminated and killpg_calls == []


def test_stop_all_clears_everything(stub_launch):
    mgr = GuiHarnessManager()
    mgr.launch("a")
    mgr.launch("b")
    assert len(mgr._harnesses) == 2

    mgr.stop_all()
    assert mgr._harnesses == {}
    assert all(p.wait_calls >= 1 for p in stub_launch)


def test_teardown_escalates_to_killpg_on_timeout(monkeypatch):
    import signal

    killpg_calls = []
    monkeypatch.setattr(gui_harness.os, "killpg", lambda *a: killpg_calls.append(a))
    proc = _FakeProc(wait_timeouts=2)  # stop-wait times out, TERM-wait times out
    harness = gui_harness._Harness("gui-1", proc, "/nonexistent", _FakeConn())

    harness.teardown(timeout=0.01)
    # TERM -> group-liveness probe (sig 0, group "alive" since the stub killpg
    # doesn't raise) -> unconditional KILL of the survivors.
    assert killpg_calls == [
        (4242, signal.SIGTERM), (4242, 0), (4242, signal.SIGKILL)
    ]


def test_kill_process_group_stops_after_vanished(monkeypatch):
    def raise_gone(*a):
        raise ProcessLookupError

    monkeypatch.setattr(gui_harness.os, "killpg", raise_gone)
    proc = _FakeProc(wait_timeouts=99)
    gui_harness._kill_process_group(proc)  # must not raise or loop
    assert proc.wait_calls == 0


def test_stop_unknown_handle_raises():
    mgr = GuiHarnessManager()
    with pytest.raises(GuiHarnessError, match="unknown handle"):
        mgr.stop("nope")


def test_launch_failure_kills_group_and_reraises(monkeypatch):
    import signal

    proc = _FakeProc()
    killpg_calls = []
    monkeypatch.setattr(gui_harness.os, "killpg", lambda *a: killpg_calls.append(a))
    monkeypatch.setattr(gui_harness.subprocess, "Popen", lambda argv, **kw: proc)

    def boom(self, socket_path, timeout):
        raise GuiHarnessError("socket never ready")

    monkeypatch.setattr(GuiHarnessManager, "_open_channel", boom)

    mgr = GuiHarnessManager()
    with pytest.raises(GuiHarnessError):
        mgr.launch("sample")
    assert killpg_calls[0] == (4242, signal.SIGTERM)  # whole tree cleaned up
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
    _key_argv, _wl_fixed, _wl_msg, _wl_string, _wl_uint, find_node, find_nodes,
    find_target_app, pick_node, serialize_tree,
)


class _FakeNode:
    def __init__(self, role, name, children=None, n_actions=0):
        self._role = role
        self._name = name
        self._children = children or []
        self.n_actions = n_actions

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


def test_find_nodes_returns_all_matches_dfs_order():
    inner = _FakeNode("button", "Save", n_actions=1)
    wrapper = _FakeNode("button", "Save", [inner])  # wrapper first in DFS
    app = _FakeNode("application", "python3", [wrapper])
    assert find_nodes(app, role="button", name="Save") == [wrapper, inner]


def test_find_nodes_role_alias():
    app = _sample_app_tree()
    assert find_nodes(app, role="push button", name="Click Me")  # alias -> button


def test_pick_node_prefers_actionable():
    inner = _FakeNode("button", "Save", n_actions=1)
    wrapper = _FakeNode("button", "Save", [inner])  # matches first, no actions
    matches = [wrapper, inner]
    assert pick_node(matches, require_action=True) is inner
    assert pick_node(matches) is wrapper  # without the filter, DFS order wins


def test_pick_node_nth():
    a = _FakeNode("button", "Row", n_actions=1)
    b = _FakeNode("button", "Row", n_actions=1)
    assert pick_node([a, b], nth=1, require_action=True) is b
    assert pick_node([a, b], nth=5) is None


def test_pick_node_no_actionable_falls_back_to_all():
    plain = _FakeNode("label", "Save")
    assert pick_node([plain], require_action=True) is plain


# --------------------------------------------------------------------------- #
# Virtual-input building blocks — src/services/gui_agent.py
# --------------------------------------------------------------------------- #
def test_wl_msg_header_packs_size_and_opcode():
    msg = _wl_msg(6, 4)  # frame: no payload
    assert msg == b"\x06\x00\x00\x00" + (8 << 16 | 4).to_bytes(4, "little")
    with_payload = _wl_msg(1, 1, _wl_uint(2))
    assert len(with_payload) == 12
    assert int.from_bytes(with_payload[4:8], "little") == (12 << 16) | 1


def test_wl_string_nul_terminated_and_padded():
    packed = _wl_string("wl_seat")
    # length prefix counts the NUL; total payload padded to 32 bits
    assert int.from_bytes(packed[:4], "little") == 8
    assert packed[4:12] == b"wl_seat\0"
    assert len(packed) % 4 == 0


def test_wl_fixed_is_24_8():
    assert _wl_fixed(1.0) == (256).to_bytes(4, "little", signed=True)
    assert _wl_fixed(-2.5) == (-640).to_bytes(4, "little", signed=True)


def test_key_argv_plain_and_modifiers():
    assert _key_argv("Return") == ["wtype", "-P", "Return", "-p", "Return"]
    assert _key_argv("ctrl+shift+t") == [
        "wtype", "-M", "ctrl", "-M", "shift",
        "-P", "t", "-p", "t",
        "-m", "shift", "-m", "ctrl",
    ]


def test_key_argv_rejects_bad_combo():
    with pytest.raises(ValueError):
        _key_argv("")
    with pytest.raises(ValueError):
        _key_argv("hyper+x")


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
    assert harness.sent == [
        {"cmd": "click", "role": "button", "name": "Click Me", "nth": 0}
    ]


def test_click_error_raises():
    mgr, _ = _mgr_with({"ok": False, "error": "no node matching"})
    with pytest.raises(GuiHarnessError, match="no node matching"):
        mgr.click("gui-1", role="button", name="X")


def test_type_text_sends_command():
    mgr, harness = _mgr_with({"ok": True})
    mgr.type_text("gui-1", role="text", name="field", text="hello")
    assert harness.sent == [
        {"cmd": "type", "role": "text", "name": "field", "text": "hello", "nth": 0}
    ]


def test_do_action_sends_command():
    mgr, harness = _mgr_with({"ok": True})
    mgr.do_action("gui-1", role="button", name="Save", action="click", nth=1)
    assert harness.sent == [
        {"cmd": "do_action", "role": "button", "name": "Save", "action": "click",
         "nth": 1}
    ]


def test_pointer_sends_command():
    mgr, harness = _mgr_with({"ok": True})
    mgr.pointer("gui-1", 120, 340, button="right")
    assert harness.sent == [
        {"cmd": "pointer", "x": 120, "y": 340, "button": "right",
         "action": "click", "dy": 0}
    ]


def test_key_sends_command():
    mgr, harness = _mgr_with({"ok": True})
    mgr.key("gui-1", combo="ctrl+Return")
    mgr.key("gui-1", text="hello")
    assert harness.sent == [
        {"cmd": "key", "combo": "ctrl+Return", "text": None},
        {"cmd": "key", "combo": None, "text": "hello"},
    ]
