"""Image viewer widget with zoom controls and pixel-perfect rendering."""

import io
import math
import os
from pathlib import Path

import cairo as cairocffi

import gi

gi.require_version("GdkPixbuf", "2.0")

from gi.repository import Gtk, Gdk, GdkPixbuf, Gio, GLib, GObject, Graphene


# Checkerboard CSS for transparency visualization
CHECKERBOARD_CSS = """
.checkerboard {
    background-color: #cccccc;
    background-image:
        linear-gradient(45deg, #999 25%, transparent 25%, transparent 75%, #999 75%),
        linear-gradient(45deg, #999 25%, transparent 25%, transparent 75%, #999 75%);
    background-size: 16px 16px;
    background-position: 0 0, 8px 8px;
}
"""


def _format_file_size(size_bytes: int) -> str:
    """Format file size in human-readable form."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    else:
        return f"{size_bytes / (1024 * 1024):.1f} MB"


def _pixbuf_to_cairo_surface(pixbuf: GdkPixbuf.Pixbuf) -> cairocffi.ImageSurface:
    """Convert GdkPixbuf to Cairo ImageSurface via PNG round-trip."""
    success, png_data = pixbuf.save_to_bufferv("png", [], [])
    if not success:
        return None
    return cairocffi.ImageSurface.create_from_png(io.BytesIO(bytes(png_data)))


def _get_display_scale(widget: Gtk.Widget) -> float:
    """Get fractional display scale factor."""
    native = widget.get_native()
    if native:
        surface = native.get_surface()
        if surface and hasattr(surface, "get_scale"):
            return surface.get_scale()
    return float(widget.get_scale_factor() or 1)


class PixelImage(Gtk.Widget):
    """Custom widget that renders an image with pixel-perfect nearest-neighbor scaling.

    Uses Cairo rendering via snapshot.append_cairo() with FILTER_NEAREST,
    drawing directly in device-pixel coordinates. This bypasses GSK's
    texture scaling pipeline entirely, guaranteeing crisp pixel edges
    at any zoom level and fractional display scale.
    """

    __gtype_name__ = "PixelImage"

    def __init__(self):
        super().__init__()
        self._surface = None  # Cairo ImageSurface (original resolution)
        self._fit_mode = True
        self._zoom = 1.0
        self._css_width = 0.0
        self._css_height = 0.0

    def set_surface(self, surface: cairocffi.ImageSurface):
        """Set the Cairo surface to display (original resolution)."""
        self._surface = surface
        self.queue_draw()

    def set_zoom(self, zoom: float, css_w: float, css_h: float):
        """Set zoom level and CSS pixel dimensions for zoom mode."""
        self._fit_mode = False
        self._zoom = zoom
        self._css_width = css_w
        self._css_height = css_h
        self.set_size_request(math.ceil(css_w), math.ceil(css_h))
        self.queue_draw()

    def set_fit_mode(self):
        """Switch to fit mode (expand to fill parent, maintain aspect ratio)."""
        self._fit_mode = True
        self.set_size_request(-1, -1)
        self.queue_resize()

    def do_snapshot(self, snapshot):
        if not self._surface:
            return
        w = self.get_width()
        h = self.get_height()
        if w <= 0 or h <= 0:
            return

        bounds = Graphene.Rect().init(0, 0, w, h)
        cr = snapshot.append_cairo(bounds)

        # Extract device scale from Cairo's current transformation matrix
        matrix = cr.get_matrix()
        dev_sx = matrix.xx
        dev_sy = matrix.yy

        # Switch to device-pixel coordinates for pixel-perfect control
        cr.identity_matrix()

        dev_w = w * dev_sx
        dev_h = h * dev_sy
        src_w = self._surface.get_width()
        src_h = self._surface.get_height()

        if self._fit_mode:
            # Scale to fit, maintaining aspect ratio, centered
            if src_w <= 0 or src_h <= 0:
                return
            s = min(dev_w / src_w, dev_h / src_h)
            ox = (dev_w - src_w * s) / 2
            oy = (dev_h - src_h * s) / 2
            cr.translate(ox, oy)
            cr.scale(s, s)
        else:
            # Zoom mode: 1 source pixel = self._zoom device pixels
            cr.scale(self._zoom, self._zoom)

        cr.set_source_surface(self._surface, 0, 0)
        pattern = cr.get_source()
        pattern.set_filter(cairocffi.FILTER_NEAREST)
        cr.paint()

    def do_measure(self, orientation, for_size):
        if self._fit_mode:
            if self._surface:
                if orientation == Gtk.Orientation.HORIZONTAL:
                    return (0, self._surface.get_width(), -1, -1)
                return (0, self._surface.get_height(), -1, -1)
            return (0, 0, -1, -1)
        s = math.ceil(
            self._css_width
            if orientation == Gtk.Orientation.HORIZONTAL
            else self._css_height
        )
        return (s, s, -1, -1)


class ImageViewer(Gtk.Box):
    """A widget for viewing raster images with zoom controls."""

    # Supported raster image extensions
    EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}

    def __init__(self, file_path: str):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.set_vexpand(True)
        self.set_hexpand(True)

        self.file_path = file_path
        self._pixbuf = None
        self._cairo_surface = None
        self._img_width = 0
        self._img_height = 0
        self._zoom = 1.0
        self._fit_mode = True

        self._setup_css()
        self._build_ui()
        self._load_image()

    def _setup_css(self):
        """Setup checkerboard CSS."""
        css_provider = Gtk.CssProvider()
        css_provider.load_from_string(CHECKERBOARD_CSS)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

    def _build_ui(self):
        """Build the viewer UI."""
        # Toolbar
        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        toolbar.set_margin_start(8)
        toolbar.set_margin_end(8)
        toolbar.set_margin_top(4)
        toolbar.set_margin_bottom(4)
        toolbar.add_css_class("toolbar")

        # Filename label
        filename = Path(self.file_path).name
        name_label = Gtk.Label(label=filename)
        name_label.add_css_class("dim-label")
        toolbar.append(name_label)

        # Info label (dimensions + size)
        self._info_label = Gtk.Label(label="")
        self._info_label.add_css_class("dim-label")
        self._info_label.set_margin_start(8)
        toolbar.append(self._info_label)

        # Spacer
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        toolbar.append(spacer)

        # Zoom controls
        fit_btn = Gtk.Button(label="Fit")
        fit_btn.add_css_class("flat")
        fit_btn.set_tooltip_text("Fit to window (0)")
        fit_btn.connect("clicked", lambda b: self._set_fit_mode())
        toolbar.append(fit_btn)

        one_btn = Gtk.Button(label="1:1")
        one_btn.add_css_class("flat")
        one_btn.set_tooltip_text("Original size (1)")
        one_btn.connect("clicked", lambda b: self._set_zoom(1.0))
        toolbar.append(one_btn)

        zoom_out_btn = Gtk.Button()
        zoom_out_btn.set_icon_name("zoom-out-symbolic")
        zoom_out_btn.add_css_class("flat")
        zoom_out_btn.set_tooltip_text("Zoom out (-)")
        zoom_out_btn.connect("clicked", lambda b: self._zoom_step(-1))
        toolbar.append(zoom_out_btn)

        zoom_in_btn = Gtk.Button()
        zoom_in_btn.set_icon_name("zoom-in-symbolic")
        zoom_in_btn.add_css_class("flat")
        zoom_in_btn.set_tooltip_text("Zoom in (+)")
        zoom_in_btn.connect("clicked", lambda b: self._zoom_step(1))
        toolbar.append(zoom_in_btn)

        # Zoom percentage label
        self._zoom_label = Gtk.Label(label="Fit")
        self._zoom_label.set_width_chars(6)
        toolbar.append(self._zoom_label)

        self.append(toolbar)

        # Scrolled window for the image
        self._scrolled = Gtk.ScrolledWindow()
        self._scrolled.set_vexpand(True)
        self._scrolled.set_hexpand(True)

        # Image container with checkerboard
        self._image_box = Gtk.Box()
        self._image_box.add_css_class("checkerboard")
        self._image_box.set_halign(Gtk.Align.CENTER)
        self._image_box.set_valign(Gtk.Align.CENTER)

        # Custom pixel-perfect image widget
        self._picture = PixelImage()
        self._picture.set_hexpand(True)
        self._picture.set_vexpand(True)
        self._image_box.append(self._picture)

        self._scrolled.set_child(self._image_box)
        self.append(self._scrolled)

        # Start in fit mode
        self._apply_fit_mode()

        # Ctrl+scroll zoom
        scroll_controller = Gtk.EventControllerScroll.new(
            Gtk.EventControllerScrollFlags.VERTICAL
        )
        scroll_controller.connect("scroll", self._on_scroll)
        self._scrolled.add_controller(scroll_controller)

        # Keyboard shortcuts
        key_controller = Gtk.EventControllerKey()
        key_controller.connect("key-pressed", self._on_key_pressed)
        self.add_controller(key_controller)

    def _load_image(self):
        """Load the image file."""
        try:
            self._pixbuf = GdkPixbuf.Pixbuf.new_from_file(self.file_path)
            self._img_width = self._pixbuf.get_width()
            self._img_height = self._pixbuf.get_height()
            self._cairo_surface = _pixbuf_to_cairo_surface(self._pixbuf)
            self._picture.set_surface(self._cairo_surface)

            # Update info label
            file_size = os.path.getsize(self.file_path)
            self._info_label.set_text(
                f"{self._img_width}\u00d7{self._img_height} \u00b7 {_format_file_size(file_size)}"
            )
        except Exception as e:
            self._info_label.set_text(f"Error: {e}")

    def _set_fit_mode(self):
        """Switch to fit-to-window mode."""
        self._fit_mode = True
        self._apply_fit_mode()
        self._zoom_label.set_text("Fit")

    def _apply_fit_mode(self):
        """Apply fit mode settings."""
        if self._cairo_surface:
            self._picture.set_surface(self._cairo_surface)
        self._picture.set_fit_mode()
        self._picture.set_hexpand(True)
        self._picture.set_vexpand(True)
        self._image_box.set_halign(Gtk.Align.FILL)
        self._image_box.set_valign(Gtk.Align.FILL)
        self._image_box.set_hexpand(True)
        self._image_box.set_vexpand(True)
        self._scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.NEVER)

    def _set_zoom(self, zoom: float):
        """Set a specific zoom level."""
        zoom = max(0.10, min(10.0, zoom))
        self._zoom = zoom
        self._fit_mode = False
        self._apply_zoom()
        self._zoom_label.set_text(f"{int(zoom * 100)}%")

    def _apply_zoom(self):
        """Apply current zoom level with pixel-perfect Cairo rendering."""
        if not self._cairo_surface:
            return
        scale = _get_display_scale(self)

        # Target size in device pixels
        dev_w = round(self._img_width * self._zoom)
        dev_h = round(self._img_height * self._zoom)

        # Exact CSS pixel dimensions
        css_w = dev_w / scale
        css_h = dev_h / scale

        self._picture.set_zoom(self._zoom, css_w, css_h)
        self._picture.set_hexpand(False)
        self._picture.set_vexpand(False)
        self._image_box.set_halign(Gtk.Align.CENTER)
        self._image_box.set_valign(Gtk.Align.CENTER)
        self._image_box.set_hexpand(False)
        self._image_box.set_vexpand(False)
        self._scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)

    def _zoom_step(self, direction: int):
        """Zoom in or out by a step."""
        if self._fit_mode:
            self._zoom = 1.0
        step = 0.25
        new_zoom = self._zoom + direction * step
        self._set_zoom(new_zoom)

    def _on_scroll(self, controller, dx, dy):
        """Handle scroll events for Ctrl+wheel zoom."""
        state = controller.get_current_event_state()
        if state & Gdk.ModifierType.CONTROL_MASK:
            if self._fit_mode:
                self._zoom = 1.0
            direction = -1 if dy > 0 else 1
            step = 0.10
            self._set_zoom(self._zoom + direction * step)
            return True
        return False

    def _on_key_pressed(self, controller, keyval, keycode, state):
        """Handle keyboard shortcuts."""
        if keyval == Gdk.KEY_0:
            self._set_fit_mode()
            return True
        elif keyval == Gdk.KEY_1:
            self._set_zoom(1.0)
            return True
        elif keyval in (Gdk.KEY_plus, Gdk.KEY_equal, Gdk.KEY_KP_Add):
            self._zoom_step(1)
            return True
        elif keyval in (Gdk.KEY_minus, Gdk.KEY_KP_Subtract):
            self._zoom_step(-1)
            return True
        return False

    def grab_focus(self):
        """Focus the scrolled window for keyboard events."""
        self._scrolled.grab_focus()
