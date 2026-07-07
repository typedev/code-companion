#!/usr/bin/env python3
"""Minimal GTK4/libadwaita app used as the target-under-test for the GUI spike.

It renders a window with a header bar, a label, a couple of buttons and an entry
so the screenshot has enough visual structure to judge that the whole chain
(headless compositor -> render -> grim capture) actually worked.
"""
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402


class DemoWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, default_width=520, default_height=340)
        self.set_title("GUI Spike Target")

        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar()
        toolbar.add_top_bar(header)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        box.set_margin_top(32)
        box.set_margin_bottom(32)
        box.set_margin_start(32)
        box.set_margin_end(32)

        title = Gtk.Label()
        title.set_markup("<span size='xx-large' weight='bold'>Hello from headless cage</span>")
        box.append(title)

        self.status = Gtk.Label(label="Button not clicked yet")
        self.status.add_css_class("dim-label")
        box.append(self.status)

        entry = Gtk.Entry()
        entry.set_placeholder_text("Type here (AT-SPI target)")
        box.append(entry)

        btn = Gtk.Button(label="Click me")
        btn.add_css_class("suggested-action")
        btn.set_halign(Gtk.Align.CENTER)
        btn.connect("clicked", self._on_click)
        box.append(btn)

        toolbar.set_content(box)
        self.set_content(toolbar)

    def _on_click(self, _btn):
        self.status.set_text("Button WAS clicked ✓")


class DemoApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id="dev.typedev.GuiSpikeTarget")

    def do_activate(self):
        DemoWindow(self).present()


if __name__ == "__main__":
    DemoApp().run()
