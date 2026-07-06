"""The one background-work helper for the app (roadmap 2.1).

`run_async` replaces every ad-hoc `threading.Thread` + `GLib.idle_add` pair with a
single pattern that is correct by construction:

- the worker runs on a daemon thread, its body wrapped in try/except;
- the result is marshalled back to the GTK main thread via `GLib.idle_add`;
- a **generation token** per (widget, key) means only the newest call's result is
  applied — stale results from superseded calls are dropped;
- a **liveness guard** (`widget.get_root() is not None`) drops callbacks into a
  widget that has been removed from its window, so no GTK-CRITICAL on teardown;
- `on_error` defaults to a toast, so failures are never swallowed.

Usage:

    run_async(
        self,
        worker=lambda: self.service.pull(),          # runs off-thread
        on_done=lambda result: self._apply(result),  # runs on main thread, newest-wins
        on_error=lambda exc: self._show_error(exc),   # optional; defaults to a toast
        key="pull",                                   # one generation counter per key
    )
"""

from __future__ import annotations

import threading
from typing import Callable

from gi.repository import GLib

from .toast_service import ToastService

_GEN_ATTR_PREFIX = "_run_async_gen__"


def run_async(
    widget,
    worker: Callable[[], object],
    on_done: Callable[[object], None] | None = None,
    on_error: Callable[[Exception], None] | None = None,
    *,
    key: str = "default",
) -> None:
    """Run ``worker()`` off-thread and deliver the result on the main thread.

    Args:
        widget: The owning GTK widget. Used for the liveness guard and to hold the
            per-key generation counter.
        worker: Callable executed on a daemon thread. Its return value is passed to
            ``on_done``. Exceptions are caught and routed to ``on_error``.
        on_done: Called on the main thread with the worker's result, only if this is
            the newest call for ``key`` and the widget is still alive.
        on_error: Called on the main thread with the exception. Defaults to a toast.
        key: Names the generation counter, so independent operations on one widget
            (e.g. "pull" vs "status") don't cancel each other.
    """
    gen_attr = _GEN_ATTR_PREFIX + key
    generation = getattr(widget, gen_attr, 0) + 1
    setattr(widget, gen_attr, generation)

    def deliver(is_error: bool, payload: object) -> bool:
        # Liveness: the widget was removed from its window mid-flight.
        if widget.get_root() is None:
            return False
        # Generation: a newer call for this key superseded us.
        if getattr(widget, gen_attr, None) != generation:
            return False
        if is_error:
            if on_error is not None:
                on_error(payload)  # type: ignore[arg-type]
            else:
                ToastService.show_error(str(payload))
        elif on_done is not None:
            on_done(payload)
        return False

    def thread_body():
        try:
            result = worker()
        except Exception as exc:  # noqa: BLE001 - deliberately broad; surfaced via on_error
            GLib.idle_add(deliver, True, exc)
        else:
            GLib.idle_add(deliver, False, result)

    threading.Thread(target=thread_body, daemon=True).start()


def bump_generation(widget, key: str = "default") -> None:
    """Invalidate any in-flight ``run_async(key=...)`` result for ``widget``.

    Useful to cancel a pending refresh without starting a new one.
    """
    gen_attr = _GEN_ATTR_PREFIX + key
    setattr(widget, gen_attr, getattr(widget, gen_attr, 0) + 1)
