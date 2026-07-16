"""Touchpad scroll damping: pixels in, whole scroll steps out (vte#2720 workaround)."""

from src.utils.scroll_accumulator import ScrollAccumulator


def test_below_threshold_emits_nothing():
    acc = ScrollAccumulator(25)
    assert acc.feed(10.0) == 0
    assert acc.feed(14.0) == 0  # 24px banked, still short of 25


def test_remainder_carries_across_events():
    # The whole point: 25 one-pixel events are worth one 25px event. A real
    # gesture arrives as ~190 slivers (measured min dy = 0.121), so dropping the
    # remainder would silently eat most of the movement.
    acc = ScrollAccumulator(25)
    steps = sum(acc.feed(1.0) for _ in range(25))
    assert steps == 1


def test_exact_multiple_emits_all_steps_at_once():
    acc = ScrollAccumulator(25)
    assert acc.feed(75.0) == 3


def test_up_is_negative_down_is_positive():
    acc = ScrollAccumulator(10)
    assert acc.feed(30.0) == 3
    acc.reset()
    assert acc.feed(-30.0) == -3


def test_wiggle_cancels_instead_of_sawtoothing():
    # VTE converts total path, not net displacement, so a jittering finger emits
    # hundreds of clicks in both directions. Signed banking must cancel them.
    acc = ScrollAccumulator(25)
    emitted = 0
    for _ in range(50):
        emitted += acc.feed(+5.0)
        emitted += acc.feed(-5.0)
    assert emitted == 0


def test_no_drift_over_a_long_gesture():
    # 1212px of travel (a real measured gesture) at 25px/step must land on 48
    # steps, not 1519 as VTE does today.
    acc = ScrollAccumulator(25)
    emitted = sum(acc.feed(3.4) for _ in range(357))  # ~1213.8px in ~354 slivers
    assert emitted == int(357 * 3.4 / 25)
    assert emitted == 48


def test_divisor_one_reproduces_vte_behaviour():
    # The escape hatch: 1px per step is exactly what VTE does now, so a user who
    # wants the old behaviour can have it.
    acc = ScrollAccumulator(1)
    assert acc.feed(22.961) == 22  # truncates toward zero, remainder banked
    assert acc.feed(0.1) == 1      # ...and the banked 0.961 shows up next event


def test_retuning_drops_the_stale_remainder():
    acc = ScrollAccumulator(25)
    acc.feed(24.0)
    acc.set_pixels_per_click(10)
    assert acc.pixels_per_click == 10
    assert acc.feed(9.0) == 0  # the old 24px must not leak into the new scale


def test_bad_divisors_are_refused_not_obeyed():
    # A 0 would divide by zero, a negative would invert scrolling. Neither should
    # be reachable from the UI, but the accumulator must not be the thing that
    # crashes if one ever is.
    assert ScrollAccumulator(0).pixels_per_click == 1
    assert ScrollAccumulator(-5).pixels_per_click == 1
    assert ScrollAccumulator(None).pixels_per_click == 1
