"""Supervisor port reservation: pick_stable_port determinism + reservation logic."""

import hashlib

from src.utils import claude_session as cs


def _base(name: str) -> int:
    span = cs.PORT_MAX - cs.PORT_MIN + 1
    return cs.PORT_MIN + int(hashlib.sha1(name.encode()).hexdigest(), 16) % span


def test_deterministic_and_in_nonephemeral_range():
    p1 = cs.pick_stable_port("cc-abc123", set(), lambda _p: True)
    p2 = cs.pick_stable_port("cc-abc123", set(), lambda _p: True)
    assert p1 == p2 == _base("cc-abc123")
    assert cs.PORT_MIN <= p1 <= cs.PORT_MAX
    assert p1 < 32768  # below the Linux ephemeral floor


def test_skips_reserved_port():
    name = "cc-def456"
    base = _base(name)
    port = cs.pick_stable_port(name, {base}, lambda _p: True)
    assert port != base
    assert cs.PORT_MIN <= port <= cs.PORT_MAX
    assert port not in {base}


def test_skips_os_busy_port():
    name = "cc-ghi789"
    base = _base(name)
    busy = {base, base + 1}
    port = cs.pick_stable_port(name, set(), lambda p: p not in busy)
    assert port not in busy
    assert cs.PORT_MIN <= port <= cs.PORT_MAX


def test_none_when_range_exhausted():
    everything = set(range(cs.PORT_MIN, cs.PORT_MAX + 1))
    assert cs.pick_stable_port("cc-x", everything, lambda _p: True) is None
    assert cs.pick_stable_port("cc-x", set(), lambda _p: False) is None


def test_reserved_and_busy_combine():
    name = "cc-combo"
    base = _base(name)
    reserved = {base}
    port = cs.pick_stable_port(name, reserved, lambda p: p != base + 1)
    # base is reserved, base+1 is busy -> first usable is base+2.
    assert port == base + 2
