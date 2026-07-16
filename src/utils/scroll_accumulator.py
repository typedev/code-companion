"""Turn touchpad pixel deltas into terminal scroll steps.

GDK4 tags scroll events with a unit: ``WHEEL`` (delta = notches, 1.0 each) or
``SURFACE`` (delta = pixels). VTE reads neither — it consumes the raw delta as a
wheel-detent count (``vte.cc``: ``cnt_y = m_mouse_smooth_scroll_y_delta``), so a
touchpad gets one wheel click per *pixel* of finger travel: ~1500 clicks for a
gesture a mouse would answer with 5. Upstream bug, open since 2024 and blocked on
choosing a divisor: https://gitlab.gnome.org/GNOME/vte/-/issues/2720

This accumulator does what VTE would if it read the unit: bank the pixels and emit
one step per ``pixels_per_click``, carrying the remainder so nothing is lost or
invented across a gesture. Deliberately free of ``gi`` imports — the caller owns
the GTK side, and this stays testable without a display.

Full analysis: docs/plan-touchpad-scroll.md
"""

from __future__ import annotations


class ScrollAccumulator:
    """Bank pixel deltas; hand out whole scroll steps.

    The accumulator is *signed*: scrolling back and forth cancels out rather than
    piling up, which is what makes a wiggling finger stop emitting a sawtooth.
    """

    def __init__(self, pixels_per_click: int) -> None:
        self._pixels_per_click = self._sanitize(pixels_per_click)
        self._banked = 0.0

    @staticmethod
    def _sanitize(pixels_per_click: int) -> int:
        # 1 reproduces VTE's raw behaviour; 0 or negative would divide by zero or
        # invert scrolling, so refuse them rather than trust the caller.
        try:
            value = int(pixels_per_click)
        except (TypeError, ValueError):
            return 1
        return max(1, value)

    @property
    def pixels_per_click(self) -> int:
        return self._pixels_per_click

    def set_pixels_per_click(self, pixels_per_click: int) -> None:
        """Retune live. Drops the banked remainder: it was measured against the
        old divisor, and carrying it over would make the first step after a
        settings change jump by a surprise amount."""
        self._pixels_per_click = self._sanitize(pixels_per_click)
        self._banked = 0.0

    def feed(self, dy_px: float) -> int:
        """Bank ``dy_px`` and return whole steps owed: + down, - up, 0 if short.

        Truncation is toward zero (as VTE's ``double -> gint`` is), and the
        remainder stays banked, so N calls of 1px are worth one call of N px.
        """
        self._banked += dy_px
        steps = int(self._banked / self._pixels_per_click)
        if steps:
            self._banked -= steps * self._pixels_per_click
        return steps

    def reset(self) -> None:
        """Forget the remainder — call between gestures, not within one."""
        self._banked = 0.0
