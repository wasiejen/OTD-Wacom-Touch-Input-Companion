"""Orientation math, desktop metrics, pen-area selection, and the
pen cursor mapping leg moved from touch_controller."""

import time
import math
import os
import ctypes
import hashlib
from wtd.config import (resolved_pen_mode, union_rect_from_records)
from wtd.protocol import (PEN_BUTTON_1_MASK, PEN_BUTTON_2_MASK, PEN_REPORT_ID, PEN_REPORT_LENGTH, PEN_ROTATION_FLAG, PEN_STATUS_COORDS, PEN_STATUS_OUT_OF_RANGE, PEN_TOOL_REPORT_STATUS, PEN_VIRTUAL_RESOLUTION, decode_pen_position, decode_pen_pressure)
from wtd._log import logger as _log  # shared wtd logger (see wtd/_log.py)


             

# ==============================================================================
# SHARED ROTATION CONSTANTS (U74)
# ==============================================================================
# Defined here, before the config class: the module-level DriverConfig
# instance at the end of that class validates orientation_degrees, so the
# bounds constant must exist at import time.
# Touch sub-blocks decode to 0..4095 (parse_sub_block), so the touch rotation
# center is that square's middle. The pen rotation center is the digitizer
# center from the per-model ranges (PEN_SPECS_BY_PRODUCT / PTH-651).
TOUCH_COORD_MAX = 4095
TOUCH_ROTATION_CENTER = (TOUCH_COORD_MAX / 2, TOUCH_COORD_MAX / 2)

# ==============================================================================
# DEVICE ORIENTATION (U74) — shared rotation for touch and pen coordinates
# ==============================================================================
# The bounds and center constants (TOUCH_COORD_MAX, TOUCH_ROTATION_CENTER,
# ORIENTATION_MIN/MAX_DEGREES) live above the config class: the module-level
# DriverConfig instance validates orientation_degrees at import time.

def rotate_point(x, y, degrees, center_x, center_y):
    """Rotate a device coordinate about the center by whole degrees.

    One sin/cos matrix shared by both input paths; 1° precision, no quadrant
    quantization. Tablet coordinates have y pointing down, so a positive
    angle turns clockwise on screen. 0° is the identity passthrough and
    returns the input coordinates unchanged.
    """
    if degrees == 0:
        return (x, y)
    rad = math.radians(degrees)
    cos_a = math.cos(rad)
    sin_a = math.sin(rad)
    dx = x - center_x
    dy = y - center_y
    return (center_x + cos_a * dx - sin_a * dy,
            center_y + sin_a * dx + cos_a * dy)

def pen_rotation_center(pen_specs):
    """Pen rotation center: the tablet center from the per-model ranges."""
    return (pen_specs["max_x"] / 2, pen_specs["max_y"] / 2)

def apply_orientation(point, cfg, center):
    """Shared orientation transform: raw device coordinate in, rotated point out."""
    return rotate_point(point[0], point[1], cfg.orientation_degrees,
                        center[0], center[1])

def _abs_snap_cos_sin(degrees):
    """Whole-quadrant-snapped |cos|, |sin| of the orientation angle.

    One snap rule for both count extents and the physical box, so the two
    stay proportional for a uniform-density digitizer. 0° gives (1.0, 0.0).
    """
    if degrees == 0:
        return (1.0, 0.0)
    rad = math.radians(degrees)
    cos_a = abs(math.cos(rad))
    sin_a = abs(math.sin(rad))
    if cos_a > 1 - 1e-9:
        cos_a = 1.0
    elif cos_a < 1e-9:
        cos_a = 0.0
    if sin_a > 1 - 1e-9:
        sin_a = 1.0
    elif sin_a < 1e-9:
        sin_a = 0.0
    return (cos_a, sin_a)


def rotated_pen_extents(pen_specs, degrees):
    """Bounding box of the digitizer rectangle after the orientation rotation.

    The rotation about the tablet center swaps the extents of a non-square
    tablet at 90/270, so absolute mode must map THIS box (not the fixed
    max_x/max_y) onto the desktop, else the pen cursor covers only the inner
    band. Whole-quadrant cos/sin artifacts are snapped to exact 0/1 so the
    box edges land exactly on full extent. Returns (left, top, width, height);
    0° returns the untouched device rectangle.
    """
    if degrees == 0:
        return (0, 0, pen_specs["max_x"], pen_specs["max_y"])
    cos_a, sin_a = _abs_snap_cos_sin(degrees)
    width = pen_specs["max_x"] * cos_a + pen_specs["max_y"] * sin_a
    height = pen_specs["max_x"] * sin_a + pen_specs["max_y"] * cos_a
    cx = pen_specs["max_x"] / 2
    cy = pen_specs["max_y"] / 2
    return (cx - width / 2, cy - height / 2, width, height)


def rotated_pen_box_mm(pen_specs, degrees):
    """Physical mm size of the mapped (rotated) digitizer box.

    The mm counterpart of rotated_pen_extents with the SAME snap, so a
    uniform-density digitizer (every PEN_SPECS row runs exactly 200 counts/mm
    on both axes) keeps the mm box and the count box proportional. Returns
    (width_mm, height_mm); 0 deg returns the declared size.

    A grown (autoscaled) box keeps the DECLARED mm area: the pinned model of
    the safety net is that the extra counts spread over the same physical
    area, so mm-per-count falls with the growth -- the same model the relative
    lane uses and pins (test_relative_mode_uses_the_grown_box). Under that
    model one px-per-mm scale still covers both axes of a grown box, which is
    what pen_isotropic_absolute_projection delivers for it.
    """
    cos_a, sin_a = _abs_snap_cos_sin(degrees)
    return (pen_specs["width_mm"] * cos_a + pen_specs["height_mm"] * sin_a,
            pen_specs["height_mm"] * cos_a + pen_specs["width_mm"] * sin_a)


# ==============================================================================
# VIRTUAL DESKTOP GEOMETRY (U376 / #380)
# ==============================================================================
# MOUSEEVENTF_VIRTUALDESK stretches the 0..65535 absolute range over the
# virtual desktop's PIXEL extents (the same process-DPI space GetSystemMetrics
# reports, so the two sides use one metric). A mapping computed in that
# normalized space therefore keeps equal FRACTIONS of the screen per mm, not
# equal px/mm: on a 25:9 desktop the U344 "1:1 default" squashed the height by
# exactly the screen aspect (maintainer report 2026-11-06: a 45 deg pen line
# ran 22.7 deg and the top and bottom 3/16 of the display were unreachable).
# The isotropic default needs the desktop pixel extents at mapping time.
SM_CMONITORS = 2
SM_CXVIRTUALSCREEN = 7
SM_CYVIRTUALSCREEN = 8
SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77


def _get_system_metrics(index):
    """One system-metrics read, None when the host offers none."""
    try:
        return int(ctypes.windll.user32.GetSystemMetrics(index))
    except Exception:
        return None


# Junk guard: no real display mode is this small (the smallest shipped is
# 320x200), while a headless or service-session host can report positive
# nonsense -- a 3 x 3 pair was measured on this project's test host. Junk
# extents route the mapping to the documented stretch_unmeasured lane
# instead of letting it map against a bogus aspect.
MIN_DESKTOP_PX = 256


def virtual_desktop_metrics():
    """Virtual desktop bounds (left, top, width_px, height_px), None if none.

    Read-only system metrics -- no input event is ever sent here. None names
    the degraded mapping lane: a host without the metrics API, a read that
    returns a zero extent, or extents below MIN_DESKTOP_PX (positive junk).
    Both this read and MOUSEEVENTF_VIRTUALDESK run in the same DPI-awareness
    space of this process, so their ratio is consistent; a future change to
    the process DPI awareness must keep that parity.
    """
    left = _get_system_metrics(SM_XVIRTUALSCREEN)
    top = _get_system_metrics(SM_YVIRTUALSCREEN)
    width = _get_system_metrics(SM_CXVIRTUALSCREEN)
    height = _get_system_metrics(SM_CYVIRTUALSCREEN)
    if None in (left, top, width, height):
        return None
    if width < MIN_DESKTOP_PX or height < MIN_DESKTOP_PX:
        return None
    return (left, top, width, height)


def monitor_count():
    """Display-monitor count from the system metrics, None if unavailable."""
    return _get_system_metrics(SM_CMONITORS)


# Metrics readout diagnostic (U412 / TODO #412): opt-in ONE startup line
# naming the raw SM_*VIRTUALSCREEN returns (UNsanitized - junk pass-through
# is the point), SM_CMONITORS, the ctypes call path, the monitor count and
# the EnumDisplayMonitors union rect. Data for the #412 measured-fit ruling;
# no mapping change, default OFF prints nothing.

METRICS_DIAG_ENV = "WTD_METRICS_DIAG"
METRICS_DIAG_INDICES = (SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN,
                        SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN, SM_CMONITORS)


def metrics_diag_enabled():
    """True when the env gate names the readout (WTD_METRICS_DIAG=1)."""
    # Same accept shape as the pen-map gate (exact "1", no strip) - one
    # vocabulary for the two opt-in diagnostics (U412 STEP-1 P2-2).
    return os.environ.get(METRICS_DIAG_ENV, "") == "1"


def raw_system_metrics():
    """Raw GetSystemMetrics returns for the diag indices, None on raise.

    Deliberately bypasses the MIN_DESKTOP_PX junk guard (virtual_desktop_
    metrics): the readout must show the raw values a host really returns.
    """
    values = []
    for index in METRICS_DIAG_INDICES:
        try:
            values.append(ctypes.windll.user32.GetSystemMetrics(index))
        except Exception:
            values.append(None)
    return values


def metrics_readout_line():
    """The byte-stable one-line readout (U412)."""
    x, y, w, h, monitors = raw_system_metrics()
    records = monitor_records()
    union = union_rect_from_records(records)
    if union is None:
        union_text = "none"
    else:
        union_text = "%d,%d,%dx%d" % union
    return ("wtd_metrics_diag SM_XVIRTUALSCREEN=%s SM_YVIRTUALSCREEN=%s "
            "SM_CXVIRTUALSCREEN=%s SM_CYVIRTUALSCREEN=%s SM_CMONITORS=%s "
            "call=user32.GetSystemMetrics monitors=%d union=%s"
            % (x, y, w, h, monitors, len(records), union_text))


def log_metrics_diagnostic():
    """Print the readout once when the env gate is on; silent otherwise."""
    if metrics_diag_enabled():
        _log.info(metrics_readout_line())


# --- display identity (U414 / #406, approved proposal §2) ---------------------
# The pen-area map stores one rectangle per display configuration. The
# configuration identity is a sha1 over the sorted monitor records of
# EnumDisplayMonitors/GetMonitorInfoW, in the same process-DPI awareness
# space as the virtual metrics read above (virtual_desktop_metrics docstring).

class _RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class _MONITORINFOEXW(ctypes.Structure):
    _fields_ = [("cb", ctypes.c_ulong),
                ("rcMonitor", _RECT),
                ("rcWork", _RECT),
                ("dwFlags", ctypes.c_ulong),
                ("szDevice", ctypes.c_wchar * 32)]


def monitor_records():
    """One record per monitor '<szDevice>|<left>|<top>|<W>x<H>', [] on failure.

    U414 (#406): callers treat an empty list as 'no display identity' and
    fall back to the isotropic default; the walk never raises.
    """
    records = []

    def _callback(h_monitor, _hdc, _rect, _lparam):
        info = _MONITORINFOEXW()
        info.cb = ctypes.sizeof(_MONITORINFOEXW)
        if not ctypes.windll.user32.GetMonitorInfoW(h_monitor, ctypes.byref(info)):
            return True
        left, top, right, bottom = (info.rcMonitor.left, info.rcMonitor.top,
                                    info.rcMonitor.right, info.rcMonitor.bottom)
        records.append(f"{info.szDevice}|{left}|{top}|{right - left}x{bottom - top}")
        return True

    try:
        # U414 STEP-1 P2-2: pin the signatures so monitor handles keep their
        # full 64-bit width on Win64 (default int coercion would truncate).
        get_info = ctypes.windll.user32.GetMonitorInfoW
        get_info.argtypes = [ctypes.c_void_p, ctypes.POINTER(_MONITORINFOEXW)]
        get_info.restype = ctypes.c_bool
        enum_monitors = ctypes.windll.user32.EnumDisplayMonitors
        enum_monitors.argtypes = [ctypes.c_void_p, ctypes.POINTER(_RECT),
                                  ctypes.c_void_p, ctypes.c_long]
        enum_monitors.restype = ctypes.c_bool
        proc = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p,
                                  ctypes.POINTER(_RECT), ctypes.c_void_p)(_callback)
        enum_monitors(None, None, proc, 0)
    except Exception:
        return []
    return records


def display_key_for_records(records):
    """Display configuration identity: sha1 hex over sorted records joined by newlines."""
    return hashlib.sha1("\n".join(sorted(records)).encode("utf-8")).hexdigest()


def select_pen_area_entry(lane, display_key):
    """First entry whose display_key matches exactly; None when none does.

    U414 load rule: exact full-configuration match only; a stale entry for a
    different monitor set is never selected (the isotropic default takes
    over), even though the entry stays stored for the next return."""
    if not isinstance(lane, dict):
        return None
    for entry in lane.get("entries", []):
        if isinstance(entry, dict) and entry.get("display_key") == display_key:
            return entry
    return None


def current_pen_area_entry(config, identity):
    """The stored pen-area rectangle for the live display configuration, else None."""
    records = monitor_records()
    if not records:
        return None
    return select_pen_area_entry(config.pen_area_maps.get(identity),
                                 display_key_for_records(records))


# Consumer-side selection for the per-report pen path (U414 / #406). The
# monitor walk is memoized behind a short TTL so a running reader does not
# enumerate displays every report; a GUI save takes effect within the TTL
# (the #386 Apply Live shape for this feature).

PEN_AREA_KEY_TTL_SECONDS = 2.0
_pen_area_key_cache = {"key": None, "at": None}


def reset_pen_area_key_cache():
    """Empty the display-key TTL cache (test seam and post-change hook)."""
    _pen_area_key_cache["key"] = None
    _pen_area_key_cache["at"] = None


def cached_display_key(now=None):
    """Display key with a TTL; None (enumeration failed) is cached too."""
    if now is None:
        now = time.monotonic()
    if (_pen_area_key_cache["at"] is not None
            and now - _pen_area_key_cache["at"] < PEN_AREA_KEY_TTL_SECONDS):
        return _pen_area_key_cache["key"]
    records = monitor_records()
    key = display_key_for_records(records) if records else None
    _pen_area_key_cache["key"] = key
    _pen_area_key_cache["at"] = now
    return key


def virtual_desktop_dpi():
    """(dpi_x, dpi_y) of the primary DC for the rect editor's mm fields
    (U424 / #424 ruling 2). Never raises: falls back to 96/96 (Windows
    default scaling) when the DC calls fail."""
    try:
        hdc = ctypes.windll.user32.GetDC(0)
        dpi_x = ctypes.windll.gdi32.GetDeviceCaps(hdc, 88)   # LOGPIXELSX
        dpi_y = ctypes.windll.gdi32.GetDeviceCaps(hdc, 90)   # LOGPIXELSY
        ctypes.windll.user32.ReleaseDC(0, hdc)
        if dpi_x > 0 and dpi_y > 0:
            return float(dpi_x), float(dpi_y)
    except Exception:
        pass
    return 96.0, 96.0


def pen_mm_per_px(dpi, axis=0):
    """Millimetres per pixel for the numeric rect fields (U424 ruling 2)."""
    return 25.4 / dpi[axis]


def select_live_pen_area_rect(config, identity):
    """The FRACTIONAL rect for the live identity + display config, else None.

    U416 / #406: returns rect_frac (0..1 of the union bounds); the
    normalized 0..65535 pen space spans the same virtual bounds
    (MOUSEEVENTF_VIRTUALDESK), so consumers need no display metrics.
    """
    if identity is None:
        return None
    key = cached_display_key()
    if key is None:
        return None
    entry = select_pen_area_entry(config.pen_area_maps.get(identity), key)
    if entry is None:
        return None
    return list(entry["rect_frac"])


def pen_area_rect_aspect_lock(rect_px, box_mm_w, box_mm_h):
    """Return the rect with rh re-derived so rw/rh == box_mm_w/box_mm_h.

    U414 / #406: the editor handle and the default rect both run through
    here, so isotropy holds BY CONSTRUCTION in the pen_area lane (L2's
    px/mm pin depends on this aspect); rx/ry/rw are passed through.
    """
    rx, ry, rw, _ = rect_px
    return [rx, ry, rw, int(round(rw * box_mm_h / box_mm_w))]


def pen_area_norm_projection(rect_px, extent_w, extent_h, screen_w, screen_h):
    """Normalized-space offset/scale of the pen-area rect (proposal §2 formula).

    off_x = rx * 65535 / screen_w places the box edge at rect px rx;
    scale_x = rw * 65535 / (extent_w * screen_w) spans rw px for the full
    rotated count extent; same per axis for y.

    Datum (U414 STEP-2 P1): rx/ry are pixels RELATIVE TO THE VIRTUAL
    DESKTOP ORIGIN - the (left, top) of the metrics bounding rect, i.e.
    MOUSEEVENTF_VIRTUALDESK's own zero - not absolute screen coordinates.

    U416 / #406: the pen path passes FRACTIONAL rects (0..1 of the union
    bounds) with screen_w = screen_h = 1.0 - the same form, fractions
    pre-divided; the normalized space IS the virtual-desk space, so no
    display metrics are consulted for the mapping.
    """
    rx, ry, rw, rh = rect_px
    return (rx * PEN_VIRTUAL_RESOLUTION / screen_w,
            rw * PEN_VIRTUAL_RESOLUTION / (extent_w * screen_w),
            ry * PEN_VIRTUAL_RESOLUTION / screen_h,
            rh * PEN_VIRTUAL_RESOLUTION / (extent_h * screen_h))


PEN_MAP_DIAG_ENV = "WTD_PEN_MAP_DIAG"


def pen_mapping_diag_enabled():
    """Env gate for the one-shot pen mapping diagnostic line (U376 leg 1)."""
    return os.environ.get(PEN_MAP_DIAG_ENV, "") == "1"


def pen_isotropic_absolute_projection(extent_w, extent_h, box_mm_w, box_mm_h,
                                      screen_w_px, screen_h_px):
    """Isotropic centered absolute mapping in the 0..65535 normalized space.

    ONE px-per-mm scale = min(screen_w_px / box_mm_w, screen_h_px / box_mm_h):
    the digitizer box FITS the desktop instead of one axis stretching past the
    other, and it is centered, so each unreachable band is symmetric and a
    45 deg pen stroke maps at 45 deg (U376 fix; the opt-in stretch lane keeps
    the per-axis anisotropic fill).

    Returns (scale_px_per_mm, used_px_w, used_px_h, norm_scale_x, norm_scale_y,
             norm_off_x, norm_off_y): the pixel footprint plus the normalized
    coefficients of the consumer formula
    nx = int(norm_off_x + (device_x - left) * norm_scale_x + 1e-9).
    """
    scale = min(screen_w_px / box_mm_w, screen_h_px / box_mm_h)
    used_w = box_mm_w * scale
    used_h = box_mm_h * scale
    norm_off_x = (screen_w_px - used_w) / 2.0 * PEN_VIRTUAL_RESOLUTION / screen_w_px
    norm_off_y = (screen_h_px - used_h) / 2.0 * PEN_VIRTUAL_RESOLUTION / screen_h_px
    norm_scale_x = used_w * PEN_VIRTUAL_RESOLUTION / (extent_w * screen_w_px)
    norm_scale_y = used_h * PEN_VIRTUAL_RESOLUTION / (extent_h * screen_h_px)
    return (scale, used_w, used_h, norm_scale_x, norm_scale_y,
            norm_off_x, norm_off_y)

def pen_effective_specs(pen_specs, state):
    """The declared digitizer box grown to the observed coordinate range.

    Defensive safety net (U344, kept under the U370 authoritative OTD
    extents): per axis the larger of the declared extent and the largest
    coordinate observed in this session -- GROW-ONLY, it can never shrink
    below the corrected declared extents (pinned by
    test_autoscale_floor_cannot_shrink_below_declared_extents). The pen
    consumers map THIS box. A model reporting inside its declared box
    returns the input dict unchanged (identity).
    """
    if state.pen_obs_max_x <= pen_specs["max_x"] and \
            state.pen_obs_max_y <= pen_specs["max_y"]:
        return pen_specs
    grown = dict(pen_specs)
    grown["max_x"] = max(pen_specs["max_x"], state.pen_obs_max_x)
    grown["max_y"] = max(pen_specs["max_y"], state.pen_obs_max_y)
    return grown

def release_pen_inputs(state, action_dispatch):
    """Release every held pen input and reset the relative origin."""
    ad = action_dispatch
    ad.release_pen_pressure()
    for slot in list(ad.pen_button_held):
        ad.release_pen_button(slot)
    state.pen_position = None
    state.pen_pressure = 0
    state.pen_buttons = (False, False)

def _log_pen_mapping_diagnostic(state, config, pen_specs, metrics, mode,
                                extent_w, extent_h, px_mm, bands):
    """One-shot named mapping snapshot of the absolute lane (U376 leg 1).

    Byte-stable key=value line, gated by env WTD_PEN_MAP_DIAG=1 and printed
    once per connection: it names the branch that executed, the declared
    extents with the product id, the mapped count and mm box, the autoscale
    observed range, the desktop geometry with its monitor count, and the
    px-per-mm scale of each axis plus its unreachable band. The mapping
    itself is untouched by this line.
    """
    box_mm_w, box_mm_h = rotated_pen_box_mm(pen_specs, config.orientation_degrees)
    if px_mm is not None and bands is not None:
        scales = "px_mm_x=%.4f px_mm_y=%.4f band_x=%.2f_px band_y=%.2f_px" % (
            px_mm[0], px_mm[1], bands[0], bands[1])
    elif px_mm is not None:
        # U414 pen_area lane: a user-placed rect has no contain-fit band.
        scales = "px_mm_x=%.4f px_mm_y=%.4f band_x=na band_y=na" % (
            px_mm[0], px_mm[1])
    else:
        scales = "px_mm_x=na px_mm_y=na band_x=na band_y=na"
    monitors = monitor_count()
    _log.info("pen_abs_map mode=%s product=%s declared=%dx%d box=%dx%d "
          "box_mm=%.4fx%.4f obs=%dx%d screen=%sx%s monitors=%s %s"
          % (mode, state.pen_product_id,
             pen_specs["max_x"], pen_specs["max_y"],
             int(extent_w), int(extent_h), box_mm_w, box_mm_h,
             state.pen_obs_max_x, state.pen_obs_max_y,
             metrics[2] if metrics is not None else "na",
             metrics[3] if metrics is not None else "na",
             monitors if monitors is not None else "na", scales))


def update_pen_cursor(state, action_dispatch, config, pen_specs, x, y,
                      screen_metrics=None, pen_area_rect=None):
    """Move the pointer: absolute maps the digitizer to the virtual desktop,
    relative sends scaled deltas with a fractional accumulator.

    screen_metrics (U376): synthetic (left, top, width_px, height_px) desktop
    extents for the tests; None resolves them from the system metrics.
    pen_area_rect (U414 / #406, U416 fractional): [rx, ry, rw, rh] FRACTIONS
    (0..1) of the virtual-desktop union bounds, from the per-configuration
    lane (resolved by the caller only on an exact display-key match). The
    branch maps them straight through the normalized 0..65535 space, which
    IS the MOUSEEVENTF_VIRTUALDESK space, so NO display metrics are read;
    None runs the pre-U414 lanes byte-unchanged.
    """
    ad = action_dispatch
    # U424 / #424: ONE mode decides the lane (stored `pen_mode`, else the
    # documented toggle migration) - the old booleans never steer directly
    # here anymore, so a toggle write cannot dual-control the mapping.
    pen_mode = resolved_pen_mode(config)

    if pen_mode != "relative":
        # Aspect-aware mapping: the orientation transform already rotated the
        # coordinate, so scale it against the ROTATED bounding box. The 1e-9
        # tolerance absorbs the rotation's float artifacts at the box edges
        # without shifting any integer device coordinate's truncated result.
        left, top, extent_w, extent_h = rotated_pen_extents(
            pen_specs, config.orientation_degrees)
        metrics = (virtual_desktop_metrics() if screen_metrics is None
                   else screen_metrics)
        px_mm = None
        bands = None
        if pen_mode == "stretched":
            # U424 / #424 ruling 1: the screen checkboxes RESTRICT the
            # stretch target; unselected screens are unreachable, the box
            # stretches across the UNION of the selected monitors. Empty
            # or unset = ALL screens = the pre-U424 full-desktop stretch
            # (byte-unchanged). Junk metrics keep the degraded full fill
            # (subset math needs the metrics datum, never invents one).
            subset = getattr(config, "pen_stretch_screens", None)
            union = (union_rect_from_records(subset)
                     if subset and metrics is not None else None)
            if union is not None and union[2] > 0 and union[3] > 0:
                frac = [(union[0] - metrics[0]) / metrics[2],
                        (union[1] - metrics[1]) / metrics[3],
                        union[2] / metrics[2], union[3] / metrics[3]]
                off_x, scale_x, off_y, scale_y = pen_area_norm_projection(
                    frac, extent_w, extent_h, 1.0, 1.0)
                nx = min(max(int(off_x + (x - left) * scale_x + 1e-9), 0),
                         PEN_VIRTUAL_RESOLUTION)
                ny = min(max(int(off_y + (y - top) * scale_y + 1e-9), 0),
                         PEN_VIRTUAL_RESOLUTION)
                mode = "stretch_subset"
                box_mm_w, box_mm_h = rotated_pen_box_mm(
                    pen_specs, config.orientation_degrees)
                px_mm = (frac[2] * metrics[2] / box_mm_w,
                         frac[3] * metrics[3] / box_mm_h)
                bands = (0.0, 0.0)
            else:
                nx = min(max(int((x - left) * PEN_VIRTUAL_RESOLUTION / extent_w + 1e-9), 0),
                         PEN_VIRTUAL_RESOLUTION)
                ny = min(max(int((y - top) * PEN_VIRTUAL_RESOLUTION / extent_h + 1e-9), 0),
                         PEN_VIRTUAL_RESOLUTION)
                mode = "stretch"
                if metrics is not None:
                    box_mm_w, box_mm_h = rotated_pen_box_mm(
                        pen_specs, config.orientation_degrees)
                    px_mm = (metrics[2] / box_mm_w, metrics[3] / box_mm_h)
                    bands = (0.0, 0.0)
        elif pen_area_rect is not None:
            # Pen-area rect lane (U414 / #406, proposal §2; U416 fractional):
            # the rotated digitizer box maps onto the user's stored
            # rectangle - rect is [rx, ry, rw, rh] FRACTIONS of the virtual
            # bounds, so this branch consults NO display metrics and runs
            # on junk-metrics hosts (#406 defect). Isotropy holds by the
            # aspect-locked rect; px_mm below reports the pair from the
            # ROTATED mm box when metrics allow (diag decoration only).
            # `stretched` mode above keeps override authority; no rect
            # falls through to the U376 default / degraded lane.
            box_mm_w, box_mm_h = rotated_pen_box_mm(
                pen_specs, config.orientation_degrees)
            off_x, scale_x, off_y, scale_y = pen_area_norm_projection(
                pen_area_rect, extent_w, extent_h, 1.0, 1.0)
            nx = min(max(int(off_x + (x - left) * scale_x + 1e-9), 0),
                     PEN_VIRTUAL_RESOLUTION)
            ny = min(max(int(off_y + (y - top) * scale_y + 1e-9), 0),
                     PEN_VIRTUAL_RESOLUTION)
            if metrics is not None:
                px_mm = (pen_area_rect[2] * metrics[2] / box_mm_w,
                         pen_area_rect[3] * metrics[3] / box_mm_h)
            else:
                px_mm = None
            bands = None
            mode = "pen_area"
        elif metrics is not None:
            # Isotropic centered default (U376 fix of the U344 lane): ONE
            # px-per-mm scale from the desktop fit plus a centering offset, so
            # a 45 deg pen stroke stays 45 deg and every unreachable band is
            # symmetric. The old parity ran in the 0..65535 space Windows
            # stretches to the desktop aspect, so it squashed height by
            # exactly the screen aspect (maintainer report 2026-11-06).
            box_mm_w, box_mm_h = rotated_pen_box_mm(
                pen_specs, config.orientation_degrees)
            (scale_px_mm, used_w, used_h, scale_x, scale_y,
             off_x, off_y) = pen_isotropic_absolute_projection(
                extent_w, extent_h, box_mm_w, box_mm_h, metrics[2], metrics[3])
            nx = min(max(int(off_x + (x - left) * scale_x + 1e-9), 0),
                     PEN_VIRTUAL_RESOLUTION)
            ny = min(max(int(off_y + (y - top) * scale_y + 1e-9), 0),
                     PEN_VIRTUAL_RESOLUTION)
            px_mm = (scale_px_mm, scale_px_mm)
            bands = ((metrics[2] - used_w) / 2.0, (metrics[3] - used_h) / 2.0)
            mode = "isotropic"
        else:
            # Degraded lane (no usable desktop metrics), U411 / TODO #401
            # planner ruling (C) 2026-11-11: this branch ran the old
            # equal-FRACTION normalized math (scale 65535/max-extent,
            # centered), which left a 18.75 % dead band per end of a 16:10
            # pad on ANY desktop -- the maintainer's unreachable
            # top/bottom sixths (diag mode was isotropic_unmeasured). The
            # lane now reuses the SHIPPED stretch full-fill math (identical
            # expressions to the opt-in branch above): the full declared
            # range reaches the full 0..65535 on both axes; anisotropy is
            # accepted for this host class until the host-measurement probe
            # (TODO #412) makes the measured isotropic lane reachable.
            # `stretched` mode above keeps override
            # authority. Named `stretch_unmeasured` in the diag line.
            nx = min(max(int((x - left) * PEN_VIRTUAL_RESOLUTION / extent_w + 1e-9), 0),
                     PEN_VIRTUAL_RESOLUTION)
            ny = min(max(int((y - top) * PEN_VIRTUAL_RESOLUTION / extent_h + 1e-9), 0),
                     PEN_VIRTUAL_RESOLUTION)
            mode = "stretch_unmeasured"
        if pen_mapping_diag_enabled() and not state.pen_abs_map_logged:
            state.pen_abs_map_logged = True
            _log_pen_mapping_diagnostic(state, config, pen_specs, metrics, mode,
                                        extent_w, extent_h, px_mm, bands)
        state.pen_position = (x, y)
        # WTD-BATCH: park the position in the dispatcher's latest-wins slot
        # instead of injecting per report; the reader-loop time gate, every
        # stateful emission, and the OOR path flush it (see sender.py).
        ad.queue_pen_absolute_move(nx, ny)
        return

    if state.pen_position is None:
        # First report after an origin reset: start point, no delta.
        state.pen_position = (x, y)
        return

    last_x, last_y = state.pen_position
    dx_mm = (x - last_x) * pen_specs["width_mm"] / pen_specs["max_x"]
    dy_mm = (y - last_y) * pen_specs["height_mm"] / pen_specs["max_y"]
    state.pen_position = (x, y)

    state.pen_cursor_acc_x += dx_mm * config.pen_speed
    state.pen_cursor_acc_y += dy_mm * config.pen_speed
    move_x = int(state.pen_cursor_acc_x)
    move_y = int(state.pen_cursor_acc_y)
    if move_x != 0 or move_y != 0:
        ad.send_real_mouse_move(move_x, move_y)
        state.pen_cursor_acc_x -= move_x
        state.pen_cursor_acc_y -= move_y

def update_pen_pressure_button(state, action_dispatch, config):
    """Pressure drives the left button: held at or above threshold, else released."""
    ad = action_dispatch
    pressed = state.pen_pressure >= config.pen_pressure_threshold
    if pressed and not ad.pen_pressure_held:
        ad.press_pen_pressure()
    elif not pressed and ad.pen_pressure_held:
        ad.release_pen_pressure()

def process_pen_report(report, state, action_dispatch, config, pen_specs,
                       screen_metrics=None):
    """Full pen input from the 10-byte 0x02 report (U70).

    The status byte switches the meaning of bytes 6-7: bits 1 and 3 mark a
    rotation report (pressure, tilt and buttons carry over from the previous
    report), bit 5 marks a tablet report (pressure valid). Tilt and hover are
    ignored per the approved ruling; pressure acts as left-click state.
    screen_metrics passes the synthetic desktop extents through to the
    absolute mapping (U376).
    """
    if not report or report[0] != PEN_REPORT_ID or len(report) < PEN_REPORT_LENGTH:
        return

    status = report[1]
    ad = action_dispatch

    if status == PEN_STATUS_OUT_OF_RANGE:
        # Upstream resets the pointer origin and drops the follow-up report.
        # WTD-BATCH: flush the parked absolute move FIRST so the final
        # position lands on every pen lift/leave - no held-input release
        # may leapfrog it, and nothing emits after the release pair.
        ad.flush_pending_move()
        release_pen_inputs(state, ad)
        return

    if status == PEN_TOOL_REPORT_STATUS:
        return

    is_rotation = (status & PEN_ROTATION_FLAG) == PEN_ROTATION_FLAG
    if not is_rotation and (status & PEN_STATUS_COORDS) == 0:
        # No coordinates in this report (tool/unknown variant): skip.
        return

    x, y = decode_pen_position(report)
    # U344 autoscale: track the raw observed coordinate range, map the box
    # grown to it, and print ONE named line the first time it has to grow.
    state.pen_obs_max_x = max(state.pen_obs_max_x, x)
    state.pen_obs_max_y = max(state.pen_obs_max_y, y)
    grown_specs = pen_effective_specs(pen_specs, state)
    if grown_specs is not pen_specs and not state.pen_extents_scaled_logged:
        _log.info(f"pen_extents_autoscale declared_x={pen_specs['max_x']} "
              f"declared_y={pen_specs['max_y']} "
              f"observed_x={state.pen_obs_max_x} "
              f"observed_y={state.pen_obs_max_y} using=observed")
        state.pen_extents_scaled_logged = True
    # Orientation (U74): same shared transform as the touch contacts, about
    # the digitizer center from the per-model ranges (U344: the grown box).
    # Tilt stays ignored.
    x, y = apply_orientation((x, y), config, pen_rotation_center(grown_specs))
    if is_rotation:
        # Bytes 6-7 carry rotation, not pressure; values carry over.
        new_buttons = state.pen_buttons
    else:
        state.pen_pressure = decode_pen_pressure(report)
        new_buttons = ((status & PEN_BUTTON_1_MASK) != 0,
                       (status & PEN_BUTTON_2_MASK) != 0)

    for slot, current in ((1, new_buttons[0]), (2, new_buttons[1])):
        if current != state.pen_buttons[slot - 1]:
            action_name = config.action_mapping.get(f"pen_button_{slot}")
            if current:
                ad.press_pen_button(slot, action_name)
            else:
                ad.release_pen_button(slot)
    state.pen_buttons = new_buttons

    update_pen_cursor(state, ad, config, grown_specs, x, y,
                      screen_metrics=screen_metrics,
                      pen_area_rect=select_live_pen_area_rect(config,
                                                              state.pen_identity))
    update_pen_pressure_button(state, ad, config)
