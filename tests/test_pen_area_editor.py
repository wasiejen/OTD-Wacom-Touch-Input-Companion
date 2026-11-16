"""U414 L3 (#406, option A) + U416 L3 (#406 defect round 1): rect editor.

Model pins run headless (pure `PenAreaCanvasModel` math, no tkinter); the
widget pins use the module-scoped hidden Tk root like test_gui_device_bar.

U416 (maintainer host defect: black canvas + "No display geometry ..." on
the junk-metrics host): the editor now BUILDS its model always - bounds
from virtual metrics, else the EnumDisplayMonitors union, else a single
fallback rectangle with the pad's aspect - and the stored rect is
FRACTIONAL. Device identity is required only at SAVE time (it names the
lane). RED-before 03c0a40: the old inert-without-metrics gate + rect_px
save.

PENAREA (maintainer-requested 2026-11-16): the inside-the-union clamp is
RETIRED. Standing maintainer safety rule, quoted: "at least one corner of
the pen-area rectangle must remain inside the display area". The pins below
that say PENAREA lock the new corner-rule math (the pure
`clamp_rect_to_display`), the off-screen drag/resize/field lanes, the
viewport margin, and the rotation revalidation; the pins that pinned the
retired clamp carry a PENAREA re-pin note.
"""

import math

import pytest

import gui
from gui import PenAreaCanvasModel
from wtd.mapping import pen_area_rect_aspect_lock
from wtd import config as wtd_config
from wtd import mapping
from wtd import protocol

SCREEN = (0, 0, 3440, 1440)
ORIGIN_SCREEN = (-1440, 0, 4880, 2560)
RECORDS = ["\\\\.\\DISPLAY1|0|0|3440x1440"]
RECORDS_DUO = ["\\\\.\\DISPLAY1|0|0|3440x1440", "\\\\.\\DISPLAY2|3440|0|1440x2560"]
RECT = [500, 120, 1920, 1200]
BOX_MM = (325.12, 203.2)
CANVAS = (320.0, 120.0)


def _model(metrics=SCREEN, records=None, rect=RECT):
    return PenAreaCanvasModel(metrics, records if records is not None else RECORDS,
                              CANVAS[0], CANVAS[1], BOX_MM[0], BOX_MM[1], rect)


def test_apply_scale_resizes_footprint_at_fixed_center():
    # U424r3 R3-L3 (#424 item 3b): sensitivity is an editable handle on
    # the footprint. RED-before cc392fc: AttributeError apply_scale.
    m = _model()
    cx = m.rect[0] + m.rect[2] / 2
    cy = m.rect[1] + m.rect[3] / 2
    m.apply_scale(2.0)
    assert m.rect[2] == pytest.approx(2.0 * BOX_MM[0], rel=1e-9)
    assert m.rect[3] == pytest.approx(2.0 * BOX_MM[1], rel=1e-9)
    assert (m.rect[0] + m.rect[2] / 2, m.rect[1] + m.rect[3] / 2) == \
        pytest.approx((cx, cy))


def test_apply_scale_overshoot_slides_the_rect_to_keep_a_corner_on_screen():
    # PENAREA re-pin of test_apply_scale_clamps_inside_the_desktop: the
    # absurd-px/mm case no longer caps the size at the DESKTOP WIDTH - a
    # sensitivity beyond it is accepted (only the MAX_SIZE_FACTOR arithmetic
    # bound, tested separately, caps). The wanted size stands; only the
    # placement obeys the maintainer rule "at least one corner of the
    # pen-area rectangle must remain inside the display area", so the
    # over-sized rect slides to the nearest legal placement (the center
    # (500, 350) is asymmetric -> the unique nearest placement lands the
    # rect's lower-right corner on the display's lower-right corner).
    m = _model(rect=[100.0, 100.0, 800.0, 500.0])
    m.apply_scale(50.0)  # 16256 px wide: past the 3440 px desktop, in bound
    assert m.rect[2] == pytest.approx(50.0 * BOX_MM[0])
    assert m.rect[3] == pytest.approx(50.0 * BOX_MM[1])
    assert m.rect[2] > SCREEN[2]  # the retired desktop-width cap
    assert gui.rect_any_corner_inside(m.rect, m.display_bbox())
    assert [m.rect[0] + m.rect[2], m.rect[1] + m.rect[3]] == pytest.approx(
        [SCREEN[2], SCREEN[3]])


def test_aspect_lock_helper_rederives_rh():
    assert pen_area_rect_aspect_lock([0, 0, 1000, 7], 325.12, 203.2) == \
        [0, 0, 1000, 625]
    assert pen_area_rect_aspect_lock([10, 20, 160, 99], 16.0, 10.0) == \
        [10, 20, 160, 100]


def _margin_view(metrics):
    """The rule-derived viewport of a model whose rect stays inside the bbox."""
    return (metrics[2] * (1 + 2 * gui.VIEW_MARGIN),
            metrics[3] * (1 + 2 * gui.VIEW_MARGIN),
            -metrics[2] * gui.VIEW_MARGIN, -metrics[3] * gui.VIEW_MARGIN)


def test_model_scale_and_canvas_rect_round_trip():
    # PENAREA re-pin: the view transform fits the display bbox PLUS the
    # viewport margin into the canvas (the rect stays inside the bbox here,
    # so the view is exactly bbox * (1 + 2 * VIEW_MARGIN)); the canvas map
    # and its inverse round-trip, rect_canvas uses the same transform.
    model = _model()
    view_w, view_h, view_x0, view_y0 = _margin_view(SCREEN)
    scale = min(320.0 / view_w, 120.0 / view_h)
    assert model.scale == pytest.approx(scale)
    assert (model.view_x0, model.view_y0) == pytest.approx((view_x0, view_y0))
    x0, y0, x1, y1 = model.rect_canvas()
    # U424r4 R4-A re-pin: canvas coords carry the centering offset now.
    assert ((x0 - model.offset_x) / scale + view_x0,
            (y0 - model.offset_y) / scale + view_y0) == \
        pytest.approx((RECT[0], RECT[1]))
    assert ((x1 - x0) / scale, (y1 - y0) / scale) == pytest.approx((RECT[2], RECT[3]))
    # The handle maps through the same transform and maps back exactly.
    hx, hy = model.handle_canvas()
    assert model.desktop_xy_from_canvas(hx, hy) == pytest.approx(
        (RECT[0] + RECT[2], RECT[1] + RECT[3]))


def test_model_monitor_outlines_use_records_and_origin():
    # U424r4 R4-A re-pin + PENAREA: outlines carry the CENTERING offset and
    # the viewport datum (view_x0) now.
    model = _model(ORIGIN_SCREEN, RECORDS_DUO)
    boxes = model.monitor_canvas_rects()
    view_w, view_h, view_x0, _ = _margin_view(ORIGIN_SCREEN)
    scale = min(320.0 / view_w, 120.0 / view_h)
    assert len(boxes) == 2
    assert boxes[0][0] == pytest.approx(
        (0 - (-1440) - view_x0) * scale + model.offset_x)
    assert boxes[1][0] == pytest.approx(
        (3440 - (-1440) - view_x0) * scale + model.offset_x)


# --- U424r3 R4-A (#424 round-4 item A): drawn union centered in canvas ---

def _drawn_bbox(model):
    xs0, ys0, xs1, ys1 = [], [], [], []
    for x0, y0, x1, y1 in model.monitor_canvas_rects():
        xs0.append(x0); ys0.append(y0); xs1.append(x1); ys1.append(y1)
    return min(xs0), min(ys0), max(xs1), max(ys1)


def test_drawn_union_centered_equal_margins_three_layouts():
    # RED-before 69315c3: content started at canvas origin -> the slack
    # margin was dead space on ONE side (his "dead area left of screens").
    layouts = (
        (SCREEN, RECORDS),                       # wide single
        ((0, 0, 4880, 2560), RECORDS_DUO),       # dual, unequal heights
        ((0, 0, 1440, 2560),                     # one pivoted (tall)
         ["\\\\.\\DISPLAY1|0|0|1440x2560"]),
    )
    for metrics, records in layouts:
        m = PenAreaCanvasModel(metrics, records, 320.0, 120.0,
                               BOX_MM[0], BOX_MM[1])
        bx0, by0, bx1, by1 = _drawn_bbox(m)
        assert bx0 == pytest.approx(320.0 - bx1), (metrics, records)  # L == R
        # Equal margins on the slack axis: the drawn union's own box
        # (not the per-monitor bbox, which touches only what fits) sits
        # inside the centered desktop content frame. PENAREA: the fit now
        # derives from the bbox PLUS the viewport margin; the bbox itself
        # stays exactly centered inside the view (R4-A holds).
        view_w, view_h, _, _ = _margin_view(metrics)
        assert m.scale == pytest.approx(min(320.0 / view_w, 120.0 / view_h))
        content_w = m.desktop_w * m.scale
        assert bx1 - bx0 == pytest.approx(content_w, abs=1.5)
        assert m.offset_x == pytest.approx((320.0 - view_w * m.scale) / 2.0)


def test_model_drag_places_rect_partly_offscreen_freely():
    # PENAREA: a drag that takes the rect past a screen edge is LEGAL while
    # at least one corner stays inside - the retired clamp would have
    # stopped it at ry = 0.
    model = _model()
    model.move_by(-400 * model.scale, -100 * model.scale)
    assert model.rect == pytest.approx([100.0, 20.0, 1920.0, 1200.0])
    model.move_by(0, -500 * model.scale)  # hangs 480 px above the screens
    assert model.rect == pytest.approx([100.0, -480.0, 1920.0, 1200.0])
    assert gui.rect_any_corner_inside(model.rect, model.display_bbox())


def test_model_drag_stops_with_one_corner_on_the_display_edge():
    # PENAREA re-pin of test_model_drag_clamps_to_desktop: an overshoot that
    # would leave NO corner inside clamps to the nearest rule-valid
    # placement - the rect simply stops (no popup), now on the OTHER side of
    # the screens (one corner rides the edge, the body hangs outside).
    model = _model()
    model.move_by(100000, 0)
    assert model.rect == pytest.approx([3440.0, 120.0, 1920.0, 1200.0])
    assert gui.rect_any_corner_inside(model.rect, model.display_bbox())
    model.move_by(-200000, 0)
    assert model.rect == pytest.approx([-1920.0, 120.0, 1920.0, 1200.0])
    assert gui.rect_any_corner_inside(model.rect, model.display_bbox())


def test_model_handle_resize_locks_aspect_and_grows_past_the_screens():
    # PENAREA re-pin of test_model_handle_resize_locks_aspect_and_clamps:
    # with the origin (500, 120) inside the screens, ANY wanted size is
    # rule-valid, so the old bbox size cap is gone and the drag grows the
    # rect past the right edge. The aspect lock stays exact.
    model = _model()
    handle = model.handle_canvas()
    model.resize_handle_to(handle[0] + 100 * model.scale, handle[1] + 100 * model.scale)
    assert model.rect[3] == round(model.rect[2] * BOX_MM[1] / BOX_MM[0])
    handle = model.handle_canvas()  # now at desktop (2520, 1382)
    model.resize_handle_to(handle[0] + 6000 * model.scale, handle[1] + 6000 * model.scale)
    assert model.rect[2] == pytest.approx(2520.0 + 6000.0 - 500.0, abs=1.0)
    assert model.rect[0] + model.rect[2] > 3440  # hangs off the right edge
    assert model.rect[3] == round(model.rect[2] * BOX_MM[1] / BOX_MM[0])
    assert gui.rect_any_corner_inside(model.rect, model.display_bbox())


def test_model_handle_resize_stops_when_no_corner_would_stay_inside():
    # PENAREA: the rect hangs off the top-left with its lower-right corner
    # (500, 137.5) as the ONLY anchor inside the screens; growing the rect
    # pushes that anchor out, so the resize update is rejected - the handle
    # stops at the last valid size (no popup).
    model = _model(rect=[-1000.0, -800.0, 1500.0, 937.5])
    assert gui.rect_any_corner_inside(model.rect, model.display_bbox())
    handle = model.handle_canvas()
    model.resize_handle_to(handle[0] + 5000 * model.scale,
                           handle[1] + 5000 * model.scale)
    assert model.rect == pytest.approx([-1000.0, -800.0, 1500.0, 937.5])
    # The per-event recompute follows the handle again the moment the wanted
    # size is legal: w = 3000 puts the lower-right corner at (2000, 1075),
    # inside the screens.
    handle = model.handle_canvas()
    model.resize_handle_to(handle[0] + 1500 * model.scale, handle[1])
    assert model.rect == pytest.approx([-1000.0, -800.0, 3000.0, 1875.0])
    assert gui.rect_any_corner_inside(model.rect, model.display_bbox())


def test_model_readout_names_px_and_mm():
    out = _model().readout()
    assert "1920x1200 px" in out
    assert "5.9055 px/mm" in out  # 1920 / 325.12 = 5.90551...


# --- PENAREA: the pure corner-rule clamp (rect + bbox -> allowed rect) ---
# Maintainer safety rule, quoted: "at least one corner of the pen-area
# rectangle must remain inside the display area".

PURE_BBOX = (0.0, 0.0, 200.0, 100.0)


def test_corner_rule_boundary_case_one_corner_exactly_on_the_edge():
    # Edges count as inside (closed rule): a flush rect with exactly ONE
    # corner on the display edge is legal and comes back unchanged.
    flush = [200.0, 50.0, 50.0, 40.0]
    assert gui.rect_any_corner_inside(flush, PURE_BBOX) is True
    assert gui.clamp_rect_to_display(flush, PURE_BBOX) == flush
    # One step further right leaves no corner inside -> the rule bites.
    outside = [200.0001, 50.0, 50.0, 40.0]
    assert gui.rect_any_corner_inside(outside, PURE_BBOX) is False


def test_corner_rule_drag_out_from_each_corner_direction():
    # The rect starts fully inside; a jump out along each diagonal stops
    # with exactly one corner left on the display area (the nearest legal
    # placement), the body hanging outside.
    # lower-right: the rect's upper-left corner lands on the display's
    # lower-right corner (200, 100).
    assert gui.clamp_rect_to_display([500.0, 500.0, 50.0, 40.0], PURE_BBOX) == \
        [200.0, 100.0, 50.0, 40.0]
    # upper-left: the rect's lower-right corner lands on (0, 0).
    assert gui.clamp_rect_to_display([-500.0, -500.0, 50.0, 40.0], PURE_BBOX) == \
        [-50.0, -40.0, 50.0, 40.0]
    # straight right (y still inside): the upper-left corner rides the right
    # edge at its own height - the nearest slide, not a corner jump.
    assert gui.clamp_rect_to_display([500.0, 10.0, 50.0, 40.0], PURE_BBOX) == \
        [200.0, 10.0, 50.0, 40.0]
    # straight down: the upper-left corner rides the bottom edge.
    assert gui.clamp_rect_to_display([10.0, 500.0, 50.0, 40.0], PURE_BBOX) == \
        [10.0, 100.0, 50.0, 40.0]
    # straight up: the lower-left corner rides the top edge.
    assert gui.clamp_rect_to_display([10.0, -500.0, 50.0, 40.0], PURE_BBOX) == \
        [10.0, -40.0, 50.0, 40.0]


def test_corner_rule_extreme_overshoot_still_lands_on_the_nearest_placement():
    # A programmatic jump 10^6 px away cannot escape the rule either: the
    # nearest legal placement wins (minimal whole-rect displacement).
    assert gui.clamp_rect_to_display([-1e6, -1e6, 50.0, 40.0], PURE_BBOX) == \
        [-50.0, -40.0, 50.0, 40.0]
    # Far right + far up: the rect's LOWER-left corner is the cheapest
    # anchor (the body then hangs right of the edge) -> it lands on the
    # display's top-right corner (200, 0).
    assert gui.clamp_rect_to_display([1e6, -1e6, 50.0, 40.0], PURE_BBOX) == \
        [200.0, -40.0, 50.0, 40.0]


def test_corner_rule_allows_a_rect_larger_than_the_display_bbox():
    # The rule binds the PLACEMENT, not the size: with closed edges every
    # size is placeable, so a rect bigger than the screens stays when a
    # corner touches, and a floating one snaps to the nearest corner touch.
    big = [0.0, 0.0, 400.0, 300.0]
    assert gui.rect_any_corner_inside(big, PURE_BBOX) is True
    assert gui.clamp_rect_to_display(big, PURE_BBOX) == big
    # Fully floating: the nearest placement puts the rect's upper-left
    # corner on the display's lower-right corner.
    assert gui.clamp_rect_to_display([210.0, 110.0, 400.0, 300.0], PURE_BBOX) == \
        [200.0, 100.0, 400.0, 300.0]


def test_corner_rule_fully_inside_rect_needs_no_translation():
    assert gui.clamp_rect_to_display([50.0, 30.0, 50.0, 40.0], PURE_BBOX) == \
        [50.0, 30.0, 50.0, 40.0]


def test_apply_fields_accepts_negative_and_beyond_union_entries():
    # PENAREA: the X/Y fields keep the old 0..union clamp no more. An
    # off-screen placement is stored as typed while the rule holds.
    m = _model()
    assert m.apply_fields(x=-500.0, y=-300.0) == pytest.approx(
        [-500.0, -300.0, 1920.0, 1200.0])  # lower-right corner (1420, 900) in
    # A field entry that would leave no corner inside clamps to the nearest
    # valid placement, and _sync_rect_entries echoes that value back.
    assert m.apply_fields(x=9000.0) == pytest.approx(
        [3440.0, -300.0, 1920.0, 1200.0])


def test_apply_fields_width_stops_when_the_rule_would_break():
    # The rect hangs off the top-left; w = 3000 keeps the lower-right corner
    # (2000, 1075) inside, w = 4000 would push every corner out -> rejected,
    # the previous size stands (the field echoes it back).
    m = _model(rect=[-1000.0, -800.0, 1500.0, 937.5])
    assert m.apply_fields(w=3000.0) == pytest.approx(
        [-1000.0, -800.0, 3000.0, 1875.0])
    assert m.apply_fields(w=4000.0) == pytest.approx(
        [-1000.0, -800.0, 3000.0, 1875.0])


def test_model_build_snaps_a_rule_breaking_stored_rect_back():
    # A stale or hand-edited entry with NO corner inside (e.g. written while
    # the screens were bigger) snaps to the nearest rule-valid placement at
    # build, so the editor can never show or persist an unreachable rect.
    m = _model(rect=[5000.0, 2000.0, 800.0, 500.0])
    assert m.rect == pytest.approx([3440.0, 1440.0, 800.0, 500.0])
    assert gui.rect_any_corner_inside(m.rect, m.display_bbox())


def test_viewport_shows_the_offscreen_rect_part_with_margin():
    # PENAREA preview: the canvas viewport is the display bbox plus a margin
    # (extended to the stored rect when it hangs outside), so the off-screen
    # rect part is VISIBLE and its handle stays grabbable.
    m = _model(rect=[-500.0, -300.0, 1500.0, 937.5])
    x0, y0, x1, y1 = m.rect_canvas()
    assert 0.0 <= x0 and 0.0 <= y0
    assert x1 <= m.canvas_w and y1 <= m.canvas_h
    hx, hy = m.handle_canvas()
    assert 0.0 <= hx <= m.canvas_w and 0.0 <= hy <= m.canvas_h
    # The margin exists for a fully on-screen rect too (drag room beyond the
    # screens), derived from VIEW_MARGIN, not measured.
    inside = _model()
    view_w, view_h, _, _ = _margin_view(SCREEN)
    assert inside.scale == pytest.approx(min(320.0 / view_w, 120.0 / view_h))
    assert inside.scale < min(320.0 / SCREEN[2], 120.0 / SCREEN[3])


def test_viewport_refits_after_a_drag_so_the_handle_stays_reachable():
    # PENAREA (reviewer P1): the corner rule parks the rect with rx up to
    # the desktop width, which pushes its lower-right handle OUT of the
    # fixed-size canvas (the resize lane would strand). The model refits the
    # viewport at build and at the END of each interaction, so after the
    # refit the whole rect and the handle sit inside the canvas - and the
    # rect datum (what gets persisted) does not move.
    model = _model()
    model.move_by(100000, 0)  # parked at the rule limit, handle off-canvas
    assert model.handle_canvas()[0] > model.canvas_w
    model.retarget_view()
    x0, y0, x1, y1 = model.rect_canvas()
    hx, hy = model.handle_canvas()
    assert 0.0 <= x0 and x1 <= model.canvas_w
    assert 0.0 <= y0 and y1 <= model.canvas_h
    assert 0.0 <= hx <= model.canvas_w and 0.0 <= hy <= model.canvas_h
    assert model.rect == pytest.approx([3440.0, 120.0, 1920.0, 1200.0])
    # Every rule-limit direction refits into the canvas (reviewer P2-7: the
    # reachability must hold across the legal range, not one fixture spot).
    for delta in ((100000, 0), (-100000, 0), (0, 100000), (0, -100000)):
        m = _model()
        m.move_by(*delta)
        m.retarget_view()
        x0, y0, x1, y1 = m.rect_canvas()
        hx, hy = m.handle_canvas()
        assert 0.0 <= x0 and x1 <= m.canvas_w, delta
        assert 0.0 <= y0 and y1 <= m.canvas_h, delta
        assert 0.0 <= hx <= m.canvas_w and 0.0 <= hy <= m.canvas_h, delta


def test_size_bound_keeps_the_geometry_and_the_save_finite():
    # PENAREA (reviewer P2): with the inside-union size cap retired, an
    # absurd width must not propagate an infinite height into persist or
    # the normalized pen math - the wanted size floors non-finite to
    # MIN_RECT_PX and caps at MAX_SIZE_FACTOR x the larger desktop dim.
    m = _model()
    out = m.apply_fields(w=float("1.798e308"))
    assert out[2] == pytest.approx(gui.MAX_SIZE_FACTOR * 3440.0)
    assert math.isfinite(out[3]) and out[3] > 0.0
    assert gui.rect_any_corner_inside(out, m.display_bbox())
    out = m.apply_fields(w=float("nan"))
    assert out[2] == pytest.approx(m.MIN_RECT_PX)
    assert math.isfinite(out[3])
    m2 = _model()
    m2.apply_scale(1e12)
    assert m2.rect[2] == pytest.approx(gui.MAX_SIZE_FACTOR * 3440.0)
    assert math.isfinite(m2.rect[3])


def test_match_box_aspect_after_rotation_reevaluates_the_corner_rule():
    # Rotation interaction: a rect placed at 0 deg partly off-screen is
    # rule-valid; after an orientation flip (the mm box swaps, the rect
    # re-derives its shape) the SAME clamp validates the result, so rotation
    # can never silently leave no corner on-screen. The sensitivity rides
    # across EXACTLY (the retired fit-to-union cap would have shrunk it).
    m = _model(rect=[-1400.0, 100.0, 1500.0, 937.5])
    assert gui.rect_any_corner_inside(m.rect, m.display_bbox())
    m.box_mm = (BOX_MM[1], BOX_MM[0])  # 90 deg: the mm box swaps
    out = m.match_box_aspect(1500.0 / BOX_MM[0])  # s from the PREVIOUS box
    assert out == pytest.approx([-937.5, -181.25, 937.5, 1500.0])
    assert gui.rect_any_corner_inside(out, m.display_bbox())
    # The shape agrees with the rotated box (aspect kept exactly).
    assert out[2] / out[3] == pytest.approx(BOX_MM[1] / BOX_MM[0], rel=1e-9)


@pytest.fixture(scope="module")
def root():
    # Same pattern as tests/test_gui_device_bar.py: one withdrawn root.
    import tkinter as tk
    root = tk.Tk()
    root.withdraw()
    yield root
    root.destroy()


@pytest.fixture
def widget_config(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    return wtd_config.DriverConfig()


def _build_widget(root, widget_config, monkeypatch, metrics=SCREEN):
    monkeypatch.setattr(gui, "virtual_desktop_metrics", lambda: metrics)
    monkeypatch.setattr(gui, "monitor_records", lambda: list(RECORDS))
    mapping.reset_pen_area_key_cache()
    device_info = {"connection": {"identity": "0123456789ab", "product_id": 791},
                   "base_config": widget_config}
    return gui.PenAreaEditor(root, widget_config, device_info)


def test_widget_save_writes_fractional_entry_for_current_display_key(root, widget_config, monkeypatch):
    calls = []
    monkeypatch.setattr(gui, "reset_pen_area_key_cache", lambda: calls.append(1))
    widget = _build_widget(root, widget_config, monkeypatch)
    assert widget.persist_entry() is True
    entry = list(widget_config.pen_area_maps["0123456789ab"]["entries"])[0]
    assert entry["display_key"] == mapping.display_key_for_records(RECORDS)
    assert entry["monitors"] == RECORDS
    rect_frac = entry["rect_frac"]
    assert len(rect_frac) == 4
    # PENAREA: the 0..1 containment contract is retired - the schema keeps
    # finite values with positive size; an off-screen rect part stores as
    # negative fractions or fractions past 1 (round-trip pinned in
    # tests/test_pen_area_map.py). The DEFAULT rect (no edit) stays a
    # sub-union footprint, though the R5-E aspect re-derivation can let it
    # ride a fraction of a pixel off an edge - legal under the corner rule.
    assert all(math.isfinite(f) for f in rect_frac)
    assert rect_frac[2] > 0.0 and rect_frac[3] > 0.0
    assert rect_frac[2] < 1.0 and rect_frac[3] < 1.0
    assert gui.rect_any_corner_inside(widget.model.rect,
                                      widget.model.display_bbox())
    assert calls == [1]


def test_widget_editable_without_identity_save_refuses(root, widget_config, monkeypatch):
    # U416: identity names the LANE, so it gates SAVE only. Editing must
    # work without it (the model is live, drag math runs).
    monkeypatch.setattr(gui, "virtual_desktop_metrics", lambda: SCREEN)
    monkeypatch.setattr(gui, "monitor_records", lambda: list(RECORDS))
    device_info = {"connection": {"product_id": 791}, "base_config": widget_config}
    widget = gui.PenAreaEditor(root, widget_config, device_info)
    assert widget.model is not None
    widget.model.move_by(100000, 0)
    # PENAREA: the drag stops at the corner rule, not at the old bbox edge -
    # the rect hangs off the right edge with its upper-left corner riding it.
    assert widget.model.rect[0] == pytest.approx(3440.0)
    assert gui.rect_any_corner_inside(widget.model.rect,
                                      widget.model.display_bbox())
    assert widget.persist_entry() is False
    # U424r4 R4-D: remove_entry deleted with the Remove Rect button.
    assert widget_config.pen_area_maps == {}


def test_widget_builds_on_metrics_less_host_from_records(root, widget_config, monkeypatch):
    # U416 defect core repro: junk/None virtual metrics (maintainer host).
    # Bounds fall back to the EnumDisplayMonitors union; canvas usable.
    monkeypatch.setattr(gui, "virtual_desktop_metrics", lambda: None)
    monkeypatch.setattr(gui, "monitor_records", lambda: list(RECORDS))
    device_info = {"connection": {"identity": "0123456789ab", "product_id": 791},
                   "base_config": widget_config}
    widget = gui.PenAreaEditor(root, widget_config, device_info)
    assert widget.model is not None
    assert (widget.model.desktop_w, widget.model.desktop_h) == (3440.0, 1440.0)
    widget.model.move_by(100000, 0)
    # PENAREA: same corner-rule stop on the fallback host (no bbox edge).
    assert widget.model.rect[0] == pytest.approx(3440.0)
    assert widget.persist_entry() is True


def test_widget_falls_back_to_pad_aspect_rect(root, widget_config, monkeypatch):
    # U416 last resort: NO metrics AND no usable enumeration -> the canvas
    # still renders a single virtual-desktop rectangle with the pad's
    # aspect (fractions stay load-stable via the empty-records display key).
    monkeypatch.setattr(gui, "virtual_desktop_metrics", lambda: None)
    monkeypatch.setattr(gui, "monitor_records", lambda: [])
    device_info = {"connection": {"identity": "0123456789ab", "product_id": 791},
                   "base_config": widget_config}
    widget = gui.PenAreaEditor(root, widget_config, device_info)
    assert widget.model is not None
    box = mapping.rotated_pen_box_mm(
        protocol.PEN_SPECS_BY_PRODUCT[791], 0)
    assert widget.model.desktop_w / widget.model.desktop_h == pytest.approx(
        box[0] / box[1], rel=1e-6)
    assert len(widget.model.monitor_canvas_rects()) == 1
    assert widget.persist_entry() is True
    entry = list(widget_config.pen_area_maps["0123456789ab"]["entries"])[0]
    assert entry["monitors"] == []
    assert entry["display_key"] == mapping.display_key_for_records([])


def test_widget_loads_stored_fraction_back_into_model_px(root, widget_config, monkeypatch):
    # Fraction round-trip through the editor: saved fraction -> model px
    # on reopen matches the same bounds.
    monkeypatch.setattr(gui, "virtual_desktop_metrics", lambda: SCREEN)
    monkeypatch.setattr(gui, "monitor_records", lambda: list(RECORDS))
    device_info = {"connection": {"identity": "0123456789ab", "product_id": 791},
                   "base_config": widget_config}
    widget = gui.PenAreaEditor(root, widget_config, device_info)
    widget.model.rect = [516.0, 144.0, 1720.0, 720.0]  # 16:10-ish; aspect may lock
    assert widget.persist_entry() is True
    key = mapping.display_key_for_records(RECORDS)
    frac = widget_config.pen_area_maps["0123456789ab"]["entries"][0]["rect_frac"]
    reopened = gui.PenAreaEditor(root, widget_config, device_info)
    # U424r5 R5-E: the reopened model RE-DERIVES a mismatched stored
    # rect to the box aspect (center + sensitivity kept). PENAREA re-pin:
    # the retired fit-to-union clamp used to push the taller re-derived
    # rect onto the top edge (rect[1] == 0); now the sensitivity-exact
    # rect simply hangs 33.5 px above the screens - its lower corners
    # (516/2236, 1041.5) stay inside, so the maintainer rule holds.
    box0 = reopened.model.box_mm
    assert reopened.model.rect[2] / reopened.model.rect[3] == pytest.approx(
        box0[0] / box0[1], rel=1e-6)
    assert reopened.model.rect[2] == pytest.approx(1720.0, rel=1e-6)  # s kept
    assert reopened.model.rect[1] == pytest.approx(-33.5, abs=1e-6)
    assert gui.rect_any_corner_inside(reopened.model.rect,
                                      reopened.model.display_bbox())


def test_widget_drag_release_refits_the_viewport(root, widget_config, monkeypatch):
    # PENAREA (reviewer P1 cure, widget leg): the release handler calls the
    # refit, so the handle of a rect parked at the rule limit is back inside
    # the canvas for the next grab (mid-drag nothing refits).
    widget = _build_widget(root, widget_config, monkeypatch)
    widget.model.move_by(100000, 0)
    assert widget.model.handle_canvas()[0] > widget.CANVAS_W
    widget._release(None)
    assert 0.0 <= widget.model.handle_canvas()[0] <= widget.CANVAS_W


def test_widget_offscreen_rect_persists_and_reopens_at_the_same_px(
        root, widget_config, monkeypatch):
    # PENAREA spec item 4 at the EDITOR level: park the rect off-screen,
    # persist it (the Apply Live path), reopen the editor -> the SAME px
    # come back. The stored fractions are negative here; the aspect-equal
    # rect skips the R5-E re-derivation, so the round trip is exact.
    widget = _build_widget(root, widget_config, monkeypatch)
    bw, bh = widget.model.box_mm
    rect = [-500.0, -300.0, 1500.0, 1500.0 * bh / bw]
    widget.model.rect = list(rect)
    assert widget.persist_entry() is True
    frac = widget_config.pen_area_maps["0123456789ab"]["entries"][0]["rect_frac"]
    assert frac[0] < 0.0 and frac[1] < 0.0
    reopened = _build_widget(root, widget_config, monkeypatch)
    assert reopened.model.rect == pytest.approx(rect)
    assert reopened.model.rect[0] < 0.0 and reopened.model.rect[1] < 0.0


# --- GUI-B (2026-11-16, from the maintainer's PENAREA/GUI-A live test):
# B1 rotated-projection overlay. The blue rect stays the axis-aligned
# BOUNDING BOX of the pen projection (its size follows the rotated mm
# box); the lighter dashed outline drawn over it is the ACTUAL rotated
# footprint (gui.projection_overlay_corners). The pins below run the
# expected values through wtd.mapping itself - the transform authority -
# never through a copied matrix.


def test_gui_b_overlay_corners_equal_the_mapping_rotation_of_the_blue_corners():
    # GUI-B B1 pin: overlay corners == blue corners projected through the
    # mapping rotation at 0/45/90/270 (rotate_point is the authority).
    rect = [500.0, 120.0, 1920.0, 1200.0]
    cx = rect[0] + rect[2] / 2.0
    cy = rect[1] + rect[3] / 2.0
    for degrees in (0, 45, 90, 270):
        expected = [mapping.rotate_point(x, y, degrees, cx, cy)
                    for x, y in gui.rect_corners(rect)]
        for got, want in zip(gui.projection_overlay_corners(rect, degrees),
                             expected):
            assert got == pytest.approx(want)


def test_gui_b_rotation_anchor_maps_to_the_rect_center():
    # GUI-B B1 anchor fidelity: the pen path rotates about
    # pen_rotation_center (the device center). Running that center through
    # the projection's OWN math (rotated_pen_extents +
    # pen_area_norm_projection) lands on EXACTLY the rect center at every
    # angle, so the rect center is the projection's rotation anchor in the
    # editor datum and the overlay is literally the footprint.
    specs = {"max_x": 43500, "max_y": 27480,
             "width_mm": 217.5, "height_mm": 137.4}
    for degrees in (0, 45, 90, 270):
        left, top, ext_w, ext_h = mapping.rotated_pen_extents(specs, degrees)
        rect = [0.11, 0.23, 0.61, 0.37]  # fractions, screen 1.0 (U416 form)
        off_x, scale_x, off_y, scale_y = mapping.pen_area_norm_projection(
            rect, ext_w, ext_h, 1.0, 1.0)
        center = mapping.pen_rotation_center(specs)
        nx = ((off_x + (center[0] - left) * scale_x)
              / mapping.PEN_VIRTUAL_RESOLUTION)
        ny = ((off_y + (center[1] - top) * scale_y)
              / mapping.PEN_VIRTUAL_RESOLUTION)
        assert nx == pytest.approx(rect[0] + rect[2] / 2.0)
        assert ny == pytest.approx(rect[1] + rect[3] / 2.0)


def test_gui_b_widget_draws_the_rotated_overlay(root, widget_config,
                                                monkeypatch):
    # GUI-B B1 at the canvas level: after a 45 deg angle the dashed overlay
    # polygon is the ROTATED footprint (it spills past the axis-aligned
    # blue bbox at 45 deg, so this leg is discriminating on an
    # axis-aligned draw), its points equal the model corners through the
    # same rotation, and its style differs from the blue bbox (lighter
    # outline vs solid #00c8ff).
    widget = _build_widget(root, widget_config, monkeypatch)
    widget.orientation_degrees = 45
    widget._redraw()
    overlay_items = widget.canvas.find_withtag("penarea_projection")
    assert overlay_items
    coords = widget.canvas.coords(overlay_items[0])
    expected = [widget.model.canvas_xy(*corner)
                for corner in gui.projection_overlay_corners(
                    widget.model.rect, 45)]
    assert coords == pytest.approx(
        [p for corner in expected for p in corner])
    rc = widget.model.rect_canvas()
    assert max(coords[0::2]) > rc[2] + 1e-6  # rotated: spills past the bbox
    assert (widget.canvas.itemconfig(overlay_items[0], "outline")[-1]
            == "#aee4ff")
    rect_items = widget.canvas.find_withtag("penarea_rect")
    assert (widget.canvas.itemconfig(rect_items[0], "outline")[-1]
            == "#00c8ff")


def test_gui_b_widget_reorient_updates_the_overlay(root, widget_config,
                                                   monkeypatch):
    # GUI-B B1: reorient() carries the new angle to the overlay (the pen
    # path follows the same config value), so after a 90 deg reorient the
    # drawn polygon equals the corners rotated by 90.
    widget = _build_widget(root, widget_config, monkeypatch)
    widget.reorient(protocol.PEN_SPECS_BY_PRODUCT[791], 90)
    overlay_items = widget.canvas.find_withtag("penarea_projection")
    coords = widget.canvas.coords(overlay_items[0])
    expected = [widget.model.canvas_xy(*corner)
                for corner in gui.projection_overlay_corners(
                    widget.model.rect, 90)]
    assert coords == pytest.approx(
        [p for corner in expected for p in corner])
    assert widget.orientation_degrees == 90
