#!/usr/bin/env python3
"""AT-SPI driver for the spike: find the target app in the accessibility tree,
read its widgets, then invoke the button via do_action() — NOT a synthetic
coordinate click. This is the channel that keeps working on Wayland.

Prints a small report to stdout so the spike runner can show what was found.
"""
import sys
import time

import gi

gi.require_version("Atspi", "2.0")
from gi.repository import Atspi  # noqa: E402

TARGET_BUTTON = "Click me"


def find_app(timeout=8.0):
    """Poll the desktop until an app containing our target button appears.

    GTK registers apps under the process name ("python3"), which is ambiguous,
    so we identify our app by its contents (the unique button) instead.
    """
    desktop = Atspi.get_desktop(0)
    deadline = time.time() + timeout
    while time.time() < deadline:
        for i in range(desktop.get_child_count()):
            app = desktop.get_child_at_index(i)
            if app is None:
                continue
            if find_by_role_name(app, "button", TARGET_BUTTON) is not None:
                return app
        time.sleep(0.3)
    return None


def walk(node, depth=0, out=None):
    """Collect (role, name, depth) for a compact tree dump."""
    if out is None:
        out = []
    try:
        role = node.get_role_name()
        name = node.get_name() or ""
    except Exception:
        return out
    out.append((depth, role, name))
    for i in range(node.get_child_count()):
        child = node.get_child_at_index(i)
        if child is not None:
            walk(child, depth + 1, out)
    return out


def find_by_role_name(node, role_name, name):
    if node.get_role_name() == role_name and (node.get_name() or "") == name:
        return node
    for i in range(node.get_child_count()):
        child = node.get_child_at_index(i)
        if child is not None:
            found = find_by_role_name(child, role_name, name)
            if found is not None:
                return found
    return None


def main():
    app = find_app()
    if app is None:
        print("ATSPI: FAIL — target app not found on a11y bus", flush=True)
        # Dump whatever apps ARE visible, to debug.
        desktop = Atspi.get_desktop(0)
        names = [desktop.get_child_at_index(i).get_name()
                 for i in range(desktop.get_child_count())]
        print(f"ATSPI: visible apps = {names}", flush=True)
        return 1

    print(f"ATSPI: found app '{app.get_name()}'", flush=True)

    tree = walk(app)
    print("ATSPI: widget tree ---------------------------------", flush=True)
    for depth, role, name in tree:
        label = f" '{name}'" if name else ""
        print(f"  {'  ' * depth}{role}{label}", flush=True)
    print("ATSPI: ---------------------------------------------", flush=True)

    # Fill the entry via EditableText (semantic, no keystrokes).
    entry = None
    for i in range(app.get_child_count()):
        entry = find_by_role_name(app.get_child_at_index(i), "text", "")
        if entry:
            break
    if entry is not None:
        try:
            # GI idiom: the Accessible implements the interface; call the
            # interface method with the accessible as the first argument.
            Atspi.EditableText.set_text_contents(entry, "typed via AT-SPI")
            print("ATSPI: set entry text via EditableText ✓", flush=True)
        except Exception as e:
            print(f"ATSPI: entry set_text failed: {e}", flush=True)

    # Find and invoke the button via the Action interface.
    button = find_by_role_name(app, "button", TARGET_BUTTON)
    if button is None:
        print(f"ATSPI: FAIL — button '{TARGET_BUTTON}' not found", flush=True)
        return 1

    n = Atspi.Action.get_n_actions(button)
    action_names = [Atspi.Action.get_action_name(button, i) for i in range(n)]
    print(f"ATSPI: button actions = {action_names}", flush=True)
    Atspi.Action.do_action(button, 0)  # invoke the widget's callback — no coordinates
    print(f"ATSPI: invoked do_action(0) on '{TARGET_BUTTON}' ✓", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
