import ctypes
import ctypes.wintypes
import math
import sys
import threading
import tkinter as tk
from tkinter import ttk, messagebox
from PIL import Image, ImageDraw, ImageFont
import pystray
from pystray import MenuItem as item

from wtd.config import (DEVICE_SECTION_KEYS, TOUCH_ACTION_CHOICES, WIRELESS_RECEIVER_PRODUCT_ID, WIRELESS_TABLET_SIZES, resolved_pen_mode, snapshot_device_sections, union_rect_from_records, PEN_MODES)
from wtd.mapping import (display_key_for_records, monitor_records, pen_area_rect_aspect_lock, pen_mm_per_px, pen_rotation_center, reset_pen_area_key_cache, rotate_point, rotated_pen_box_mm, rotated_pen_extents, select_pen_area_entry, virtual_desktop_dpi, virtual_desktop_metrics)
from wtd.protocol import (pen_specs_for_product, PEN_SPECS_BY_PRODUCT)
from wtd._log import logger as _log
from wtd import config

# Wireless tablet size GUI lane (U386 / TODO #385). The combo offers the
# explicit unset label plus the tablet-size vocabulary; the notice text is
# the loud GUI face of the planner ruling (unset + non-detecting receiver
# id 132 = never a silent default). WIRELESS_SIZE_NOTICE_TEXT is pinned
# byte-exact by tests/test_wireless_tablet_size.py.
WIRELESS_SIZE_UNSET_LABEL = "(unset)"
WIRELESS_SIZE_NOTICE_TEXT = (
    "Wireless tablet size NOT SET: the receiver (id 132) cannot detect its "
    "paired tablet. Choose small (788) / medium (789) / large (791) below - "
    "until chosen the driver maps the MEDIUM extents (789). A size change "
    "applies at the next device reconnect."
)
_KEEP_WIRELESS_SIZE = object()  # apply_settings_to_config sentinel: leave the attr


class ToolTip:
    """Hover tooltip for Tkinter widgets with robust cleanup on leave, click, scroll, and window move."""
    def __init__(self, widget, text):
        self.widget = widget
        self.text = text
        self.tip_window = None
        
        # Event bindings on the target widget
        self.widget.bind("<Enter>", self.show_tip)
        self.widget.bind("<Leave>", self.hide_tip)
        self.widget.bind("<ButtonPress>", self.hide_tip)

        # Bind to top-level window move/resize/scroll to close tooltips on window movement
        try:
            top_level = self.widget.winfo_toplevel()
            top_level.bind("<Configure>", self.hide_tip, add="+")
            top_level.bind("<Unmap>", self.hide_tip, add="+")
        except Exception:
            pass

    def show_tip(self, event=None):
        if self.tip_window or not self.text:
            return
        
        # Position below the widget dynamically based on current screen position
        x = self.widget.winfo_rootx() + 15
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 5

        self.tip_window = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        tw.attributes("-topmost", True)

        label = tk.Label(
            tw, text=self.text, justify="left",
            background="#ffffe0", foreground="#000000",
            relief="solid", borderwidth=1,
            font=("Segoe UI", 8, "normal"), padx=6, pady=4
        )
        label.pack(ipadx=1)

    def hide_tip(self, event=None):
        if self.tip_window:
            try:
                self.tip_window.destroy()
            except Exception:
                pass
            self.tip_window = None


def generate_icon_variant(enabled: bool, text: str = "WT", icon_size: int = 64) -> Image.Image:
    border = 2
    inner_size = icon_size - (2 * border)

    image = Image.new("RGBA", (icon_size, icon_size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    color = "#168b47" if enabled else "#9a2619"
    border_color = "#0d7739" if enabled else "#73160c"

    draw.ellipse([border, border, border + inner_size, border + inner_size], fill=color, outline=border_color, width=2)

    font_size = int(inner_size * 0.65)
    font = None
    for font_name in ["arial.ttf", "arialbd.ttf", "DejaVuSans.ttf"]:
        try:
            font = ImageFont.truetype(font_name, font_size)
            break
        except OSError:
            continue

    if font is None:
        font = ImageFont.load_default(size=font_size)

    bbox = draw.textbbox((0, 0), text, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]

    text_x = (icon_size - text_w) // 2 - bbox[0]
    text_y = (icon_size - text_h) // 2 - bbox[1]

    draw.text((text_x, text_y), text, fill="white", font=font)

    return image


class IconCache:
    def __init__(self, text: str = "WT"):
        self.icons = {
            True: generate_icon_variant(enabled=True, text=text),
            False: generate_icon_variant(enabled=False, text=text)
        }

    def get(self, enabled: bool) -> Image.Image:
        return self.icons[enabled]


class ToastNotification(tk.Toplevel):
    def __init__(self, parent, title, message, timeout_ms=1800):
        super().__init__(parent)
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        
        frame = ttk.Frame(self, padding=12, relief="solid", borderwidth=1)
        frame.pack(fill="both", expand=True)
        
        ttk.Label(frame, text=title, font=("Segoe UI", 9, "bold")).pack(anchor="w")
        ttk.Label(frame, text=message).pack(anchor="w", pady=(2, 0))
        
        self.update_idletasks()
        px = parent.winfo_x() + (parent.winfo_width() // 2) - (self.winfo_width() // 2)
        py = parent.winfo_y() + (parent.winfo_height() // 2) - (self.winfo_height() // 2)
        self.geometry(f"+{px}+{py}")
        
        self.after(timeout_ms, self.destroy)


INVALID_ACTION_PREFIX = "! invalid: "

# Sections a focused device transfers to another alias (U73 d).
DEVICE_TRANSFER_SECTIONS = ("motion_and_thresholds", "feature_toggles", "action_mapping")

# No live connection when the window was built (U383): the section still
# shows above the tabs and its focus is picked up from the live list.
NO_DEVICE_LABEL = "No device connected yet"


def collect_config_sections(config, section_names):
    """Snapshot the named sections of a live config instance as plain dicts."""
    return snapshot_device_sections(config, section_names)


def device_display_name(conn):
    """Device-bar label: the user alias when set, else the interface name (U87 a)."""
    alias = conn.get("alias")
    if alias:
        return alias
    return conn.get("product_string") or "unknown device"


# Touch button and dial controls (U71): one toggle plus one mapping per control.
BUTTON_CONTROL_KEYS = tuple(f"btn{n}_press" for n in range(1, 9)) + (
    "central_press", "dial_cw", "dial_ccw")

# Touch part of each express key (U381 / #381): one toggle plus one modifier
# choice per button, shown only for the 8 express keys. The value vocabulary
# is TOUCH_ACTION_CHOICES ("none" plus the modifier names), not the tap-action
# list; the part defaults to off.
BUTTON_TOUCH_KEYS = tuple(f"btn{n}_touch" for n in range(1, 9))
# Explicit press -> touch pairing (no string slicing): the tab builds the two
# widgets of one express key from this map.
BUTTON_TOUCH_BY_PRESS = {f"btn{n}_press": f"btn{n}_touch" for n in range(1, 9)}


def action_display_value(action, options):
    """Combo display text: the saved action, marked when it is not a valid option."""
    if action in options:
        return action
    return INVALID_ACTION_PREFIX + str(action)


def resolve_action_mapping(display, saved_action, options):
    """Resolve a combobox display back to the value that should be stored.

    A display still showing the marked saved value keeps that saved action, so
    an invalid mapping survives open plus Apply instead of being rewritten to
    the first option.
    """
    if saved_action is not None and display == action_display_value(saved_action, options):
        return saved_action
    return display


def parse_numeric_values(values):
    """Coerce raw settings-entry strings to floats, rejecting non-numeric text.

    #471 (2026-11-25): empty or whitespace-only entry text means the field is
    UNCHANGED - it is skipped, so the stored config value stays (the same
    keep-the-stored-value lane as the _KEEP_WIRELESS_SIZE combo; the entries
    are initialized FROM the config, so cleared text is a lost edit, never a
    user-chosen value). Non-empty invalid text still raises ValueError naming
    the offending key. Nothing is mutated here.
    """
    parsed = {}
    for key, raw in values.items():
        if isinstance(raw, str) and not raw.strip():
            continue
        try:
            parsed[key] = float(raw)
        except (TypeError, ValueError):
            raise ValueError(f"Field '{key}' is not a number: {raw!r}")
    return parsed


# Device orientation (U74 c): direct numeric entry (any angle - the entry
# wraps by 360 and accepts fractions) plus preset shortcuts.
ORIENTATION_PRESETS = (0, 90, 180, 270)
# Generalized tablet aspect for the pictogram (PTH-651 digitizer, mm).
PICTOGRAM_WIDTH = 223.52
PICTOGRAM_HEIGHT = 139.7

# Tablet drawing geometry (U415 / #407 ruling): the Motion-tab pictogram is
# a REDRAWN vector tablet (tkinter-native shapes; the PNG
# images/wacom_intuos_pro_medium.png is CONSTRUCTION REFERENCE ONLY - button
# placement 1-8 + central ring + pen-area placement - and is never
# displayed). One entry per selected tablet size: body = the physical pad
# outline, digitizer = the active area (mirrors the PEN_SPECS mm so the
# drawing tracks the mapping the driver actually uses). The express-key
# strip is LEFT ONLY on every size (the old graphic mirrored an empty
# column to the right); the digitizer sits right of the strip.
TABLET_DRAWINGS = {
    "small": {"body_mm": (210.0, 140.0),
              "digitizer_mm": (PEN_SPECS_BY_PRODUCT[788]["width_mm"],
                               PEN_SPECS_BY_PRODUCT[788]["height_mm"])},
    "medium": {"body_mm": (277.0, 189.0),
               "digitizer_mm": (PEN_SPECS_BY_PRODUCT[789]["width_mm"],
                                PEN_SPECS_BY_PRODUCT[789]["height_mm"])},
    "large": {"body_mm": (378.0, 262.0),
              "digitizer_mm": (PEN_SPECS_BY_PRODUCT[791]["width_mm"],
                               PEN_SPECS_BY_PRODUCT[791]["height_mm"])},
}


def parse_orientation_degrees(raw):
    """Entry string to an angle in [0, 360), fractions kept (F1 2026-11-16).

    Anything numeric wraps by modulo 360 (-10 -> 350, 370 -> 10, 360 -> 0),
    so the field can never hold an angle the rotation matrix would ignore.
    Whole numbers stay ints, fractions stay floats; non-numeric or
    non-finite input returns None (the existing refuse style).
    """
    try:
        degrees = float(raw)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(degrees):
        return None
    degrees %= 360.0
    return int(degrees) if degrees.is_integer() else degrees


# Pen move flush cap (the existing config knob `pen_move_flush_hz`, default
# PEN_MOVE_FLUSH_HZ_DEFAULT = 250 Hz): GUI bounds for the Pen-tab spinbox.
# GUI-B B4 / decision 8=a (maintainer 2026-11-16): widened from 30..1000 to
# 1..2000 in the validator's direction, so extreme test values (e.g. a
# sub-30 Hz congestion experiment) stay reachable in the GUI too; the
# config-file validator keeps its own refuse-only-above-0 rule
# (wtd/config.py). Out-of-range, zero, and negative entries are still
# rejected consistently (the spinbox arrow range mirrors
# parse_move_flush_hz, the Apply Live check). Fractional Hz stay legal.
MOVE_FLUSH_HZ_MIN = 1
MOVE_FLUSH_HZ_MAX = 2000


def parse_move_flush_hz(raw):
    """Float Hz inside MOVE_FLUSH_HZ_MIN..MOVE_FLUSH_HZ_MAX; None when invalid."""
    try:
        hz = float(raw)
    except (TypeError, ValueError):
        return None
    if not MOVE_FLUSH_HZ_MIN <= hz <= MOVE_FLUSH_HZ_MAX:
        return None
    return hz


def _drawing_layout_rects(width, height, digitizer):
    """(strip_width, digitizer_width, digitizer_height, right_margin) (U415).

    The express-key strip is the leftover body width right of it sits the
    digitizer rect, leaving a thin right margin. Without an explicit
    digitizer the body degrades to fixed ratios (legacy default shape).
    """
    if digitizer is None:
        strip = width * 0.22
        right_margin = width * 0.05
        return strip, width - strip - right_margin, height * 0.76, right_margin
    digitizer_w, digitizer_h = float(digitizer[0]), float(digitizer[1])
    right_margin = width * 0.045
    strip = width - digitizer_w - right_margin
    return strip, digitizer_w, digitizer_h, right_margin


def tablet_pictogram_layout(degrees, width=PICTOGRAM_WIDTH, height=PICTOGRAM_HEIGHT,
                            digitizer=None):
    """Rotated tablet pictogram geometry, centered at the origin (U74 d, U415).

    Unrotated layout (U416 re-measured against the product PNG, STEP-0):
    body rectangle spanning +-width/2 x +-height/2; the express-key strip
    LEFT ONLY; the dial ring + central round SIT IN THE STRIP (same column
    as the keys, vertically centered between the groups - NOT at the body
    center, that was the U415 defect); keys 1-4 tightly above the ring and
    5-8 tightly below (numbered top-to-bottom, all circles inside the
    body); the digitizer (pen-area) rect right of the strip. Every point
    rotates clockwise by degrees with the same matrix the input transform
    uses, so widget and cursor mapping stay consistent. Return keys:
    rect, pen_area, strip_right, seam, center (body center), ring_center
    (dial center, in the strip), dial_radius, button_radius, buttons.
    """
    half_w, half_h = width / 2, height / 2
    strip_w, digitizer_w, digitizer_h, right_margin = _drawing_layout_rects(
        width, height, digitizer)
    # Ratios from images/wacom_intuos_pro_medium.png (viewed 2026-11-12):
    # tight groups (key pitch 0.155, inner gap 0.06), key circles fully
    # inside the body edge, ring ~0.24 of half-height BUT CAPPED to the
    # strip (U416 reviewer P1: the strip is narrower than the body height
    # on every size, and on `large` a height-sized ring crossed the body
    # and the seam - the ring must fit the STRIP it lives in).
    dial_radius = min(half_h * 0.24, strip_w / 2 - half_h * 0.02)
    button_radius = half_h * 0.075
    step = half_h * 0.155
    inner_offset = dial_radius + half_h * 0.06 + button_radius
    column_x = -half_w + strip_w / 2
    ring_x, ring_y = column_x, 0.0
    pen_left = -half_w + strip_w
    pen_right = half_w - right_margin

    offsets = {
        1: -inner_offset - 3 * step, 2: -inner_offset - 2 * step,
        3: -inner_offset - step, 4: -inner_offset,
        5: inner_offset, 6: inner_offset + step,
        7: inner_offset + 2 * step, 8: inner_offset + 3 * step,
    }

    def rotated(x, y):
        if degrees == 0:
            return (x, y)
        return rotate_point(x, y, degrees, 0, 0)

    corners = [(-half_w, -half_h), (half_w, -half_h),
               (half_w, half_h), (-half_w, half_h)]
    pen_corners = [(pen_left, -digitizer_h / 2), (pen_right, -digitizer_h / 2),
                   (pen_right, digitizer_h / 2), (pen_left, digitizer_h / 2)]
    seam_raw = [(strip_w - half_w, -half_h), (strip_w - half_w, half_h)]
    buttons = [(number, *rotated(column_x, offset))
                for number, offset in offsets.items()]
    return {
        "rect": [rotated(x, y) for x, y in corners],
        "seam": [rotated(x, y) for x, y in seam_raw],
        "pen_area": [rotated(x, y) for x, y in pen_corners],
        "strip_right": strip_w - half_w,
        "center": rotated(0, 0),
        "ring_center": rotated(ring_x, ring_y),
        "dial_radius": dial_radius,
        "button_radius": button_radius,
        "buttons": buttons,
    }


TABLET_SIZE_BY_PRODUCT = {788: "small", 789: "medium", 791: "large"}


def resolve_drawing_size(product_id=None, wireless_tablet_size=None):
    """Which tablet drawing to show (U415 / #407 ruling: size-driven).

    Wired products map directly; the wireless receiver (132) cannot detect
    its tablet, so the user's Pen-tab choice drives the drawing and an
    unset choice falls back to the driver's medium mapping (mirrors the
    U386 mapping default). Unknown/missing falls back to medium.
    """
    if product_id in TABLET_SIZE_BY_PRODUCT:
        return TABLET_SIZE_BY_PRODUCT[product_id]
    if product_id == WIRELESS_RECEIVER_PRODUCT_ID and wireless_tablet_size \
            in WIRELESS_TABLET_SIZES:
        return wireless_tablet_size
    return "medium"


def pictogram_view_scale(layout, canvas_w, canvas_h, safety=0.93):
    """Canvas px per mm that fits the ROTATED layout inside the canvas (U415).

    The U74d defect: the widget scale was fixed on the UNROTATED height, so
    at 90 deg the drawing left the drawing area. The view scale is refit
    from the rotated bounding box of EVERY drawn feature (frame corners,
    digitizer corners, button circles, dial ring), times a small safety
    margin for text extents.
    """
    features = ([(x, y, 0.0) for x, y in layout["rect"]]
                + [(x, y, 0.0) for x, y in layout["pen_area"]]
                + [(x, y, layout["button_radius"]) for _, x, y in layout["buttons"]]
                # U416 P2-2: the DRAWN ring sits at ring_center (the old
                # body-center reference was stale and unbounded the fit).
                + [(*layout["ring_center"], layout["dial_radius"])])
    extents_x = max(abs(x) + radius for x, _, radius in features)
    extents_y = max(abs(y) + radius for _, y, radius in features)
    return min(canvas_w / 2 / extents_x, canvas_h / 2 / extents_y) * safety


class OrientationWidget(tk.Canvas):
    """Orientation pictogram (U74 d, redrawn in U415): the tablet as a
    tkinter-native DRAWING (no image file; the product PNG is construction
    reference only). Body outline, left-only express-key strip, the digitizer
    (pen-area) rect, central dial ring + round, keys 1-4 above and 5-8
    mirrored below; the whole drawing rotates clockwise with the orientation
    angle and REFITS its scale per angle (the old fit used the unrotated
    height and escaped the canvas at 90 deg). Key labels do not renumber.
    """
    CANVAS_WIDTH = 220
    CANVAS_HEIGHT = 150

    def __init__(self, parent, degrees=0, size="medium", **kwargs):
        super().__init__(parent, width=self.CANVAS_WIDTH, height=self.CANVAS_HEIGHT,
                         background="#2b2b2b", highlightthickness=1,
                         highlightbackground="#666", **kwargs)
        self.size = size
        self.degrees = degrees
        self.draw_degrees(degrees)

    def _canvas_point(self, x, y):
        return (self.CANVAS_WIDTH / 2 + x * self._scale,
                self.CANVAS_HEIGHT / 2 + y * self._scale)

    def draw_degrees(self, degrees):
        """Redraw the pictogram at the given angle for the widget's size."""
        self.degrees = degrees
        drawing = TABLET_DRAWINGS.get(self.size, TABLET_DRAWINGS["medium"])
        body_w, body_h = drawing["body_mm"]
        layout = tablet_pictogram_layout(degrees, body_w, body_h,
                                         digitizer=drawing["digitizer_mm"])
        self._scale = pictogram_view_scale(layout, self.CANVAS_WIDTH,
                                           self.CANVAS_HEIGHT)
        scale = self._scale
        self.delete("all")

        self.create_polygon(*[p for corner in layout["rect"]
                              for p in self._canvas_point(*corner)],
                            fill="#3a3a3a", outline="#8a8a8a")

        # Pen-area (digitizer) rect right of the strip + the strip seam.
        self.create_polygon(*[p for corner in layout["pen_area"]
                              for p in self._canvas_point(*corner)],
                            fill="#31435a", outline="#6a9fd8")
        # U416 / #407 defect 3: the seam is a LAYOUT element now - both
        # endpoints go through the rotation transform like everything else
        # (it used to be drawn from unrotated corners and stayed vertical).
        (sx1, sy1), (sx2, sy2) = (self._canvas_point(*end)
                                  for end in layout["seam"])
        self.create_line(sx1, sy1, sx2, sy2, fill="#5a5a5a",
                         tags="penarea_seam")

        # Dial ring + central round IN THE STRIP (U416 / PNG): same column
        # as the keys, centered between the groups.
        cx, cy = self._canvas_point(*layout["ring_center"])
        dial = layout["dial_radius"] * scale
        self.create_oval(cx - dial, cy - dial, cx + dial, cy + dial,
                         outline="#6a9fd8")
        # U416 P2-3 (PNG): the central button carries NO text (the label
        # sprawled across the small round); keep it a plain filled round,
        # capped inside the ring.
        central = min(layout["button_radius"] * 1.2,
                      layout["dial_radius"] * 0.55) * scale
        self.create_oval(cx - central, cy - central, cx + central, cy + central,
                         fill="#2e5a86", outline="#6a9fd8")

        button = layout["button_radius"] * scale
        for number, x, y in layout["buttons"]:
            bx, by = self._canvas_point(x, y)
            self.create_oval(bx - button, by - button, bx + button, by + button,
                             fill="#575757", outline="#9a9a9a")
            self.create_text(bx, by, text=str(number), fill="#e6e6e6",
                             font=("Segoe UI", 8))


def _screen_label(record):
    """Human label for a monitor record 'PATH|left|top|WxH' (U424)."""
    try:
        path, left, top, dims = record.split("|")
        return f"{path.split(chr(92))[-1].lstrip('.')} {left},{top} {dims}"
    except ValueError:
        return record


# PENAREA (maintainer-requested 2026-11-16): the editor rect may extend
# PARTLY outside the combined display area. The old clamp (rect inside the
# display bbox) is retired. The PENAREA corner rule ("at least one corner
# of the pen-area rectangle must remain inside the display area") is
# SUPERSEDED by u467/#467: per corner it is the UNION of four origin
# regions, disconnected on one axis once the rect is longer than the
# display on that axis - the gap made slow drags pin a corner ON the edge
# (uncrossable) and only fast jumps crossed, exactly the maintainer's
# rotation 45/90 report. Current rule: the rect must INTERSECT the display
# area (closed edges). For a rect that fits the display the two rules are
# the identical interval, so the proven-good 0 deg behavior is preserved;
# an oversized rect may cover the display (every corner outside, the whole
# screen still mapped) instead of sticking at one edge.
VIEW_MARGIN = 0.15  # fraction of the desktop dims added per viewport side
# PENAREA: the placement rule (#467) binds the PLACEMENT, not the size, so the editor
# keeps an arithmetic bound of its own - a wanted size beyond
# MAX_SIZE_FACTOR x the larger desktop dim (or non-finite) floors/caps
# instead of propagating: the retired inside-union cap used to hide these
# inputs, and an unbounded size reaches the normalized 0..65535 pen math and
# overflows it. 10x the desktop is far above any real pen area.
MAX_SIZE_FACTOR = 10.0


def rect_overlaps_display(rect, bbox):
    """True when the rect intersects the display bbox (closed edges).

    The bbox is [x, y, w, h]. Placement rule (#467, superseding the PENAREA
    corner rule): the pen-area rect must keep touching the display area,
    touching an edge counts. Per axis the legal origins form ONE interval
    (the rect's edges straddle the display's), so unlike the retired corner
    rule this set is connected for EVERY size - that is what makes slow and
    fast drags land identically. For a rect no longer than the display on
    both axes this is value-for-value the retired corner rule (the two
    corner origin-regions join into exactly this interval), which is why
    the 0 deg pins never move.
    """
    rx, ry, rw, rh = rect
    bx, by, bw, bh = bbox
    return (bx <= rx + rw and rx <= bx + bw
            and by <= ry + rh and ry <= by + bh)


def clamp_rect_to_display(rect, bbox):
    """Translate a whole rect to the nearest placement that obeys the rule.

    Pure (rect + bbox -> allowed rect), no tkinter. Placement rule (#467):
    the rect must intersect the display area (closed edges); the size is
    untouched, so the function is total. The legal origins are the interval
    product [bx - rw, bx + bw] x [by - rh, by + bh]; the nearest placement
    is the per-axis clamp onto it (smallest whole-rect displacement). The
    retired corner-rule clamp projected onto the union of four per-corner
    regions - disconnected once the rect is longer than the display on an
    axis, so its projection pinned slow drags at the gap's near edge while
    fast jumps crossed (the #467 rotation report). The move lanes run every
    candidate through here: an overshooting drag stops with the rect's edge
    riding the display edge instead of escaping it (no popup); an oversized
    rect that covers the display stands where it is.
    """
    rx, ry, rw, rh = rect
    bx, by, bw, bh = bbox
    if bw <= 0 or bh <= 0:
        return [float(rx), float(ry), float(rw), float(rh)]
    rx = min(max(rx, bx - rw), bx + bw)
    ry = min(max(ry, by - rh), by + bh)
    return [rx, ry, rw, rh]


def projection_overlay_corners(rect, degrees, pen_specs):
    """GUI-B B1: the rotated projection footprint inside the blue bbox.

    The editor's blue rect is the axis-aligned BOUNDING BOX of the pen
    projection (its size follows the rotated mm box; it is never drawn
    rotated). This returns the ACTUAL footprint: the four device-box
    corners run through the SAME two steps a pen cursor takes in
    wtd.mapping.update_pen_cursor - rotation about `pen_rotation_center`
    by `degrees` via `rotate_point` (the one sin/cos matrix shared by both
    input paths; y down, positive angle clockwise on screen), then the
    per-axis affine that maps `rotated_pen_extents` onto the rect (the
    rect-space form of `pen_area_norm_projection`: off + (q - left) *
    rw / extent_w). Every overlay corner is exactly where that device
    corner sends the cursor - the footprint BY CONSTRUCTION, hence always
    INSIDE the blue bbox, touching its edges where the rotated device box
    touches the extents box (the reviewer P1 over the first draft, which
    rotated the bbox itself - a circumscribed box, not the footprint).
    Returned in CYCLIC order (TL, TR, BR, BL) for the polygon lane
    (a row-major corner order would draw a bowtie - reviewer P1).
    Anchor: `pen_rotation_center` projects to the center of the extents
    box, which the affine carries to the rect center (pinned in
    test_gui_b_rotation_anchor_maps_to_the_rect_center). Uniform-density
    digitizers (every shipped PEN_SPECS row is exactly 200 counts/mm on
    both axes - pinned in
    test_gui_b_shipped_specs_are_uniform_density) keep the affine
    isotropic, so the footprint is a true rectangle.
    """
    left, top, ext_w, ext_h = rotated_pen_extents(pen_specs, degrees)
    cx, cy = pen_rotation_center(pen_specs)
    rx, ry, rw, rh = rect
    sx, sy = rw / ext_w, rh / ext_h
    mx, my = float(pen_specs["max_x"]), float(pen_specs["max_y"])
    rotated = [rotate_point(x, y, degrees, cx, cy)
               for x, y in ((0.0, 0.0), (mx, 0.0), (mx, my), (0.0, my))]
    return [(rx + (x - left) * sx, ry + (y - top) * sy)
            for x, y in rotated]


class PenAreaCanvasModel:
    """Pure model behind the pen-area rect editor (U414 / #406 option A).

    Owns the virtual-desktop geometry, the canvas scale, and the rect in
    virtual-desktop px measured FROM THE DESKTOP ORIGIN (the datum of
    `pen_area_norm_projection`). No tkinter lives here, so the editor's
    math is pinned headless (tests/test_pen_area_editor.py); the widget
    only maps canvas events into these calls.
    """

    MIN_RECT_PX = 16.0

    def __init__(self, metrics, records, canvas_w, canvas_h, box_mm_w, box_mm_h,
                 rect_px=None):
        left, top, width, height = metrics
        self.origin_x, self.origin_y = float(left), float(top)
        self.desktop_w, self.desktop_h = float(width), float(height)
        self.records = [str(rec) for rec in records]
        self.box_mm = (float(box_mm_w), float(box_mm_h))
        self.canvas_w, self.canvas_h = float(canvas_w), float(canvas_h)
        if rect_px is None:
            start_w = min(self.desktop_w * 2.0 / 3.0,
                          self.desktop_h * self.box_mm[0] / self.box_mm[1])
            rect = [float(v) for v in pen_area_rect_aspect_lock(
                [0, 0, start_w, 0], self.box_mm[0], self.box_mm[1])]
        else:
            rect = [float(v) for v in rect_px]
        # PENAREA: a stored or hand-edited entry that leaves NO overlap
        # with the display area (the rule has no size cap, only a placement
        # rule - #467) snaps back to the nearest rule-valid placement at build.
        self.rect = clamp_rect_to_display(rect, self.display_bbox())
        # U424r4 R4-A (#424 item A) generalized by PENAREA: the CANVAS
        # VIEWPORT is the display bbox plus a margin (extended to the rect
        # when it hangs outside), centered in the canvas with equal margins,
        # so an off-screen rect part stays visible and grabbable.
        self._set_view()

    def _set_view(self):
        """Derive the viewport (and the scale/offsets) from the CURRENT rect.

        Called at build and by `retarget_view` only - NEVER while a drag is
        in progress, so the scale cannot fight the pointer (the U424 L3
        jitter rule: no transform change between press and release).
        """
        pad_x = self.desktop_w * VIEW_MARGIN
        pad_y = self.desktop_h * VIEW_MARGIN
        self.view_x0 = min(0.0, self.rect[0]) - pad_x
        self.view_y0 = min(0.0, self.rect[1]) - pad_y
        self.view_w = (max(self.desktop_w, self.rect[0] + self.rect[2])
                       + pad_x - self.view_x0)
        self.view_h = (max(self.desktop_h, self.rect[1] + self.rect[3])
                       + pad_y - self.view_y0)
        self.scale = min(self.canvas_w / self.view_w, self.canvas_h / self.view_h)
        self.offset_x = (self.canvas_w - self.view_w * self.scale) / 2.0
        self.offset_y = (self.canvas_h - self.view_h * self.scale) / 2.0

    def retarget_view(self):
        """Refit the viewport to the current rect (PENAREA, reviewer P1).

        The placement rule legally parks the rect far enough out that its
        lower-right handle leaves the fixed-size canvas, which would strand
        the resize lane. The editor therefore calls this at the END of each
        interaction (drag release, field apply, reorient): the view refits
        so the whole rect and its handle are visible again. The rect datum
        (desktop px from the virtual-desktop origin) is untouched, so the
        persisted fractions cannot move.
        """
        self._set_view()

    def display_bbox(self):
        """The combined display area in the model datum (desktop px)."""
        return (0.0, 0.0, self.desktop_w, self.desktop_h)

    def canvas_xy(self, x, y):
        """Desktop-px (origin-relative) -> canvas px (viewport, centered)."""
        return ((x - self.view_x0) * self.scale + self.offset_x,
                (y - self.view_y0) * self.scale + self.offset_y)

    def desktop_xy_from_canvas(self, canvas_x, canvas_y):
        """Canvas px -> desktop-px (origin-relative); the inverse of canvas_xy."""
        return ((canvas_x - self.offset_x) / self.scale + self.view_x0,
                (canvas_y - self.offset_y) / self.scale + self.view_y0)

    def _bounded_size(self, value):
        """Floor/cap a wanted rect size into the editor's arithmetic bound.

        PENAREA (reviewer P2): with the inside-union size cap retired, a
        non-finite or absurd field entry would propagate an infinite height
        into `persist_entry` (Apply Live raised) and into the normalized pen
        mapping (OverflowError). Non-finite floors to MIN_RECT_PX, a huge
        size caps at MAX_SIZE_FACTOR x the larger desktop dim; both echo
        back through the field sync, so the control agrees with the model.
        """
        try:
            wanted = float(value)
        except (TypeError, ValueError):
            return self.MIN_RECT_PX
        if wanted != wanted:  # NaN floors (+inf caps via min, -inf floors)
            return self.MIN_RECT_PX
        if wanted < self.MIN_RECT_PX:
            return self.MIN_RECT_PX
        return min(wanted, MAX_SIZE_FACTOR * max(self.desktop_w,
                                                 self.desktop_h))

    @staticmethod
    def parse_record(record):
        """'<path>|<left>|<top>|<W>x<H>' -> (left, top, width, height) ints."""
        parts = record.split("|")
        width_txt, height_txt = parts[3].split("x")
        return int(parts[1]), int(parts[2]), int(width_txt), int(height_txt)

    def apply_fields(self, x=None, y=None, w=None):
        """Set the rect from the numeric fields (U424 / #424 ruling 2).

        Width aspect-locks the height to the rotated digitizer box (PENAREA:
        no display-bbox size cap - the rect may extend beyond the screens).
        A size at a placement that would leave NO overlap with the display
        area is rejected (the previous size stands, so the field echoes it
        back); a position that breaks the #467 placement rule clamps to the
        nearest rule-valid placement. Returns the new rect [rx, ry, rw, rh].
        """
        rx, ry, rw, rh = self.rect
        bbox = self.display_bbox()
        if w is not None:
            wanted_w = self._bounded_size(w)
            candidate = [rx, ry, wanted_w,
                         wanted_w * self.box_mm[1] / self.box_mm[0]]
            if rect_overlaps_display(candidate, bbox):
                rx, ry, rw, rh = candidate
        if x is not None:
            rx = float(x)
        if y is not None:
            ry = float(y)
        self.rect = clamp_rect_to_display([rx, ry, rw, rh], bbox)
        return list(self.rect)

    def apply_scale(self, px_mm):
        """Resize the footprint to the wanted sensitivity px/mm, keeping its
        CENTER (U424r3 R3-L3, #424 item 3b; protocol DECISION: s is the
        derived handle s = rw_px / box_mm_w - aspect-lock makes one scalar
        suffice; footprint semantics and the pen math are unchanged).

        PENAREA: no display-bbox size cap. #467: a huge centered
        sensitivity COVERS the display and stands (the center promise holds
        exactly); only a placement that left the display entirely slides to
        the nearest rule-valid overlap.
        """
        cx = self.rect[0] + self.rect[2] / 2
        cy = self.rect[1] + self.rect[3] / 2
        w = self._bounded_size(float(px_mm) * self.box_mm[0])
        h = w * self.box_mm[1] / self.box_mm[0]
        self.rect = clamp_rect_to_display([cx - w / 2.0, cy - h / 2.0, w, h],
                                          self.display_bbox())
        return list(self.rect)

    def match_box_aspect(self, s=None):
        """Re-derive the rect shape to the current box aspect (U424r5
        R5-E / #424 item E, watch resolved: the SHAPE follows
        orientation). Rule: keep the CENTER and the SENSITIVITY s (new w = s
        x new box_w, height follows the new aspect), then validate the
        placement with the placement-rule clamp (PENAREA/#467). No-op (tolerance
        1e-6 rel) when the rect
        already matches, so normal loads never move the stored rect.
        `s` = the sensitivity of the PREVIOUS box (callers that swap
        box_mm first pass it explicitly; None = derive from the model's
        own box, i.e. same-box re-derivation at build).
        """
        bw, bh = self.box_mm
        if bw <= 0 or bh <= 0 or self.rect[2] <= 0 or self.rect[3] <= 0:
            return list(self.rect)
        want = bw / bh
        if abs(want - self.rect[2] / self.rect[3]) <= 1e-6 * want:
            return list(self.rect)
        if s is None:
            s = self.rect[2] / bw
        cx = self.rect[0] + self.rect[2] / 2.0
        cy = self.rect[1] + self.rect[3] / 2.0
        w = s * bw
        h = w * bh / bw
        # U424r5 R5-E stays: the shape must always agree with the box,
        # never clamp into a wrong aspect. PENAREA retires the fit-to-union
        # size cap (the rect may extend beyond the screens now), so the
        # sensitivity rides across the rotation EXACTLY even when the rotated
        # box exceeds the display bbox; the #467 placement rule then
        # validates the placement with the same clamp every move lane uses
        # (rotation can therefore never silently invalidate the rule, and
        # with a connected legal set it can no longer snap the center per
        # park position either - the #467 report).
        self.rect = clamp_rect_to_display([cx - w / 2.0, cy - h / 2.0, w, h],
                                          self.display_bbox())
        return list(self.rect)

    def monitor_canvas_rects(self):
        """Monitor outline boxes in canvas px, origin = virtual-desktop origin.

        U416: with no usable records (fallback bounds) it returns ONE box
        covering the bounds, so the canvas shows the single virtual-
        desktop rectangle instead of staying black.
        """
        if not self.records:
            x0, y0 = self.canvas_xy(0.0, 0.0)
            x1, y1 = self.canvas_xy(self.desktop_w, self.desktop_h)
            return [(x0, y0, x1, y1)]
        boxes = []
        for record in self.records:
            left, top, width, height = self.parse_record(record)
            x0, y0 = self.canvas_xy(left - self.origin_x, top - self.origin_y)
            x1, y1 = self.canvas_xy(left - self.origin_x + width,
                                    top - self.origin_y + height)
            boxes.append((x0, y0, x1, y1))
        return boxes

    def rect_canvas(self):
        """The rect in canvas px (origin-relative datum through the view
        transform - U424r4 R4-A, PENAREA viewport)."""
        rx, ry, rw, rh = self.rect
        x0, y0 = self.canvas_xy(rx, ry)
        x1, y1 = self.canvas_xy(rx + rw, ry + rh)
        return (x0, y0, x1, y1)

    def handle_canvas(self):
        """Lower-right corner handle position in canvas px."""
        rx, ry, rw, rh = self.rect
        return self.canvas_xy(rx + rw, ry + rh)

    def move_by(self, delta_canvas_x, delta_canvas_y):
        """Drag the whole rect by a canvas-px delta (PENAREA).

        The old clamp kept the whole rect inside the desktop bbox - that
        "ok for now" expired. Now only the placement rule applies (the rect
        must keep intersecting the display area - PENAREA corner rule as
        superseded by #467), so a drag may take the rect partly off-screen;
        an update that would leave NO overlap clamps to the nearest
        rule-valid placement (the rect simply stops, no popup).
        """
        rx, ry, rw, rh = self.rect
        self.rect = clamp_rect_to_display(
            [rx + delta_canvas_x / self.scale,
             ry + delta_canvas_y / self.scale, rw, rh], self.display_bbox())

    def resize_handle_to(self, handle_canvas_x, handle_canvas_y):
        """Move the lower-right handle to a canvas point; aspect stays locked.

        Width follows the drag (PENAREA: no display-bbox cap, so the rect may
        grow past the screens); rh is re-derived from the rotated mm box
        (`pen_area_rect_aspect_lock`), so the aspect never breaks. The
        placement rule still decides: a wanted size at a placement that would
        leave NO overlap with the display area is rejected - the handle
        simply stops at the last valid size (no popup). With the #467 rule
        the rejection can no longer depend on which corner happens to be the
        only anchor inside (that position-dependence was the report).
        """
        rx, ry = self.rect[0], self.rect[1]
        handle_x, _ = self.desktop_xy_from_canvas(handle_canvas_x,
                                                  handle_canvas_y)
        wanted = handle_x - rx
        locked = pen_area_rect_aspect_lock(
            [rx, ry, int(self._bounded_size(wanted)), 0],
            self.box_mm[0], self.box_mm[1])
        if rect_overlaps_display(locked, self.display_bbox()):
            self.rect = [float(v) for v in locked]

    def readout(self):
        """Live mm/px readout line for the editor label."""
        rx, ry, rw, rh = self.rect
        return ("%dx%d px at (%d,%d) | pen box %.2fx%.2f mm | %.4f px/mm"
                % (int(rw), int(rh), int(rx), int(ry),
                   self.box_mm[0], self.box_mm[1], rw / self.box_mm[0]))


class PenAreaEditor(ttk.Frame):
    """In-tab pen-area rect editor (U414 / #406, option A ruled 2026-11-11).

    Scaled virtual-desktop preview (monitor outlines), draggable rect with
    lower-right aspect-locked handle, live readout, and Save/Remove into
    the per-display-configuration `pen_area_map_<identity>` lane. Save
    re-resolves the running reader's display key immediately
    (`reset_pen_area_key_cache`), the #386 Apply Live shape for this
    feature. U416 (maintainer black-canvas host): the editor ALWAYS builds
    - bounds from vetted metrics, else the monitor-record union, else one
    pad-aspect fallback rectangle; device identity gates SAVE only, never
    editing. Fallback hosts share the empty-records display key; entries
    live in per-identity lanes so different pads cannot collide, and a
    pad-orientation flip between fallback sessions reinterprets fractions
    under the flipped box aspect (rare, self-healing on next save).
    """

    CANVAS_W = 320.0
    CANVAS_H = 120.0
    HANDLE = 8

    def __init__(self, parent, config, device_info, on_change=None, **kwargs):
        super().__init__(parent, **kwargs)
        self.config = config
        # U424r3 R3-L3 (#424 item 3a): the window hooks this to sync the
        # numeric fields when the model moves (drag/resize) - the sync is
        # bidirectional.
        self.on_change = on_change
        conn = (device_info or {}).get("connection") or {}
        # U416 / #406 defect: the model ALWAYS builds (the canvas is
        # editable everywhere); device identity names the lane, so it is
        # required at SAVE time only, never for editing. Bounds resolve
        # in order: vetted virtual metrics -> EnumDisplayMonitors union ->
        # single fallback rectangle at the pad's aspect (fractions are
        # load-stable across fallback sessions via the empty-records key).
        self.identity = conn.get("identity")
        metrics = virtual_desktop_metrics()
        records = monitor_records()
        # U416 P2-4: no magic fallback product id - pen_specs_for_product
        # itself names the unknown-id fallback (byte-stable notice +
        # Medium extents), which is exactly the pad to draw here.
        product_id = conn.get("product_id")
        specs = pen_specs_for_product(product_id, config)
        box_mm = rotated_pen_box_mm(specs, config.orientation_degrees)
        # GUI-B B1: the rotated projection overlay follows the SAME angle
        # the pen path rotates by (reorient keeps it current), and the
        # SAME device extents the cursor math rotates (reviewer P1).
        self.orientation_degrees = config.orientation_degrees
        self.pen_specs = specs
        bounds = None
        usable_records = []
        if metrics is not None and metrics[2] > 0 and metrics[3] > 0:
            bounds, usable_records = metrics, records
        else:
            union = union_rect_from_records(records)
            if union is not None and union[2] > 0 and union[3] > 0:
                bounds, usable_records = union, records
            else:
                bounds = (0.0, 0.0, box_mm[0], box_mm[1])
        self.model = PenAreaCanvasModel(
            bounds, usable_records, self.CANVAS_W, self.CANVAS_H,
            box_mm[0], box_mm[1],
            self._stored_rect(usable_records, bounds[2], bounds[3]))
        # U424r5 R5-E: a legacy/stale stored rect that does not match the
        # current box aspect is re-derived at BUILD (canvas-only; the
        # next Apply persists).
        self.model.match_box_aspect()
        self.canvas = tk.Canvas(self, width=int(self.CANVAS_W),
                                height=int(self.CANVAS_H), bg="#262626",
                                borderwidth=1, highlightthickness=0)
        # U424 L3 (#424 canvas jitter): the canvas is pinned to the west
        # cell edge and the readout label carries a FIXED character width,
        # so the rect-dependent readout can never resize the row and
        # slide the fixed-size canvas left/right on redraw. The view
        # transform itself derives from the screen bounds ONCE (model
        # __init__: scale from desktop dims only, never from the rect).
        self.canvas.grid(row=0, column=0, columnspan=2, pady=(0, 4), sticky="w")
        self.readout_label = ttk.Label(self, text=self.model.readout(),
                                       width=72, anchor="w")
        self.readout_label.grid(row=1, column=0, columnspan=2, sticky="w")
        # U424r3 R3-L2 (#424 item 2): no "Save Pen Area" - Apply Live
        # persists the rect (persist_entry, driven by the window's Apply).
        # U424r4 R4-D (#424 item D): "Remove Rect" REMOVED too - a
        # modeless state change with no feedback; the mode radios already
        # cover switching away from 1:1.
        self._drag_anchor = None
        self._drag_mode = "rect"
        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._motion)
        # PENAREA (reviewer P1): the viewport refits when the button lets go
        # (never mid-drag), so a rect parked at the placement-rule limit keeps
        # its handle inside the canvas and the resize lane reachable.
        self.canvas.bind("<ButtonRelease-1>", self._release)
        self._redraw()

    def _stored_rect(self, records, bounds_w, bounds_h):
        """Stored FRACTION for the current display key, in model px."""
        if self.identity is None:
            return None
        lane = self.config.pen_area_maps.get(self.identity)
        entry = select_pen_area_entry(lane, display_key_for_records(records))
        if entry is None:
            return None
        fx, fy, fw, fh = entry["rect_frac"]
        return [fx * bounds_w, fy * bounds_h, fw * bounds_w, fh * bounds_h]

    def reorient(self, pen_specs, orientation_degrees):
        """Follow a tablet-orientation change (U424r4 R4-B / #424 item B).

        PEN PATH WINS: the pen maps the STORED rect as the footprint and
        fills it with the ROTATED extents (wtd.mapping
        pen_area_norm_projection + update_pen_cursor), so the editor
        keeps the rect px footprint EXACTLY and only updates its box_mm
        - the mm readout, the sensitivity (w-axis) echo and the aspect
        for future edits then match what the pen will do. No silent
        footprint change, ever.
        """
        if self.model is None:
            return
        # U424r5 R5-E (#424 item E, watch resolved): the DRAWN SHAPE
        # follows too - re-derived at fixed center + the OLD box's
        # sensitivity (s rides across the swap when the union fits), no
        # pointer interaction required. Apply persists it (the window
        # reorients BEFORE persisting); the pen then follows the entry.
        box = rotated_pen_box_mm(pen_specs, orientation_degrees)
        # GUI-B B1 overlay follows the pen path's angle AND extents.
        self.orientation_degrees = orientation_degrees
        self.pen_specs = pen_specs
        old_bw = self.model.box_mm[0]
        s = self.model.rect[2] / old_bw if old_bw > 0 else None
        self.model.box_mm = (float(box[0]), float(box[1]))
        self.model.match_box_aspect(s)
        self.model.retarget_view()  # PENAREA: the refit shape may need room
        self._redraw()

    def persist_entry(self):
        """Write the model rect (FRACTIONS) under the current key.

        U414 L1; U424r3 R3-L2: driven by the window's Apply Live (the old
        standalone "Save Pen Area" button was removed). No file write here
        - persistence follows Save like every other setting; the key cache
        reset keeps the running reader live.
        """
        if self.model is None or self.identity is None:
            return False
        key = display_key_for_records(self.model.records)
        w, h = self.model.desktop_w, self.model.desktop_h
        rx, ry, rw, rh = self.model.rect
        self.config.set_pen_area_entry(
            self.identity, key, self.model.records,
            [rx / w, ry / h, rw / w, rh / h])
        reset_pen_area_key_cache()
        return True

    def _press(self, event):
        self._drag_anchor = (event.x, event.y)
        hx, hy = self.model.handle_canvas()
        self._drag_mode = ("handle" if abs(event.x - hx) <= self.HANDLE
                           and abs(event.y - hy) <= self.HANDLE else "rect")

    def _motion(self, event):
        if self._drag_anchor is None or self.model is None:
            return
        if self._drag_mode == "handle":
            self.model.resize_handle_to(float(event.x), float(event.y))
        else:
            self.model.move_by(event.x - self._drag_anchor[0],
                               event.y - self._drag_anchor[1])
            self._drag_anchor = (event.x, event.y)
        self._redraw()
        if self.on_change is not None:
            self.on_change()

    def _release(self, event):
        """Refit the viewport once the pointer lets go (PENAREA, P1 cure).

        Mid-drag the transform stays fixed (the jitter rule); at release the
        view refits to the rect, so the whole rect and its handle are inside
        the canvas again before the next interaction.
        """
        if self.model is None:
            return
        self.model.retarget_view()
        self._redraw()

    def _redraw(self):
        self.canvas.delete("all")
        if self.model is None:
            return
        for box in self.model.monitor_canvas_rects():
            self.canvas.create_rectangle(*box, outline="#888888")
        rx0, ry0, rx1, ry1 = self.model.rect_canvas()
        self.canvas.create_rectangle(rx0, ry0, rx1, ry1, outline="#00c8ff",
                                     width=2, tags="penarea_rect")
        # GUI-B B1 (maintainer-accepted design, reviewer P1 fixes): the
        # blue rect stays the axis-aligned bounding box; the lighter
        # DASHED outline drawn over it is the ACTUAL projected footprint
        # (device corners through the cursor's own rotate + extents-to-rect
        # affine), always INSIDE the blue frame. At the 90-degree multiples
        # the footprint IS the bbox - there the dashes would just re-draw
        # the solid blue frame, so the overlay is skipped (reviewer P2).
        if self.orientation_degrees % 90:
            overlay = [self.model.canvas_xy(*corner) for corner in
                       projection_overlay_corners(self.model.rect,
                                                  self.orientation_degrees,
                                                  self.pen_specs)]
            self.canvas.create_polygon(
                *[p for corner in overlay for p in corner],
                outline="#aee4ff", fill="", dash=(4, 3),
                tags="penarea_projection")
        hx, hy = self.model.handle_canvas()
        half = self.HANDLE / 2.0
        self.canvas.create_rectangle(hx - half, hy - half, hx + half, hy + half,
                                     fill="#00c8ff", tags="penarea_handle")
        self.readout_label.config(text=self.model.readout())


def apply_settings_to_config(config, numeric_values, accel_toggles, feature_toggles,
                             action_mappings, pen_button_actions=None, pen_mode=None,
                             button_actions=None, button_toggles=None,
                             wireless_tablet_size=_KEEP_WIRELESS_SIZE):
    """Apply a complete settings set to config, or apply nothing at all.

    numeric_values are raw entry strings; they are validated before the first
    mutation, so one bad entry cannot leave config half-applied. An empty
    entry string means unchanged: the field is skipped and its stored value
    stays (#471), including the orientation_degrees / pen_move_flush_hz lanes
    (their guards key on the parsed set).
    wireless_tablet_size (U386 / TODO #385) is the Pen-tab combo choice: one
    of WIRELESS_TABLET_SIZES, None for the explicit unset, or the sentinel
    default to leave the attribute untouched.
    """
    if wireless_tablet_size is not _KEEP_WIRELESS_SIZE and wireless_tablet_size is not None \
            and wireless_tablet_size not in WIRELESS_TABLET_SIZES:
        raise ValueError(f"Field 'wireless_tablet_size' needs one of "
                         f"{'/'.join(WIRELESS_TABLET_SIZES)} or unset: "
                         f"{wireless_tablet_size!r}")

    parsed = parse_numeric_values(numeric_values)

    if "orientation_degrees" in parsed:
        # F1 2026-11-16: the entry wraps by 360 (fractions kept), so only a
        # non-numeric or non-finite value can refuse here.
        degrees = parse_orientation_degrees(numeric_values["orientation_degrees"])
        if degrees is None:
            raise ValueError(f"Field 'orientation_degrees' needs a number "
                             f"(angles wrap by 360): "
                             f"{numeric_values['orientation_degrees']!r}")
        parsed["orientation_degrees"] = degrees

    if "pen_move_flush_hz" in parsed:
        # The Pen-tab spinbox lane of the existing WTD-BATCH knob: bounded
        # per MOVE_FLUSH_HZ_MIN/MAX before the first mutation (the same
        # all-or-nothing contract as the orientation entry; the config-file
        # validator keeps its own refuse-only-above-0 rule).
        flush_hz = parse_move_flush_hz(numeric_values["pen_move_flush_hz"])
        if flush_hz is None:
            raise ValueError(f"Field 'pen_move_flush_hz' needs a number "
                             f"{MOVE_FLUSH_HZ_MIN}..{MOVE_FLUSH_HZ_MAX} Hz: "
                             f"{numeric_values['pen_move_flush_hz']!r}")
        parsed["pen_move_flush_hz"] = flush_hz

    for key, val in parsed.items():
        setattr(config, key, val)

    if wireless_tablet_size is not _KEEP_WIRELESS_SIZE:
        config.wireless_tablet_size = wireless_tablet_size

    config.sync_sensitivities()

    config.feature_toggles["cursor_acceleration"] = accel_toggles["cursor_acceleration"]
    config.feature_toggles["scroll_acceleration"] = accel_toggles["scroll_acceleration"]

    for feat, enabled in feature_toggles.items():
        config.feature_toggles[feat] = enabled

    for feat_key, action in action_mappings.items():
        config.action_mapping[feat_key] = action

    if pen_mode is not None:
        # U424: the Pen tab applies the ONE stored mode (the old
        # pen_absolute_mode toggle write is retired - no dual control).
        config.set_pen_mode(pen_mode)

    for pen_key, action in (pen_button_actions or {}).items():
        config.action_mapping[pen_key] = action

    for key, enabled in (button_toggles or {}).items():
        config.feature_toggles[key] = enabled

    for key, action in (button_actions or {}).items():
        config.action_mapping[key] = action


# Initial window geometry (F3 2026-11-16, maintainer 150%-scaling report):
# the window opens at the size the content wants (his manual resize measured
# ~870x1600) shrunk to fit the PRIMARY MONITOR WORK AREA (taskbar excluded)
# and never larger than it. SPI_GETWORKAREA reports the work area in THIS
# process's DPI space - the same coordinate space tkinter window geometry
# uses - so the fit honors display scaling without manual DPI math.
SPI_GETWORKAREA = 0x0030
WINDOW_NEEDED_W = 870
WINDOW_NEEDED_H = 1600
WINDOW_WORKAREA_FILL = 0.7  # GUI-B B3 (maintainer 2026-11-16): 0.9 opened
# the window too big; settled at 70% of the work area (he asked ~66%).


def initial_window_geometry(work_area, needed_w=WINDOW_NEEDED_W,
                            needed_h=WINDOW_NEEDED_H,
                            fill=WINDOW_WORKAREA_FILL):
    """Pure geometry math: work area (left, top, width, height) in, initial
    (width, height, x, y) out - all ints, deterministic, no tkinter. The
    wanted size is capped to `fill` of the work area (never larger than
    it) and CENTERED in it (integer floor division)."""
    left, top, area_w, area_h = work_area
    width = int(min(float(needed_w), area_w * fill) + 0.5)
    height = int(min(float(needed_h), area_h * fill) + 0.5)
    x = left + (area_w - width) // 2
    y = top + (area_h - height) // 2
    return (width, height, x, y)


def primary_work_area():
    """Primary monitor work area (left, top, w, h) via SPI_GETWORKAREA.
    None when the API is missing or reports junk (the caller falls back to
    the tkinter screen metrics)."""
    try:
        rect = ctypes.wintypes.RECT()
        if not ctypes.windll.user32.SystemParametersInfoW(
                SPI_GETWORKAREA, 0, ctypes.byref(rect), 0):
            return None
        width = rect.right - rect.left
        height = rect.bottom - rect.top
        if width <= 0 or height <= 0:
            return None
        return (rect.left, rect.top, width, height)
    except Exception:
        return None


class SettingsWindow(tk.Toplevel):
    def __init__(self, parent, state_ref, config_ref, device_info=None):
        super().__init__(parent)
        self.state = state_ref
        self.config = config_ref
        # {"connection": conn dict, "base_config": global config, "manager":
        # manager} when the driver runs (U383: main() always passes this
        # bundle; connection may be None and the section adopts the first
        # live device); a None bundle keeps the window on plain global
        # settings.
        self.device_info = device_info
        self.title("Wacom Touch Driver Settings")
        # F3 2026-11-16 (maintainer 150%-scaling report): the fixed 460x650
        # hid most tab content - open at the needed size fit into the
        # PRIMARY MONITOR WORK AREA (junk or missing read falls back to the
        # tkinter primary-screen metrics; never larger than the work area).
        area = primary_work_area() or (0, 0, self.winfo_screenwidth(),
                                       self.winfo_screenheight())
        win_w, win_h, win_x, win_y = initial_window_geometry(area)
        self.geometry(f"{win_w}x{win_h}+{win_x}+{win_y}")
        # The usability floor follows the computed size on degenerate hosts
        # so the never-larger-than-the-work-area promise holds there too.
        self.minsize(min(460, win_w), min(480, win_h))

        # Shared numeric-entry registry: Motion, Pen and Advanced tabs fill this dict.
        self.entries = {}
        # Mapping values as they were when the window opened, keyed by feature.
        # Initialized here so the Pen tab entries survive the Features tab build.
        self.saved_actions = {}
        # Pen button comboboxes, filled by the Pen tab.
        self.pen_combos = {}
        # Button/dial toggles and comboboxes (U71), filled by the Buttons tab.
        self.button_vars = {}
        self.button_combos = {}
        # Touch-part toggles and modifier comboboxes (U381 / #381), also filled
        # by the Buttons tab; the part defaults to off.
        self.touch_vars = {}
        self.touch_combos = {}

        main_container = ttk.Frame(self)
        main_container.pack(fill="both", expand=True, padx=10, pady=10)

        # Focused-device controls (U73): alias assignment and subset copy.
        # U383: main() always passes the bundle, so the section is present
        # even when no device had connected at launch.
        if device_info is not None:
            self._build_device_bar(main_container)

        notebook = ttk.Notebook(main_container)
        notebook.pack(fill="both", expand=True)

        # Tab 1: Motion & Acceleration
        self.tab_motion = ttk.Frame(notebook)
        notebook.add(self.tab_motion, text="Motion & Acceleration")
        self._build_motion_tab()

        # Tab 2: Pen
        self.tab_pen = ttk.Frame(notebook)
        notebook.add(self.tab_pen, text="Pen")
        self._build_pen_tab()

        # Tab 3: Touch Buttons & Rotary Dial
        self.tab_buttons = ttk.Frame(notebook)
        notebook.add(self.tab_buttons, text="Buttons")
        self._build_buttons_tab()

        # Tab 4: Combined Features & Actions
        self.tab_features_actions = ttk.Frame(notebook)
        notebook.add(self.tab_features_actions, text="Features & Actions")
        self._build_features_actions_tab()

        # Tab 5: Thresholds & Advanced
        self.tab_advanced = ttk.Frame(notebook)
        notebook.add(self.tab_advanced, text="Thresholds & Advanced")
        self._build_advanced_tab()

        # Bottom Button Bar
        btn_frame = ttk.Frame(main_container)
        btn_frame.pack(fill="x", side="bottom", pady=(10, 0))

        ttk.Button(btn_frame, text="Close", command=self.destroy).pack(side="right", padx=(5, 0))
        ttk.Button(btn_frame, text="Save to File", command=self.save_to_file).pack(side="right", padx=5)
        ttk.Button(btn_frame, text="Apply Live", command=self.apply_settings).pack(side="right")

    def _build_device_bar(self, container):
        # U383: a missing launch connection is no reason to skip the section;
        # focus the first live device from the manager, else none. The focused
        # device becomes the editing target too -- the same config the
        # launch-connection path passes in as config_ref -- on EVERY build, so
        # reopens from the tray-adopted bundle bind the device config as well
        # (reviewer P0: a first-build-only rebind falls back to the half-focus
        # on the second open).
        conn = self.device_info.get("connection")
        if conn is None:
            live = self._live_connections()
            conn = live[0] if live else None
            self.device_info["connection"] = conn
        conn = self._adopt_stale_focus()
        if conn is not None:
            self.config = conn["config"]
        base = self.device_info["base_config"]
        frame = ttk.LabelFrame(container, text=" Focused Device ", padding=8)
        frame.pack(fill="x", pady=(0, 6))
        self._focused_device_frame = frame  # U424r3 R3-L5 lane parent pin

        # Live device list (U87 a): the combo feeds on the running connections.
        ttk.Label(frame, text="Device:").grid(row=0, column=0, sticky="w")
        self.devices_combo = ttk.Combobox(frame, width=18, state="readonly")
        self.devices_combo.grid(row=0, column=1, sticky="w", padx=(4, 0))
        self.devices_combo.bind("<<ComboboxSelected>>", self._on_device_selected)
        refresh_btn = ttk.Button(frame, text="Refresh", width=8,
                                 command=self._refresh_device_combo)
        refresh_btn.grid(row=0, column=2, padx=(6, 0))
        ToolTip(refresh_btn, "Re-read the live device list from the running driver.")

        self.device_label = ttk.Label(
            frame, text=device_display_name(conn) if conn else NO_DEVICE_LABEL)
        self.device_label.grid(row=1, column=0, columnspan=4, sticky="w")

        ttk.Label(frame, text="Alias:").grid(row=2, column=0, sticky="w")
        self.alias_entry = ttk.Entry(frame, width=16)
        self.alias_entry.insert(0, (conn.get("alias") or "") if conn else "")
        self.alias_entry.grid(row=2, column=1, sticky="w", padx=(4, 0))
        btn = ttk.Button(frame, text="Assign alias", command=self.assign_alias)
        btn.grid(row=2, column=2, padx=(6, 0))
        ToolTip(btn, "Name this device; the name shows in the device list and "
                     "persists in the settings file under its identity key. "
                     "Tags the wireless receiver twin manually as well.")

        ttk.Label(frame, text="Copy settings to:").grid(row=3, column=0, sticky="w", pady=(6, 0))
        self.copy_alias_combo = ttk.Combobox(frame, values=sorted(base.devices), width=16)
        self.copy_alias_combo.grid(row=3, column=1, sticky="w", padx=(4, 0))
        self.copy_section_vars = {
            name: tk.BooleanVar(value=True) for name in DEVICE_TRANSFER_SECTIONS}
        section_labels = {
            "motion_and_thresholds": "Motion",
            "feature_toggles": "Toggles",
            "action_mapping": "Mapping",
        }
        for idx, (name, var) in enumerate(self.copy_section_vars.items(), start=3):
            chk = ttk.Checkbutton(frame, text=section_labels[name], variable=var)
            chk.grid(row=idx, column=2, sticky="w")
        copy_btn = ttk.Button(frame, text="Copy to alias", command=self.copy_settings_to_alias)
        copy_btn.grid(row=3, column=3, sticky="e")
        ToolTip(copy_btn, "Copies the selected sections of this device's settings into "
                          "the chosen alias; takes effect on that device's next connection.")

        device_btn = ttk.Button(frame, text="Save to Device",
                                command=self.save_to_focused_device)
        device_btn.grid(row=6, column=0, columnspan=2, sticky="w", pady=(8, 0))

        # D1 (ruling R4-a): identity-entry-wins stays the config rule; this
        # hint states WHICH sections the focused device overrides right now,
        # computed from the base config on every focus/apply/save refresh.
        self.identity_hint_label = ttk.Label(frame, text="", wraplength=400,
                                             justify="left")
        # Row 7: rows 3-5 belong to the copy-lane checkbuttons (row 5 was
        # the reviewer P1 overlap), row 6 is the button row; the wireless
        # lane moved to row 8 below.
        self.identity_hint_label.grid(row=7, column=0, columnspan=4, sticky="w",
                                      pady=(6, 0))
        self._refresh_identity_hint()

        # D1: restore-to-defaults removes the focused device identity entry
        # (config values only; geometry and naming lanes stay, pen_area_maps
        # untouched) and re-forks the live connection from base.
        self.reset_defaults_btn = ttk.Button(
            frame, text="Restore to Defaults",
            command=self.reset_focused_device_defaults)
        self.reset_defaults_btn.grid(row=6, column=2, sticky="w", pady=(8, 0))
        ToolTip(self.reset_defaults_btn, "Clear this device identity entry "
                  "overrides (config values only: pen area maps and aliases "
                  "are NOT touched) and re-apply global values live; the file "
                  "takes the change on the next save.")

        # U424r3 R3-L5 (#424 item 5): the wireless size lane lives HERE,
        # inside Focused Device, visible only for the receiver (id 132).
        self._build_wireless_lane(frame)

        # U424r4 R4-C (#424 item C): poll the live device list; a swap
        # while the window is open now reaches the adopt->rebind->sync
        # chain without waiting for a manual Refresh click.
        self._device_poll_ms = 5000
        self.after(self._device_poll_ms, self._device_poll_tick)
        ToolTip(device_btn, "Save ONLY this device's current values under its "
                            "identity key; the main Save button stays global.")
        frame.columnconfigure(3, weight=1)
        self._refresh_device_combo()

    def _build_wireless_lane(self, parent):
        """Wireless Tablet Size lane (U386 / #385), MOVED out of the Pen tab
        into the Focused Device section (U424r3 R3-L5 / #424 item 5): it
        belongs to the receiver identity, not to pen movement, and it is
        shown ONLY while the focused device is the wireless receiver."""
        wireless_frame = ttk.LabelFrame(
            parent, text=" Wireless Tablet Size (receiver id 132) ", padding=10)
        self._pen_wireless_frame = wireless_frame  # U424r3 pin name kept
        self.wireless_size_notice = tk.Label(
            wireless_frame, text="", wraplength=400, justify="left",
            foreground="#a00000")
        self.wireless_size_notice.grid(row=0, column=0, columnspan=2,
                                       sticky="w", pady=(0, 6))
        size_lbl = ttk.Label(wireless_frame, text="Tablet size:")
        size_lbl.grid(row=1, column=0, sticky="w", pady=4)
        self.wireless_size_combo = ttk.Combobox(
            wireless_frame, values=(WIRELESS_SIZE_UNSET_LABEL,) + WIRELESS_TABLET_SIZES,
            state="readonly", width=12)
        self.wireless_size_combo.set(
            getattr(self.config, "wireless_tablet_size", None) or WIRELESS_SIZE_UNSET_LABEL)
        self.wireless_size_combo.grid(row=1, column=1, sticky="e", pady=4)
        wireless_tip = ("Extents preset for the tablet behind the wireless receiver "
                        "(product id 132, paired size not detectable): small = 788, "
                        "medium = 789, large = 791. Unset shows a loud notice - there "
                        "is no silent default. A change applies at the next device "
                        "reconnect.")
        ToolTip(size_lbl, wireless_tip)
        ToolTip(self.wireless_size_combo, wireless_tip)
        wireless_frame.columnconfigure(1, weight=1)
        self._sync_wireless_lane()

    def _sync_wireless_lane(self):
        """Dynamic visibility (#424 item 5): render the lane ONLY when the
        focused connection is the wireless receiver (id 132); hide and
        refresh the combo echo on every focus/rebind change."""
        frame = getattr(self, "_pen_wireless_frame", None)
        if frame is None:
            return
        conn = self.device_info.get("connection") or {}
        is_wireless = conn.get("product_id") == WIRELESS_RECEIVER_PRODUCT_ID
        packed = frame.winfo_manager() == "grid"
        if is_wireless and not packed:
            frame.grid(row=8, column=0, columnspan=4, sticky="w", pady=(8, 0))
        elif not is_wireless and packed:
            frame.grid_forget()
        if hasattr(self, "wireless_size_combo"):
            self._set_widget_value(
                self.wireless_size_combo,
                getattr(self.config, "wireless_tablet_size", None)
                or WIRELESS_SIZE_UNSET_LABEL)
        self._refresh_wireless_notice()

    def _adopt_stale_focus(self):
        """U424r4 R4-C (#424 item C): a stored focus that is no longer in
        the live list (cabled unplugged, tray bundle stale at reopen) is
        ADOPTED-REPLACED by the first live device - mirroring the
        U383 dropped-focus adoption, but at build time too, so a
        cabled->wireless swap can never leave a dead focus hiding the
        wireless picker. Returns the (possibly new) focused connection."""
        conn = self.device_info.get("connection")
        live = self._live_connections()
        if live and conn is not None and not any(conn is c for c in live):
            conn = live[0]
            self.device_info["connection"] = conn
            sync_lane = getattr(self, "_sync_wireless_lane", None)
            if sync_lane is not None:  # mid-build the lane does not exist yet
                sync_lane()
        return conn

    def _device_poll_tick(self):
        """U424r4 R4-C: the missed hook - device swaps while the window is
        open produced NO event anywhere; this poll re-runs the (already
        complete) refresh->adopt->rebind->lane-sync chain periodically."""
        if not self.winfo_exists():
            return
        try:
            self._refresh_device_combo()
        finally:
            self.after(self._device_poll_ms, self._device_poll_tick)

    def _live_connections(self):
        """The running device list; the focused connection when no manager is wired."""
        manager = self.device_info.get("manager")
        if manager is not None:
            conns = manager.list_connections()
            if conns:
                return conns
        conn = self.device_info.get("connection")
        return [conn] if conn is not None else []

    def _refresh_device_combo(self):
        """Rebuild the combo from the live device list, keeping the focus."""
        conns = self._live_connections()
        self._device_conns = conns
        labels = [device_display_name(conn) for conn in conns]
        self.devices_combo["values"] = labels
        focused = self.device_info.get("connection")
        index = -1
        for i, candidate in enumerate(conns):
            if candidate is focused:
                index = i
                break
        if index >= 0:
            self.devices_combo.current(index)
        elif labels:
            # U383: the focused connection dropped -- adopt the first live
            # device so combo, label, editing config and save lanes agree.
            # Reached from window build, the Refresh button and assign_alias's
            # internal refresh; the toast names the new focus like a manual
            # combo selection does (reviewer P2: silent adoption).
            self.device_info["connection"] = conns[0]
            self.devices_combo.current(0)
            self._rebind_focused_device()
            ToastNotification(self, "Device Focused",
                              f"Editing settings of {device_display_name(conns[0])}.",
                              timeout_ms=1500)

    def _on_device_selected(self, event=None):
        """Point the window and its widgets at the selected live device."""
        conns = getattr(self, "_device_conns", [])
        index = self.devices_combo.current()
        if index < 0 or index >= len(conns):
            return False
        conn = conns[index]
        if conn is self.device_info["connection"]:
            return False
        self.device_info["connection"] = conn
        self._rebind_focused_device()
        ToastNotification(self, "Device Focused",
                          f"Editing settings of {device_display_name(conn)}.",
                          timeout_ms=1500)
        return True

    def _rebind_focused_device(self):
        conn = self.device_info["connection"]
        self.config = conn["config"]
        self.alias_entry.delete(0, "end")
        self.alias_entry.insert(0, conn.get("alias") or "")
        self.device_label.configure(text=device_display_name(conn))
        self._rebind_config_widgets()
        # Duck-typed rebind harnesses (FakeDeviceWindow) carry no hint.
        refresh_hint = getattr(self, "_refresh_identity_hint", None)
        if refresh_hint is not None:
            refresh_hint()
        # U424r3 R3-L5: dynamic lane on focus change; duck-typed rebind
        # harnesses (FakeDeviceWindow) carry neither the lane nor the sync.
        sync_lane = getattr(self, "_sync_wireless_lane", None)
        if sync_lane is not None:
            sync_lane()

    @staticmethod
    def _set_widget_value(widget, text):
        """Write a value into an Entry, Combobox or variable-like widget."""
        if hasattr(widget, "delete") and hasattr(widget, "insert"):
            widget.delete(0, "end")
            widget.insert(0, text)
        elif hasattr(widget, "set"):
            widget.set(text)

    def _rebind_config_widgets(self):
        """Reload every tab widget from the now-focused device's config."""
        config = self.config
        options = getattr(config, "AVAILABLE_ACTIONS", [])
        for key, ent in self.entries.items():
            value = getattr(config, key, None)
            if value is not None:
                self._set_widget_value(ent, str(value))
        for name, var in getattr(self, "feature_vars", {}).items():
            if name in config.feature_toggles:
                self._set_widget_value(var, config.feature_toggles[name])
        for name, var in (("cursor_acceleration", getattr(self, "cursor_accel_var", None)),
                          ("scroll_acceleration", getattr(self, "scroll_accel_var", None))):
            if var is not None and name in config.feature_toggles:
                self._set_widget_value(var, config.feature_toggles[name])
        for key, var in getattr(self, "button_vars", {}).items():
            if key in config.feature_toggles:
                self._set_widget_value(var, config.feature_toggles[key])
        pen_mode_var = getattr(self, "pen_mode_var", None)
        if pen_mode_var is not None:
            self._set_widget_value(pen_mode_var, resolved_pen_mode(config))
            self._sync_mode_panels()
        # U424 UNIT VERDICT P1: keep the subset checkboxes honest on
        # refresh (alias/focus change can swap the effective config).
        for rec, var in getattr(self, "stretch_screen_vars", {}).items():
            stored = getattr(config, "pen_stretch_screens", None)
            var.set((not stored) or rec in stored)
        self.saved_actions = dict(config.action_mapping)
        for combo_map in (getattr(self, "action_combos", {}),
                          getattr(self, "pen_combos", {}),
                          getattr(self, "button_combos", {})):
            for key, combo in combo_map.items():
                self._set_widget_value(
                    combo, action_display_value(config.action_mapping.get(key), options))
        orientation_widget = getattr(self, "orientation_widget", None)
        if orientation_widget is not None and hasattr(orientation_widget, "draw_degrees"):
            # U415 / #407: the live-apply refresh re-resolves the drawing SIZE
            # too (a Pen-tab wireless size change lands without a window
            # rebuild - same #386 apply-live shape as the orientation angle).
            conn = (getattr(self, "device_info", None) or {}).get("connection") or {}
            orientation_widget.size = resolve_drawing_size(
                conn.get("product_id"),
                getattr(config, "wireless_tablet_size", None))
            orientation_widget.draw_degrees(config.orientation_degrees)
        reorient = getattr(self, "_reorient_pen_editor", None)
        if reorient is not None:
            reorient()

    def _reorient_pen_editor(self):
        """Re-sync the rect editor to the current product + orientation
        (U424r4 R4-B). Called from the rebind loop AND the Apply success
        path - the pen reads config live, so the editor must too."""
        editor = getattr(self, "pen_area_editor", None)
        if editor is None:
            return
        conn = (getattr(self, "device_info", None) or {}).get("connection") or {}
        editor.reorient(pen_specs_for_product(conn.get("product_id"),
                                              self.config),
                        self.config.orientation_degrees)
        # The numeric echo (mm rows unchanged by box, but the sensitivity
        # w-axis basis and any future edit follow the new box) must
        # refresh with it.
        self._sync_rect_entries()

    def assign_alias(self):
        """Name the focused device: stores the alias under its identity key (U87 a)."""
        alias = self.alias_entry.get().strip()
        if not alias:
            messagebox.showerror("Invalid Alias", "Enter an alias name before assigning it.")
            return False
        conn = self.device_info.get("connection")
        if conn is None:
            messagebox.showerror(
                "No Device Focused",
                "Wake the device and pick it in the device list first.")
            return False
        base = self.device_info["base_config"]
        conn["alias"] = alias
        identity = conn.get("identity")
        if identity is not None:
            base.set_device_alias(identity, alias)
        # The binding lets this product id resolve to the alias on the next
        # connection; the receiver twin is never auto-aliased.
        base.set_device_entry(alias, {}, bindings=[conn["product_id"]])
        self.device_label.configure(text=device_display_name(conn))
        self._refresh_device_combo()
        ToastNotification(self, "Alias Assigned",
                          f"Device named '{alias}'. Save to persist.",
                          timeout_ms=1800)
        return True

    def copy_settings_to_alias(self):
        """Transfer a selectable subset of the focused device's settings.

        Writes the subset under the target alias; applies to that device on
        its next connection.
        """
        if not self.apply_settings():
            return False
        target = self.copy_alias_combo.get().strip()
        if not target:
            messagebox.showerror("Invalid Target", "Pick the alias to copy settings into.")
            return False
        selected = [name for name, var in self.copy_section_vars.items() if var.get()]
        if not selected:
            messagebox.showerror("Nothing Selected", "Select at least one section to copy.")
            return False
        conn = self.device_info.get("connection")
        if conn is None:
            messagebox.showerror(
                "No Device Focused",
                "Wake the device and pick it in the device list first.")
            return False
        base = self.device_info["base_config"]
        entry = dict(base.devices.get(target, {}))
        entry.update(collect_config_sections(conn["config"], selected))
        base.devices[target] = entry
        ToastNotification(self, "Settings Copied",
                          f"Copied {len(selected)} section(s) to '{target}'. Save to persist.",
                          timeout_ms=1800)
        return True

    def _build_motion_tab(self):
        canvas = tk.Canvas(self.tab_motion, borderwidth=0)
        scrollbar = ttk.Scrollbar(self.tab_motion, orient="vertical", command=canvas.yview)
        scroll_frame = ttk.Frame(canvas)

        scroll_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas_window = canvas.create_window((0, 0), window=scroll_frame, anchor="nw")
        canvas.bind("<Configure>", lambda e: canvas.itemconfig(canvas_window, width=e.width))
        canvas.configure(yscrollcommand=scrollbar.set)

        # --- 0. Device Orientation Frame (U74 c/d) ---
        orient_frame = ttk.LabelFrame(scroll_frame, text=" Device Orientation ", padding=10)
        orient_frame.pack(fill="x", padx=10, pady=8, expand=True)

        orient_lbl = ttk.Label(orient_frame, text="Rotation (degrees):")
        orient_lbl.grid(row=0, column=0, sticky="w", pady=4)
        orient_ent = ttk.Entry(orient_frame, width=8)
        orient_ent.insert(0, str(self.config.orientation_degrees))
        orient_ent.grid(row=0, column=1, sticky="w", padx=(4, 0))
        self.entries["orientation_degrees"] = orient_ent
        orient_ent.bind("<KeyRelease>", self._on_orientation_entry)
        # F1 2026-11-16: leaving the field rewrites the normalized angle
        # into it (no silent ignore - the displayed value is the stored one).
        orient_ent.bind("<FocusOut>", self._normalize_orientation_entry)
        orient_tip = ("Rotates the interpretation of touch and pen input clockwise "
                      "by this angle around the tablet center. 0 leaves input unchanged. "
                      "Values wrap by 360 (370 becomes 10, -10 becomes 350); "
                      "fractions are allowed.")
        ToolTip(orient_lbl, orient_tip)
        ToolTip(orient_ent, orient_tip)

        preset_frame = ttk.Frame(orient_frame)
        preset_frame.grid(row=0, column=2, columnspan=2, sticky="e", padx=(8, 0))
        for preset in ORIENTATION_PRESETS:
            preset_btn = ttk.Button(preset_frame, text=f"{preset}°", width=4,
                                    command=lambda angle=preset: self._set_orientation(angle))
            preset_btn.pack(side="left", padx=(3, 0))
            ToolTip(preset_btn, f"Set the orientation preset {preset} degrees.")

        # U415 / #407: the drawing is sized by the SELECTED tablet size (the
        # receiver lane falls back through the user's Pen-tab choice).
        # Plain (no-device) windows carry no device_info: the drawing then
        # falls back to medium, the driver's own default size.
        conn = (getattr(self, "device_info", None) or {}).get("connection") or {}
        self.orientation_widget = OrientationWidget(
            orient_frame, degrees=self.config.orientation_degrees,
            size=resolve_drawing_size(conn.get("product_id"),
                                      getattr(self.config, "wireless_tablet_size", None)))
        self.orientation_widget.grid(row=1, column=0, columnspan=4, pady=(6, 0))
        orient_frame.columnconfigure(1, weight=1)

        # Tooltips dictionary for motion & acceleration
        cursor_tooltips = {
            "cursor_deadzone": "Minimum pixel delta per packet required before cursor moves. Filters out sensor noise/jitter.",
            "cursor_min_sens": "Base precision multiplier applied at slow finger speeds.",
            "cursor_max_sens": "Maximum speed cap multiplier applied when reaching high finger speeds.",
            "cursor_speed_low": "Finger speed threshold (px/packet) below which base precision multiplier is used.",
            "cursor_speed_high": "Finger speed threshold (px/packet) at which maximum acceleration multiplier is reached."
        }

        scroll_tooltips = {
            "scroll_deadzone": "Minimum pixel delta per packet before scrolling activates. Prevents unwanted drift.",
            "scroll_min_sens": "Base scrolling speed multiplier for slow, precise scrolling.",
            "scroll_max_sens": "Maximum scrolling speed multiplier during fast flicks.",
            "scroll_speed_low": "Lower speed limit (px/packet) for flat precision scrolling.",
            "scroll_speed_high": "Upper speed limit (px/packet) to reach maximum scroll acceleration.",
            "scroll_sensitivity_x": "Horizontal scroll multiplier. (Positive = Natural, Negative = Traditional)",
            "scroll_sensitivity_y": "Vertical scroll multiplier. (Positive = Natural, Negative = Traditional)"
        }

        # --- 1. Cursor Acceleration Frame ---
        cursor_frame = ttk.LabelFrame(scroll_frame, text=" Cursor Acceleration Parameters ", padding=10)
        cursor_frame.pack(fill="x", padx=10, pady=8, expand=True)

        # Feature toggle moved here
        self.cursor_accel_var = tk.BooleanVar(value=self.config.feature_toggles.get("cursor_acceleration", True))
        chk_cursor = ttk.Checkbutton(cursor_frame, text="Enable Cursor Acceleration", variable=self.cursor_accel_var)
        chk_cursor.grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 6))
        ToolTip(chk_cursor, "Toggle non-linear piecewise acceleration curve for cursor movement.")

        cursor_fields = [
            ("Deadzone (px):", "cursor_deadzone", self.config.cursor_deadzone),
            ("Min Sens (Precision Base):", "cursor_min_sens", self.config.cursor_min_sens),
            ("Max Sens (Hard Cap):", "cursor_max_sens", self.config.cursor_max_sens),
            ("Speed Low (px/packet):", "cursor_speed_low", self.config.cursor_speed_low),
            ("Speed High (px/packet):", "cursor_speed_high", self.config.cursor_speed_high),
        ]

        for idx, (label_text, key, initial_val) in enumerate(cursor_fields, start=1):
            lbl = ttk.Label(cursor_frame, text=label_text)
            lbl.grid(row=idx, column=0, sticky="w", pady=4)
            ent = ttk.Entry(cursor_frame, width=12)
            ent.insert(0, str(initial_val))
            ent.grid(row=idx, column=1, sticky="e", pady=4)
            self.entries[key] = ent

            if key in cursor_tooltips:
                ToolTip(lbl, cursor_tooltips[key])
                ToolTip(ent, cursor_tooltips[key])

        cursor_frame.columnconfigure(1, weight=1)

        # --- 2. Scroll Acceleration Frame ---
        scroll_frame_ui = ttk.LabelFrame(scroll_frame, text=" Scrolling & Acceleration Parameters ", padding=10)
        scroll_frame_ui.pack(fill="x", padx=10, pady=8, expand=True)

        # Feature toggle moved here
        self.scroll_accel_var = tk.BooleanVar(value=self.config.feature_toggles.get("scroll_acceleration", True))
        chk_scroll = ttk.Checkbutton(scroll_frame_ui, text="Enable Scroll Acceleration", variable=self.scroll_accel_var)
        chk_scroll.grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 6))
        ToolTip(chk_scroll, "Toggle non-linear piecewise acceleration curve for 2-finger scrolling.")

        scroll_fields = [
            ("Deadzone (px):", "scroll_deadzone", self.config.scroll_deadzone),
            ("Min Sens (Precision Base):", "scroll_min_sens", self.config.scroll_min_sens),
            ("Max Sens (Hard Cap):", "scroll_max_sens", self.config.scroll_max_sens),
            ("Speed Low (px/packet):", "scroll_speed_low", self.config.scroll_speed_low),
            ("Speed High (px/packet):", "scroll_speed_high", self.config.scroll_speed_high),
            ("Sensitivity Horizontal (X):", "scroll_sensitivity_x", self.config.scroll_sensitivity_x),
            ("Sensitivity Vertical (Y):", "scroll_sensitivity_y", self.config.scroll_sensitivity_y),
        ]

        for idx, (label_text, key, initial_val) in enumerate(scroll_fields, start=1):
            lbl = ttk.Label(scroll_frame_ui, text=label_text)
            lbl.grid(row=idx, column=0, sticky="w", pady=4)
            ent = ttk.Entry(scroll_frame_ui, width=12)
            ent.insert(0, str(initial_val))
            ent.grid(row=idx, column=1, sticky="e", pady=4)
            self.entries[key] = ent

            if key in scroll_tooltips:
                ToolTip(lbl, scroll_tooltips[key])
                ToolTip(ent, scroll_tooltips[key])

        scroll_frame_ui.columnconfigure(1, weight=1)

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

    def _on_orientation_entry(self, event=None):
        """Live-redraw the pictogram while typing a valid angle."""
        degrees = parse_orientation_degrees(self.entries["orientation_degrees"].get())
        if degrees is not None:
            self.orientation_widget.draw_degrees(degrees)

    def _normalize_orientation_entry(self, event=None):
        """F1 2026-11-16: after editing, the field DISPLAYS the normalized
        angle (370 -> 10, -10 -> 350, 360 -> 0; fractions kept). Invalid
        text stays untouched - Apply refuses it in the validation style."""
        entry = self.entries["orientation_degrees"]
        degrees = parse_orientation_degrees(entry.get())
        if degrees is None:
            return
        text = str(degrees)
        if entry.get() != text:
            entry.delete(0, "end")
            entry.insert(0, text)
        self.orientation_widget.draw_degrees(degrees)

    def _set_orientation(self, degrees):
        """Preset button: write the angle into the entry and redraw the widget."""
        entry = self.entries["orientation_degrees"]
        entry.delete(0, "end")
        entry.insert(0, str(degrees))
        self.orientation_widget.draw_degrees(degrees)

    # --- U424 / #424 tri-modal handlers (rulings 1-3) ----------------------

    def _on_pen_mode_select(self):
        # Ruling 3: switching modes applies LIVE (set_pen_mode, the
        # reader re-resolves per report) and never touches the stored
        # 1:1 rect - it stays, inactive, instantly re-usable.
        self.config.set_pen_mode(self.pen_mode_var.get())
        self._sync_mode_panels()

    def _sync_mode_panels(self):
        # U424r3 R3-L1 (#424 item 1): panels pack with an explicit
        # `after=` anchor so a mode cycle can never re-append them at the
        # tab bottom (his bug); the speed row is relative-mode-only.
        mode = self.pen_mode_var.get()
        for panel, visible in ((self.stretch_screen_panel,
                                mode == "stretched"),
                               (self.pen_rect_panel, mode == "rect_1to1")):
            packed = panel.winfo_manager() == "pack"  # not pack_info:
            if visible and not packed:
                panel.pack(fill="x", padx=10, pady=(0, 8), expand=True,
                           after=self._pen_movement_frame)
            elif not visible and packed:
                panel.pack_forget()
        show_speed = mode == "relative"
        for widget, row, col, span, sticky in self._pen_speed_widgets:
            if show_speed and widget.winfo_manager() != "grid":
                widget.grid(row=row, column=col, columnspan=span,
                            sticky=sticky, pady=4)
            elif not show_speed and widget.winfo_manager() == "grid":
                widget.grid_remove()

    def _on_stretch_screens_change(self):
        # Ruling 1: the checkboxes RESTRICT the stretch target; all
        # selected persists as None (= the pre-U424 full-desktop lane).
        selected = [rec for rec, var in self.stretch_screen_vars.items()
                    if var.get()]
        self.config.pen_stretch_screens = (
            selected if len(selected) < len(self.stretch_screen_vars) else None)

    def _sync_rect_entries(self):
        editor = getattr(self, "pen_area_editor", None)
        model = editor.model if editor is not None else None
        if model is None:
            return
        dpi = virtual_desktop_dpi()
        rect = model.rect
        for i, key in enumerate("xywh"):
            axis = i % 2
            self.rect_entries[f"{key}_px"].delete(0, tk.END)
            self.rect_entries[f"{key}_px"].insert(0, f"{rect[i]:.0f}")
            self.rect_entries[f"{key}_mm"].delete(0, tk.END)
            self.rect_entries[f"{key}_mm"].insert(
                0, f"{rect[i] * pen_mm_per_px(dpi, axis):.1f}")
        self.rect_entries["s_px"].delete(0, tk.END)
        self.rect_entries["s_px"].insert(
            0, f"{rect[2] / model.box_mm[0]:.4f}")

    def _apply_rect_field(self, source_key):
        # Ruling 2: px and mm entries are bidirectional, aspect-locked
        # (height edits re-derive width). PENAREA: the model enforces the
        # placement rule (the rect keeps intersecting the display area -
        # #467), NOT the old inside-the-union clamp - negative and
        # beyond-union entries are accepted and echoed back when clamped.
        editor = getattr(self, "pen_area_editor", None)
        model = editor.model if editor is not None else None
        if model is None:
            return
        field, unit = source_key.rsplit("_", 1)
        entry = self.rect_entries[source_key]
        try:
            raw = float(entry.get().strip().replace(",", "."))
        except ValueError:
            self._sync_rect_entries()  # revert the bad edit
            return
        if raw != raw or raw in (float("inf"), float("-inf")):
            # U424r3 reviewer P2-2: float() accepts literal "nan"/"inf";
            # non-finite entries revert like any bad edit (never reach the
            # model).
            self._sync_rect_entries()
            return
        if field == "s":  # sensitivity handle (U424r3 R3-L3)
            model.apply_scale(raw)
            model.retarget_view()  # PENAREA: keep the handle in reach
            editor._redraw()
            self._sync_rect_entries()
            return
        dpi = virtual_desktop_dpi()
        axis = 1 if field in ("y", "h") else 0
        value_px = raw / pen_mm_per_px(dpi, axis) if unit == "mm" else raw
        if field == "h":  # aspect lock: height drives width through the box
            value_px = value_px * model.box_mm[0] / model.box_mm[1]
            field = "w"
        model.apply_fields(**{field: value_px})
        model.retarget_view()  # PENAREA: keep the handle in reach
        editor._redraw()
        self._sync_rect_entries()

    def _build_pen_tab(self):
        canvas = tk.Canvas(self.tab_pen, borderwidth=0)
        scrollbar = ttk.Scrollbar(self.tab_pen, orient="vertical", command=canvas.yview)
        scroll_frame = ttk.Frame(canvas)
        self._pen_scroll_frame = scroll_frame  # U424r3: pack-order pin target

        scroll_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas_window = canvas.create_window((0, 0), window=scroll_frame, anchor="nw")
        canvas.bind("<Configure>", lambda e: canvas.itemconfig(canvas_window, width=e.width))
        canvas.configure(yscrollcommand=scrollbar.set)

        pen_tooltips = {
            "pen_speed": "Relative-mode speed: pixels per millimeter of pen travel. Ignored in absolute mode.",
            "pen_pressure_threshold": "Pressure units (0..2047) at which the left button is held. Below it the button releases; filters light-contact jitter.",
            "pen_move_flush_hz": "Caps how often pen cursor updates reach Windows (default 250; lower only to test congestion behavior). Applies to the absolute modes; the relative lane is unaffected.",
        }

        # --- 1. Movement Frame ---
        movement_frame = ttk.LabelFrame(scroll_frame, text=" Pen Movement Parameters ", padding=10)
        movement_frame.pack(fill="x", padx=10, pady=8, expand=True)

        # U424 / TODO #424 (rulings 1-3): ONE mode selector replaces the
        # old absolute checkbox (RETIRED - no dual control, reviewer
        # P2-2 closure). Contextual sub-panels below: stretched -> screen
        # checkboxes (default ALL = today); rect_1to1 -> numeric fields +
        # canvas editor. Switching a mode applies LIVE.
        self.pen_mode_var = tk.StringVar(value=resolved_pen_mode(self.config))
        self.pen_mode_radios = []
        mode_rows = (
            ("relative", "Relative (movement deltas, speed below)"),
            ("stretched", "Absolute - stretched / full fill"),
            ("rect_1to1", "Absolute - 1:1 pen area rectangle"),
        )
        for idx, (mode_key, mode_label) in enumerate(mode_rows, start=1):
            rb = ttk.Radiobutton(movement_frame, text=mode_label,
                                 value=mode_key, variable=self.pen_mode_var,
                                 command=self._on_pen_mode_select)
            rb.grid(row=idx, column=0, columnspan=2, sticky="w", pady=(0, 2))
            self.pen_mode_radios.append(rb)
        ToolTip(self.pen_mode_radios[0],
                "Relative: movement is sent as deltas scaled by the speed entry. "
                "Stretched: the whole pen box fills the selected screens. "
                "1:1: the pen box maps onto a stored rectangle at one px/mm.")

        # U424r3 R3-L1 (#424 item 1): the Speed row is RELATIVE-mode-only
        # (contextual); Pressure Threshold is mode-independent. Widgets
        # carry their grid spec so _sync_mode_panels can hide/show rows.
        self._pen_speed_widgets = []
        pen_fields = [
            ("Speed (px/mm):", "pen_speed", self.config.pen_speed, 4),
            ("Pressure Threshold:", "pen_pressure_threshold", self.config.pen_pressure_threshold, 5),
            # Mode-independent (like the pressure row): the flush cap governs
            # the ABSOLUTE-lane slot, and it stays editable in every mode so
            # the value never hides while a mode switch is in progress.
            ("Move flush Hz:", "pen_move_flush_hz", self.config.pen_move_flush_hz, 6),
        ]

        for label_text, key, initial_val, row_idx in pen_fields:
            lbl = ttk.Label(movement_frame, text=label_text)
            lbl.grid(row=row_idx, column=0, sticky="w", pady=4)
            if key == "pen_move_flush_hz":
                # Bounded spinbox (the maintainer's ask for this knob): the
                # arrow range mirrors parse_move_flush_hz, the Apply Live
                # check, so neither lane can step out of range.
                ent = ttk.Spinbox(movement_frame, from_=MOVE_FLUSH_HZ_MIN,
                                  to=MOVE_FLUSH_HZ_MAX, increment=10, width=12)
            else:
                ent = ttk.Entry(movement_frame, width=12)
            ent.insert(0, str(initial_val))
            ent.grid(row=row_idx, column=1, sticky="e", pady=4)
            self.entries[key] = ent
            if key == "pen_speed":
                self._pen_speed_widgets = [(lbl, row_idx, 0, 1, "w"),
                                            (ent, row_idx, 1, 1, "e")]

            if key in pen_tooltips:
                ToolTip(lbl, pen_tooltips[key])
                ToolTip(ent, pen_tooltips[key])

        movement_frame.columnconfigure(1, weight=1)
        self._pen_movement_frame = movement_frame

        # --- 1b. Stretched-mode screen subset panel (U424 / ruling 1) ---
        # Checkboxes RESTRICT the stretch target: unselected screens are
        # not reachable, the box fills the union of the selected ones.
        # Default = ALL selected = the pre-U424 full-desktop behavior.
        self.stretch_screen_panel = ttk.LabelFrame(
            scroll_frame, text=" Screens to stretch over ", padding=8)
        self.stretch_screen_vars = {}
        # U424 UNIT VERDICT P1: seed FROM the stored subset (None/empty =
        # all) - the UI must show the actual reach on open, not all-checked.
        stored_subset = getattr(self.config, "pen_stretch_screens", None)
        for record in monitor_records():
            rec_var = tk.BooleanVar(
                value=(not stored_subset) or record in stored_subset)
            cb = ttk.Checkbutton(
                self.stretch_screen_panel, text=_screen_label(record),
                variable=rec_var, command=self._on_stretch_screens_change)
            cb.pack(anchor="w")
            self.stretch_screen_vars[record] = rec_var

        # --- 1c. Pen Area Rectangle panel (U414 / #406 + U424 ruling 2) ---
        # 1:1 writing mode only: numeric x/y/w/h in px AND mm (editing
        # either updates the other, aspect-locked, clamped to the display
        # union) above the canvas editor; Save/Remove live in here.
        self.pen_rect_panel = ttk.LabelFrame(
            scroll_frame, text=" Pen Area Rectangle (1:1 writing area) ", padding=8)
        self.rect_entries = {}
        fields_frame = ttk.Frame(self.pen_rect_panel)  # grid lives here,
        fields_frame.pack(anchor="w")                  # editor packs beside
        for col, (field_key, field_label) in enumerate(
                (("x", "X"), ("y", "Y"), ("w", "Width"), ("h", "Height"))):
            ttk.Label(fields_frame, text=field_label).grid(
                row=0, column=col, padx=4)
            px_entry = ttk.Entry(fields_frame, width=9)
            px_entry.grid(row=1, column=col, padx=4, pady=(0, 2))
            mm_entry = ttk.Entry(fields_frame, width=9)
            mm_entry.grid(row=2, column=col, padx=4)
            px_entry.bind("<Return>", lambda e, k=f"{field_key}_px": self._apply_rect_field(k))
            px_entry.bind("<FocusOut>", lambda e, k=f"{field_key}_px": self._apply_rect_field(k))
            mm_entry.bind("<Return>", lambda e, k=f"{field_key}_mm": self._apply_rect_field(k))
            mm_entry.bind("<FocusOut>", lambda e, k=f"{field_key}_mm": self._apply_rect_field(k))
            self.rect_entries[f"{field_key}_px"] = px_entry
            self.rect_entries[f"{field_key}_mm"] = mm_entry
        # U424r3 R3-L4 (#424 item 4): HONEST BASIS LABEL. The px values
        # (and the fractional-rect basis + the pen mapping) all live in
        # the DPI-VIRTUALIZED desktop space: this process declares no DPI
        # awareness, so GetSystemMetrics AND MOUSEEVENTF_VIRTUALDESK both
        # address the scaled space (a 2160-physical row at 150% reads
        # 1440). Display, rect basis and injection share ONE space - no
        # precision is lost; switching to physical pixels would move the
        # injection basis too and is deliberately NOT done.
        ttk.Label(fields_frame,
                  text="px = DPI-scaled desktop space (the same space the "
                       "pen injection addresses); mm = physical",
                  foreground="#888888").grid(row=4, column=0, columnspan=4,
                                             sticky="w", pady=(2, 0))
        # U424r3 R3-L3 (#424 item 3b): editable SENSITIVITY (px/mm) - the
        # derived handle s = rw/box_mm_w; editing it resizes the footprint
        # at fixed center (protocol DECISION: no schema change).
        ttk.Label(fields_frame, text="Sensitivity (px/mm):").grid(
            row=3, column=0, columnspan=2, sticky="w", padx=4, pady=(4, 0))
        s_entry = ttk.Entry(fields_frame, width=9)
        s_entry.grid(row=3, column=2, padx=4, pady=(4, 0))
        s_entry.bind("<Return>", lambda e: self._apply_rect_field("s_px"))
        s_entry.bind("<FocusOut>", lambda e: self._apply_rect_field("s_px"))
        self.rect_entries["s_px"] = s_entry
        self.pen_area_editor = PenAreaEditor(self.pen_rect_panel, self.config,
                                             self.device_info,
                                             on_change=self._sync_rect_entries)
        self.pen_area_editor.pack(fill="x", pady=(6, 0))
        self._sync_rect_entries()
        self._sync_mode_panels()


        # --- 3. Pen Button Mapping Frame ---
        button_frame = ttk.LabelFrame(scroll_frame, text=" Pen Button Mapping ", padding=10)
        button_frame.pack(fill="x", padx=10, pady=8, expand=True)

        options = getattr(self.config, "AVAILABLE_ACTIONS", [])
        button_fields = [
            ("Pen Button 1:", "pen_button_1",
             "Press/release action for pen button 1 (status bit 1)."),
            ("Pen Button 2:", "pen_button_2",
             "Press/release action for pen button 2 (status bit 2). Mouse-button actions hold while pressed; other actions tap on the press edge."),
        ]

        for idx, (label_text, key, tip) in enumerate(button_fields, start=1):
            lbl = ttk.Label(button_frame, text=label_text)
            lbl.grid(row=idx, column=0, sticky="w", pady=4)
            current_action = self.config.action_mapping.get(key)
            combo = ttk.Combobox(button_frame, values=options, state="readonly", width=22)
            combo.set(action_display_value(current_action, options))
            combo.grid(row=idx, column=1, sticky="e", pady=4)
            self.pen_combos[key] = combo
            self.saved_actions[key] = current_action
            ToolTip(lbl, tip)
            ToolTip(combo, tip)

        button_frame.columnconfigure(1, weight=1)

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

    def _wireless_receiver_live(self):
        """True when a wireless receiver (product id 132) connection is live."""
        device_info = getattr(self, "device_info", None)
        if device_info is None:
            return False
        conns = []
        conn = device_info.get("connection")
        if conn is not None:
            conns.append(conn)
        manager = device_info.get("manager")
        if manager is not None:
            conns.extend(manager.list_connections())
        return any(c.get("product_id") == WIRELESS_RECEIVER_PRODUCT_ID for c in conns)

    def _refresh_wireless_notice(self):
        """Show the loud unset notice while a receiver is live and the size is unset.

        U386 / TODO #385 planner ruling: the non-detecting receiver must
        never run on a silent default; the notice names all three choices.
        """
        unset = getattr(self.config, "wireless_tablet_size", None) is None
        show = unset and self._wireless_receiver_live()
        notice = self.wireless_size_notice
        notice.configure(text=WIRELESS_SIZE_NOTICE_TEXT if show else "")
        # U424r5 R5-F (#424 item F): an empty label still asks one line
        # of grid height - UNMANAGE the row when there is no text, so no
        # empty residue row stays above the tablet-size picker.
        managed = notice.winfo_manager() == "grid"
        if show and not managed:
            notice.grid(row=0, column=0, columnspan=2, sticky="w",
                        pady=(0, 6))
        elif not show and managed:
            notice.grid_remove()

    def _build_buttons_tab(self):
        canvas = tk.Canvas(self.tab_buttons, borderwidth=0)
        scrollbar = ttk.Scrollbar(self.tab_buttons, orient="vertical", command=canvas.yview)
        scroll_frame = ttk.Frame(canvas)

        scroll_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas_window = canvas.create_window((0, 0), window=scroll_frame, anchor="nw")
        canvas.bind("<Configure>", lambda e: canvas.itemconfig(canvas_window, width=e.width))
        canvas.configure(yscrollcommand=scrollbar.set)

        button_tooltips = {
            "btn1_press": "Action on the press edge of express key 1 (byte 4 bitmask bit 0). "
                          "Mouse-button actions hold while pressed; other actions tap once.",
            "central_press": "Action on the press edge of the central button (byte 3 bit 0).",
            "dial_cw": "Action per clockwise step of the rotary touch dial. Steps accumulate "
                       "from the reported dial angle; a decreasing angle is clockwise.",
            "dial_ccw": "Action per counter-clockwise step of the rotary touch dial "
                        "(increasing angle).",
        }

        # --- 1. Dial Parameters Frame ---
        dial_frame = ttk.LabelFrame(scroll_frame, text=" Rotary Dial Parameters ", padding=10)
        dial_frame.pack(fill="x", padx=10, pady=8, expand=True)

        dial_fields = [
            ("Step Threshold (angle units):", "dial_step_threshold", self.config.dial_step_threshold),
        ]
        dial_tooltips = {
            "dial_step_threshold": "Dial angle units traversed before one CW/CCW step action fires.",
        }

        for idx, (label_text, key, initial_val) in enumerate(dial_fields, start=1):
            lbl = ttk.Label(dial_frame, text=label_text)
            lbl.grid(row=idx, column=0, sticky="w", pady=4)
            ent = ttk.Entry(dial_frame, width=12)
            ent.insert(0, str(initial_val))
            ent.grid(row=idx, column=1, sticky="e", pady=4)
            self.entries[key] = ent

            if key in dial_tooltips:
                ToolTip(lbl, dial_tooltips[key])
                ToolTip(ent, dial_tooltips[key])

        dial_frame.columnconfigure(1, weight=1)

        # --- 2. Button Mapping Frame ---
        mapping_frame = ttk.LabelFrame(scroll_frame, text=" Touch Button & Dial Mapping ", padding=10)
        mapping_frame.pack(fill="x", padx=10, pady=8, expand=True)

        options = getattr(self.config, "AVAILABLE_ACTIONS", [])
        control_fields = [(f"Button {n}:", f"btn{n}_press") for n in range(1, 9)] + [
            ("Central Button:", "central_press"),
            ("Dial Clockwise:", "dial_cw"),
            ("Dial Counter-Clockwise:", "dial_ccw"),
        ]

        for idx, (label_text, key) in enumerate(control_fields, start=1):
            lbl = ttk.Label(mapping_frame, text=label_text)
            lbl.grid(row=idx, column=0, sticky="w", pady=4)

            var = tk.BooleanVar(value=self.config.feature_toggles.get(key, True))
            chk = ttk.Checkbutton(mapping_frame, text="on", variable=var)
            chk.grid(row=idx, column=1, sticky="w", pady=4)
            self.button_vars[key] = var

            current_action = self.config.action_mapping.get(key)
            combo = ttk.Combobox(mapping_frame, values=options, state="readonly", width=22)
            combo.set(action_display_value(current_action, options))
            combo.grid(row=idx, column=2, sticky="e", pady=4)
            self.button_combos[key] = combo
            self.saved_actions[key] = current_action

            tip = button_tooltips.get(key, "Action for this touch control; toggle enables the control.")
            ToolTip(lbl, tip)
            ToolTip(chk, tip)
            ToolTip(combo, tip)

            touch_key = BUTTON_TOUCH_BY_PRESS.get(key)
            if touch_key is not None:
                # U381 / #381: touch part of this express key -- the modifier
                # held while the finger keeps contact, off by default.
                touch_tip = (f"Touch part of express key {touch_key[3]}: while a finger "
                             "keeps contact, this modifier is held down and it releases on "
                             "the full release. Off by default.")
                touch_var = tk.BooleanVar(value=self.config.feature_toggles.get(touch_key, False))
                touch_chk = ttk.Checkbutton(mapping_frame, text="touch", variable=touch_var)
                touch_chk.grid(row=idx, column=3, sticky="w", pady=4)
                self.touch_vars[touch_key] = touch_var

                current_touch = self.config.action_mapping.get(touch_key)
                touch_combo = ttk.Combobox(mapping_frame, values=list(TOUCH_ACTION_CHOICES),
                                           state="readonly", width=8)
                touch_combo.set(action_display_value(current_touch, TOUCH_ACTION_CHOICES))
                touch_combo.grid(row=idx, column=4, sticky="e", pady=4)
                self.touch_combos[touch_key] = touch_combo
                self.saved_actions[touch_key] = current_touch

                ToolTip(touch_chk, touch_tip)
                ToolTip(touch_combo, touch_tip)

        mapping_frame.columnconfigure(2, weight=1)
        mapping_frame.columnconfigure(4, weight=1)

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

    def _build_features_actions_tab(self):
        canvas = tk.Canvas(self.tab_features_actions, borderwidth=0)
        scrollbar = ttk.Scrollbar(self.tab_features_actions, orient="vertical", command=canvas.yview)
        scroll_frame = ttk.Frame(canvas)

        scroll_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas_window = canvas.create_window((0, 0), window=scroll_frame, anchor="nw")

        canvas.bind("<Configure>", lambda e: canvas.itemconfig(canvas_window, width=e.width))
        canvas.configure(yscrollcommand=scrollbar.set)

        self.feature_vars = {}
        self.action_combos = {}
        # self.saved_actions is initialized in __init__ so the Pen tab entries
        # built earlier are not wiped here.
        options = getattr(self.config, "AVAILABLE_ACTIONS", [])

        # Filter out cursor_acceleration & scroll_acceleration since they are in Motion tab,
        # pen_absolute_mode since it is in the Pen tab, and the button/dial controls
        # since they are in the Buttons tab.
        # U424 / #424: pen_absolute_mode AND pen_stretch_full_desktop are
        # retired from the UI (the mode selector owns them now; the
        # toggles stay readable in config for the migration only - no
        # dual control, UNIT VERDICT P2-2).
        excluded_features = ({"cursor_acceleration", "scroll_acceleration",
                              "pen_absolute_mode", "pen_stretch_full_desktop"}
                             | set(BUTTON_CONTROL_KEYS) | set(BUTTON_TOUCH_KEYS))

        for feature_key, enabled in self.config.feature_toggles.items():
            if feature_key in excluded_features:
                continue

            row_frame = ttk.Frame(scroll_frame)
            row_frame.pack(fill="x", padx=10, pady=4, expand=True)

            var = tk.BooleanVar(value=enabled)
            chk = ttk.Checkbutton(row_frame, text=feature_key, variable=var)
            chk.pack(side="left", anchor="w")
            self.feature_vars[feature_key] = var

            if feature_key in self.config.action_mapping:
                current_action = self.config.action_mapping[feature_key]
                combo = ttk.Combobox(row_frame, values=options, state="readonly", width=18)
                combo.set(action_display_value(current_action, options))
                combo.pack(side="right", anchor="e")
                self.action_combos[feature_key] = combo
                self.saved_actions[feature_key] = current_action

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

    def _build_advanced_tab(self):
        canvas = tk.Canvas(self.tab_advanced, borderwidth=0)
        scrollbar = ttk.Scrollbar(self.tab_advanced, orient="vertical", command=canvas.yview)
        scroll_frame = ttk.Frame(canvas)

        scroll_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas_window = canvas.create_window((0, 0), window=scroll_frame, anchor="nw")

        canvas.bind("<Configure>", lambda e: canvas.itemconfig(canvas_window, width=e.width))
        canvas.configure(yscrollcommand=scrollbar.set)

        advanced_tooltips = {
            "max_tap_duration": "Maximum touch-and-release time (in seconds) to trigger a tap action.",
            "double_tap_timeout": "Time window allowed between double taps to initiate a tap-drag.",
            "tap_max_movement": "Maximum allowed finger drift (in pixels) during a tap contact.",
            "press_hold_duration": "Touch duration required to convert a static contact into a held left-click.",
            "max_gesture_touch_session_duration": "Hard limit on touch session duration for multi-finger gesture classification.",
            "swipe_threshold_x": "Horizontal swipe distance (in Wacom units) required to execute a swipe gesture.",
            "swipe_threshold_y": "Vertical swipe distance (in Wacom units) required to execute a swipe gesture.",
            "axis_dominance_ratio": "Required ratio of primary axis movement vs cross-axis movement to confirm straight swipes.",
            "pinch_sensitivity": "Multiplier applied to the pinch zoom with 2 fingers.",
            "pinch_discrete_threshold": "Spread change distance needed to trigger discrete multi-finger pinch triggers.",
            "scroll_activation_threshold": "Centroid distance (px) required before 2-finger scrolling locks in.",
            "pinch_activation_threshold": "Finger spread change (px) required before pinch zoom locks in.",
            "alt_tab_activation_threshold": "Distance (px) required to open Alt-Tab overlay with 5 fingers.",
            "alt_tab_step_threshold": "Distance (px) traversed per application icon step in Alt-Tab.",
            "alt_tab_step_sensitivity": "Packet accumulation rate required to increment one step in Alt-Tab."
        }

        advanced_fields = [
            ("Max Tap Duration (s):", "max_tap_duration", self.config.max_tap_duration),
            ("Double Tap Timeout (s):", "double_tap_timeout", self.config.double_tap_timeout),
            ("Tap Max Movement (px):", "tap_max_movement", self.config.tap_max_movement),
            ("Press & Hold Duration (s):", "press_hold_duration", self.config.press_hold_duration),
            ("Gesture Session Duration (s):", "max_gesture_touch_session_duration", self.config.max_gesture_touch_session_duration),
            ("Swipe Threshold X (px):", "swipe_threshold_x", self.config.swipe_threshold_x),
            ("Swipe Threshold Y (px):", "swipe_threshold_y", self.config.swipe_threshold_y),
            ("Axis Dominance Ratio:", "axis_dominance_ratio", self.config.axis_dominance_ratio),
            ("Pinch Sensitivity:", "pinch_sensitivity", self.config.pinch_sensitivity),
            ("Pinch Threshold (Discrete):", "pinch_discrete_threshold", self.config.pinch_discrete_threshold),
            ("Scroll Activation Threshold:", "scroll_activation_threshold", self.config.scroll_activation_threshold),
            ("Pinch Activation Threshold:", "pinch_activation_threshold", self.config.pinch_activation_threshold),
            ("Alt-Tab Activation Threshold:", "alt_tab_activation_threshold", self.config.alt_tab_activation_threshold),
            ("Alt-Tab Step Threshold:", "alt_tab_step_threshold", self.config.alt_tab_step_threshold),
            ("Alt-Tab Step Sensitivity:", "alt_tab_step_sensitivity", self.config.alt_tab_step_sensitivity),
        ]

        frame = ttk.LabelFrame(scroll_frame, text=" Advanced Thresholds & Timings ", padding=10)
        frame.pack(fill="x", padx=10, pady=10, expand=True)

        for idx, (label_text, key, initial_val) in enumerate(advanced_fields):
            lbl = ttk.Label(frame, text=label_text)
            lbl.grid(row=idx, column=0, sticky="w", pady=4)
            ent = ttk.Entry(frame, width=12)
            ent.insert(0, str(initial_val))
            ent.grid(row=idx, column=1, sticky="e", pady=4)
            self.entries[key] = ent

            if key in advanced_tooltips:
                ToolTip(lbl, advanced_tooltips[key])
                ToolTip(ent, advanced_tooltips[key])

        frame.columnconfigure(1, weight=1)

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

    def apply_settings(self):
        """Apply the widget values to the live config; returns False on bad input."""
        try:
            # 1. Collect widget values only; config is untouched until validation passes
            numeric_values = {key: ent.get() for key, ent in self.entries.items()}
            accel_toggles = {
                "cursor_acceleration": self.cursor_accel_var.get(),
                "scroll_acceleration": self.scroll_accel_var.get(),
            }
            feature_toggles = {feat: var.get() for feat, var in self.feature_vars.items()}
            options = getattr(self.config, "AVAILABLE_ACTIONS", [])
            saved_actions = getattr(self, "saved_actions", {})
            action_mappings = {
                feat_key: resolve_action_mapping(
                    combo.get(),
                    saved_actions.get(feat_key, self.config.action_mapping.get(feat_key)),
                    options,
                )
                for feat_key, combo in self.action_combos.items()
            }
            pen_combos = getattr(self, "pen_combos", {})
            pen_button_actions = {
                pen_key: resolve_action_mapping(
                    combo.get(),
                    saved_actions.get(pen_key, self.config.action_mapping.get(pen_key)),
                    options,
                )
                for pen_key, combo in pen_combos.items()
            }
            pen_mode_var = getattr(self, "pen_mode_var", None)
            pen_mode = pen_mode_var.get() if pen_mode_var is not None else None
            button_combos = getattr(self, "button_combos", {})
            button_actions = {
                key: resolve_action_mapping(
                    combo.get(),
                    saved_actions.get(key, self.config.action_mapping.get(key)),
                    options,
                )
                for key, combo in button_combos.items()
            }
            button_vars = getattr(self, "button_vars", {})
            button_toggles = {key: var.get() for key, var in button_vars.items()}
            # U381 / #381: the touch part rides the same apply lane; its combo
            # resolves against the modifier vocabulary, not the action list.
            touch_combos = getattr(self, "touch_combos", {})
            button_actions.update({
                touch_key: resolve_action_mapping(
                    combo.get(),
                    saved_actions.get(touch_key, self.config.action_mapping.get(touch_key)),
                    TOUCH_ACTION_CHOICES,
                )
                for touch_key, combo in touch_combos.items()
            })
            touch_vars = getattr(self, "touch_vars", {})
            button_toggles.update({touch_key: var.get() for touch_key, var in touch_vars.items()})
            # U386 / TODO #385: the wireless tablet size rides the same apply
            # lane; the unset label means the explicit unset, not "keep".
            wireless_combo = getattr(self, "wireless_size_combo", None)
            if wireless_combo is None:
                wireless_tablet_size = _KEEP_WIRELESS_SIZE
            elif wireless_combo.get() == WIRELESS_SIZE_UNSET_LABEL:
                wireless_tablet_size = None
            else:
                wireless_tablet_size = wireless_combo.get()

            # 2. Validate everything, then apply all-or-nothing
            apply_settings_to_config(
                self.config, numeric_values, accel_toggles, feature_toggles, action_mappings,
                pen_button_actions=pen_button_actions,
                pen_mode=pen_mode,
                button_actions=button_actions,
                button_toggles=button_toggles,
                wireless_tablet_size=wireless_tablet_size,
            )
        except ValueError as e:
            messagebox.showerror("Invalid Input", f"Please enter valid numeric values.\nDetails: {e}")
            return False

        # U424r3 R3-L2 (#424 item 2): Apply Live is the SINGLE apply path -
        # the 1:1 rect rides it like every other setting (the editor's old
        # "Save Pen Area" button is gone). Duck-typed callers without the
        # editor lane are skipped.
        # U424r5 R5-E: reorient BEFORE persist so Apply Live stores the
        # orientation-matched shape (order matters: persist writes the
        # editor's current rect).
        reorient = getattr(self, "_reorient_pen_editor", None)
        if reorient is not None:
            reorient()
        editor = getattr(self, "pen_area_editor", None)
        if editor is not None:
            editor.persist_entry()

        # Refresh only when the window actually built the wireless lane:
        # duck-typed apply_settings callers (FakeWindow pins) carry neither
        # the combo nor the notice.
        if wireless_combo is not None:
            self._refresh_wireless_notice()
        # A3 / review Item 2: the applied values also go through to the focused
        # device's base-config entry, so a pause/resume/reconnect refork starts
        # from what the user sees now instead of from the pre-Apply values.
        # Duck-typed apply_settings callers (the FakeWindow pins) carry no
        # helper, so they keep today's fork-only behavior.
        write_through = getattr(self, "_write_through_focused_entry", None)
        if write_through is not None:
            write_through()

        # D1 hint stays honest: Apply/Save routes can create the entry.
        refresh_hint = getattr(self, "_refresh_identity_hint", None)
        if refresh_hint is not None:
            refresh_hint()
        ToastNotification(self, "Success", "Settings applied directly to driver!", timeout_ms=1500)
        return True

    def _write_through_focused_entry(self):
        """Store the live-applied values under the focused device's identity key.

        A3 (review Item 2): Apply Live used to reach the running connection's
        config fork only. A pause, a resume, or a reconnect rebuilt that fork
        from base_config, which still carried the pre-Apply values, so a change
        the user applied mid-session silently came back as the old one. Writing
        the same section subset the device Save button stores - and nothing else
        - makes every later refork of THAT identity inherit what is live now,
        while another device's entry stays untouched.

        Only the in-memory base config is written here: Apply Live stays the
        non-persisting path, the file still takes the Save button. Returns
        'written', or why nothing was written ('no_connection', 'no_identity',
        'no_base_config'), so callers keep their own messaging.
        """
        device_info = getattr(self, "device_info", None) or {}
        conn = device_info.get("connection")
        if conn is None:
            return "no_connection"
        base = device_info.get("base_config")
        if base is None:
            return "no_base_config"
        identity = conn.get("identity")
        if identity is None:
            return "no_identity"
        base.set_device_identity_settings(
            identity, collect_config_sections(conn["config"], DEVICE_TRANSFER_SECTIONS))
        return "written"

    def _focused_identity_override_sections(self):
        """D1 hint data: the config sections the focused identity overrides NOW.

        Computed from the base config on every call (never remembered), so the
        hint cannot drift from what the next fork actually applies.
        """
        device_info = getattr(self, "device_info", None) or {}
        conn = device_info.get("connection") or {}
        base = device_info.get("base_config")
        identity = conn.get("identity")
        if base is None or identity is None:
            return ()
        entry = base.device_settings.get(identity) or {}
        return tuple(sorted(name for name in DEVICE_SECTION_KEYS
                                  if name in entry))

    def _refresh_identity_hint(self):
        """D1 hint: which identity-entry overrides are active for the focus."""
        label = getattr(self, "identity_hint_label", None)
        if label is None:
            return
        sections = self._focused_identity_override_sections()
        if sections:
            text = "Device-specific values active: " + ", ".join(sections)
        else:
            text = "Device-specific values active: (none)"
        label.configure(text=text)

    def reset_focused_device_defaults(self):
        """D1: clear the focused device identity entry (config values only).

        Pen area maps (geometry) and device aliases (naming) are separate
        lanes and are never touched. The live connection re-forks from the
        base config IN PLACE, so the running readers keep going and resolve
        global values immediately (the pen-mode #424-ruling-3 live shape);
        the on-file state takes the removal at the next Save like any
        config change - the global file is not rewritten here.
        """
        device_info = getattr(self, "device_info", None) or {}
        conn = device_info.get("connection")
        if conn is None:
            messagebox.showerror(
                "No Device Focused",
                "Wake the device and pick it in the device list first.")
            return False
        base = device_info.get("base_config")
        if base is None:
            messagebox.showerror(
                "No Base Config",
                "This window carries no base config; nothing was reset.")
            return False
        identity = conn.get("identity")
        entry = (base.device_settings.get(identity) or {}) if identity else {}
        sections = [name for name in DEVICE_SECTION_KEYS if name in entry]
        if not sections:
            messagebox.showinfo(
                "Nothing to Restore",
                "This device runs global values already; no identity entry "
                "sections are active.")
            return False
        confirmed = messagebox.askyesno(
            "Restore Device Defaults",
            "Clear the device-specific config values: " + ", ".join(sections)
            + "?\n\n"
            "Config values only: pen area maps (geometry) and device aliases "
            "(naming) are NOT touched. The global file is NOT rewritten now; "
            "the removal takes effect on the next Save to File.")
        if not confirmed:
            return False
        # Remove the entry; keep any untouched lane (pen_area_maps is a
        # separate config map and never lives here).
        base.device_settings.pop(identity, None)
        # Live re-apply WITHOUT restarting readers: rebuild the fork from
        # base (identity=None overlay) and swap fields inside the live
        # config object, so every holder (dispatcher, reader, editor) sees
        # global values at the next lookup.
        fresh = base.fork_for_device(conn.get("alias"), None)
        live_cfg = conn["config"]
        for name, value in vars(fresh).items():
            setattr(live_cfg, name, value)
        live_cfg.sync_sensitivities()
        _log.info("[Device] Restored defaults for identity "
                  + str(identity) + " (cleared: " + ", ".join(sections) + ")")
        self._rebind_focused_device()
        ToastNotification(self, "Defaults Restored",
                          f"{device_display_name(conn)} runs global values.",
                          timeout_ms=1800)
        return True

    def save_to_focused_device(self):
        """Subset save (U87 e): only the focused device's values, under its key.

        The main Save button keeps writing the global sections; this button
        writes nothing else, so one device's values never leak into another.
        """
        if not self.apply_settings():
            return False

        status = self._write_through_focused_entry()
        if status == "no_connection":
            messagebox.showerror(
                "No Device Focused",
                "Wake the device and pick it in the device list first.")
            return False
        if status == "no_identity":
            messagebox.showerror(
                "No Device Identity",
                "This connection carries no identity key; nothing was saved.")
            return False
        if status != "written":
            messagebox.showerror(
                "No Base Config",
                "This window carries no base config; nothing was saved.")
            return False

        base = self.device_info["base_config"]
        conn = self.device_info["connection"]
        filepath = getattr(base, "save_path", "user.cfg")
        base.save_to_file(filepath)

        ToastNotification(self, "Device Saved",
                          f"Saved {device_display_name(conn)} under its identity key.",
                          timeout_ms=1800)
        return True

    def save_to_file(self):
        """Apply first; the file is written only when the apply succeeded.

        With a focused device the device settings are written into its
        devices entry under its alias; the global sections stay untouched.
        """
        if not self.apply_settings():
            return False

        device_info = getattr(self, "device_info", None)
        conn = device_info.get("connection") if device_info is not None else None
        if conn is not None:
            base = device_info["base_config"]
            alias = conn.get("alias")
            if alias is None:
                messagebox.showerror(
                    "No Device Alias",
                    "Assign an alias before saving per-device settings.")
                return False
            base.set_device_entry(alias, collect_config_sections(
                conn["config"], DEVICE_TRANSFER_SECTIONS))
            filepath = getattr(base, "save_path", "user.cfg")
            base.save_to_file(filepath)
        else:
            filepath = getattr(self.config, "save_path", "user.cfg")
            self.config.save_to_file(filepath)

        ToastNotification(self, "File Saved", f"Configuration saved to {filepath}", timeout_ms=1800)
        return True


class DriverTrayApp:
    # TRAY unit (#448): icon-state poll cadence for toggle-driven changes
    # (a button-mapped touch_toggle flips the flag off the reader thread).
    ICON_WATCH_MS = 500

    def __init__(self, root, state_ref, config_ref, enabled_event_ref, stop_event_ref, device_info=None):
        self.root = root
        self.state = state_ref
        self.config = config_ref
        self.device_info = device_info
        self.touch_enabled_event = enabled_event_ref
        self.stop_event = stop_event_ref
        self.icon = None
        self.settings_window = None
        self.icon_cache = IconCache(text="WT")
        self._icon_state_seen = None
        self.start_icon_watch()

    def toggle_touch(self, icon=None, item=None):
        if self.touch_enabled_event.is_set():
            self.touch_enabled_event.clear()
        else:
            self.touch_enabled_event.set()
        
        status = "Paused" if not self.touch_enabled_event.is_set() else "Active"
        print(f"[Tray] Touch output is now {status} "
              f"(touch_paused = {not self.touch_enabled_event.is_set()}; "
              "pen and tablet buttons stay live)")
        self.update_icon()

    def start_icon_watch(self):
        # TRAY unit (#448): the touch_toggle action can flip the shared
        # flag from a READER thread (mapped tablet button), and pystray
        # never re-reads the icon image on its own - so a slow Tk timer
        # polls the ONE flag and re-runs update_icon on change (the same
        # refresh the tray click uses; no second source of state).
        if self.root is None or not hasattr(self.root, "after"):
            return
        self._icon_state_seen = self.touch_enabled_event.is_set()
        self._tick_icon_watch()

    def _poll_icon_state(self):
        # One watch tick (test seam): refresh only on an actual change.
        seen = self.touch_enabled_event.is_set()
        if seen != self._icon_state_seen:
            self._icon_state_seen = seen
            self.update_icon()
            return True
        return False

    def _tick_icon_watch(self):
        # Reviewer P2 (wtd-tray-review): one failing update_icon (a pystray/
        # win32 hiccup) must not silently end the watcher chain - log it and
        # keep the tick alive; only a destroyed root ends it.
        try:
            self._poll_icon_state()
        except Exception as exc:
            print(f"[Tray] icon watch tick failed: {exc}")
        finally:
            try:
                self.root.after(self.ICON_WATCH_MS, self._tick_icon_watch)
            except tk.TclError:
                # Root destroyed on quit: the watcher dies with it.
                pass
        
    def is_touch_enabled(self, item=None):
        return self.touch_enabled_event.is_set()
    
    def update_icon(self):
        # TRAY unit (#448): the STOP state is TOUCH-OUTPUT-scoped now -
        # the title says so, so a stopped touch lane is never mistaken
        # for a dead tablet (pen and tablet buttons keep working).
        if self.icon:
            is_enabled = self.touch_enabled_event.is_set()
            self.icon.icon = self.icon_cache.get(is_enabled)
            self.icon.title = ("Wacom Touch Driver (Touch Enabled)" if is_enabled
                               else "Wacom Touch Driver (Touch Disabled - "
                                    "pen and tablet buttons stay live)")

    def open_settings(self, icon=None, item=None):
        self.root.after(0, self._show_settings_gui)

    def _show_settings_gui(self):
        if self.settings_window is not None and self.settings_window.winfo_exists():
            self.settings_window.lift()
            return
        self.settings_window = SettingsWindow(self.root, self.state, self.config, self.device_info)

    def quit_driver(self, icon=None, item=None):
        print("[Tray] Exiting Wacom Touch Driver...")
        self.stop_event.set()
        #self.state.set_running_global(False)
        # TRAY unit (#448): the retired touch-flag unblock hack - the
        # manager no longer waits on the flag (it only gates emissions),
        # so quitting with touch stopped needs no event fiddling.
        if self.icon:
            self.icon.stop()
        self.root.after(0, self.root.quit)

    def run_tray(self):
        menu = pystray.Menu(
            # #448 wording: OUTPUT vocabulary (pen and tablet buttons stay
            # live regardless), matching the tooltip, CHANGELOG, and README.
            item('Enable Touch Output', self.toggle_touch, checked=self.is_touch_enabled, default=True),
            item('Settings...', self.open_settings),
            pystray.Menu.SEPARATOR,
            item('Exit Driver', self.quit_driver)
        )

        initial_state = self.touch_enabled_event.is_set()
        self.icon = pystray.Icon(
            "WacomTouchDriver",
            self.icon_cache.get(initial_state),
            "Wacom Touch Driver",
            menu
        )
        # Reviewer P2 (wtd-tray-review): carry the touch-scoped title and
        # state color from the FIRST frame, not from the first change.
        self.update_icon()
        self.icon.run()


def launch_gui(state_ref, config_ref, pause_event_ref, stop_event_ref, device_info=None):
    root = tk.Tk()
    root.withdraw()

    app = DriverTrayApp(root, state_ref, config_ref, pause_event_ref, stop_event_ref,
                        device_info=device_info)
    tray_thread = threading.Thread(target=app.run_tray, daemon=True)
    tray_thread.start()

    root.mainloop()