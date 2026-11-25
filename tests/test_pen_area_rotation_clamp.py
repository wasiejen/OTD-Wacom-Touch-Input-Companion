"""u467 (#467): pen-area editor BB clamp under rotated displays.

Maintainer live report (estate maintainer/done/2026-11-23_02-20_wtd_live_test.md):
at rotation 45/90 deg, once the bounding box (BB) is taller than the display
(DB), its lower corners can no longer cross the lower display edge (sometimes
the upper corners instead stick at the upper edge - depending on where the BB
was when the rotation was applied). Width analogue: a BB wider than the DB
makes the left or right edge uncrossable depending on the position at resize
time. A FAST drag forces the crossing (proves a clamp-math defect, not
intent). Reverting to 0 deg fixes everything (at 0 deg the aspect-locked BB
fits the display, where the old per-corner rule is edge-connected).

Root cause (model-level): the old placement rule "at least one rect CORNER
inside the display bbox" is the union of four per-corner origin regions. On
one axis that union is [by - rh, by + bh - rh] U [by, by + bh] - DISCONNECTED
whenever rh > bh (the gap is exactly the placements where the BB covers the
display vertically while every corner sits past both edges). The nearest
placement the old clamp returns for a target in that gap always falls back
into the CURRENT side's region, so a slow (per-event) drag pins one corner on
the edge forever; only a fast jump past the gap midpoint flips the regime -
hence the speed dependence and the position-dependent "stuck edge".

The fix (#467): the placement rule becomes "the rect must INTERSECT the
display bbox" (per-axis interval overlap, closed edges). For rh <= bh the two
formulations are IDENTICAL (the union is exactly the overlap interval), so the
proven-good 0 deg behavior is byte-preserved by the existing pins; for rh > bh
the valid set is the interval hull - connected, per-axis, speed-independent,
edge-symmetric. The tests below are math/object-level ONLY (synthetic
geometry, no live display settings, no input).

RED-before (pre-fix, per pin): slow crosses pin at the stuck edge instead of
terminating at the far edge; the reorient pins snap the center instead of
keeping it; the resize pin rejects the oversized growth. The last test
(shrink reject) is NOT a RED pin - it keeps the surviving rejection branch
honest (rejected for BOTH rules: the shrunk rect leaves the display entirely).
"""

import pytest

import gui
from gui import PenAreaCanvasModel
from wtd import mapping, protocol

# Single synthetic display (the report host is one rotated screen).
DISPLAY = (0, 0, 1920, 1080)
RECORDS = ["\\\\.\\DISPLAY1|0|0|1920x1080"]
SPECS = protocol.PEN_SPECS_BY_PRODUCT[791]  # PTH-851: 325.12 x 203.2 mm


def _model(box_mm, rect):
    return PenAreaCanvasModel(DISPLAY, RECORDS, 320.0, 120.0,
                              box_mm[0], box_mm[1], rect)


def _drag(m, total_dx, total_dy, step, events):
    """Feed `events` per-event canvas deltas of `step` px toward the target
    (the slow-drag lane: `_motion` calls move_by per coalesced event)."""
    sx = (total_dx / events) * m.scale
    sy = (total_dy / events) * m.scale
    for _ in range(events):
        m.move_by(sx, sy)


def _box(deg):
    return mapping.rotated_pen_box_mm(SPECS, deg)


# --- BB taller than the display: 90 deg (the report's primary case) ---

def test_tall_bb_90deg_slow_drag_down_reaches_the_display_bottom_edge():
    # 90 deg: the mm box swaps to portrait (203.2, 325.12) - aspect 1.6.
    # w = 700 -> rh = 1120 > 1080 (DB height). Start parked at the top
    # (lower corners inside near the top edge - the placement the old clamp
    # leaves after the rotation is applied); drag DOWN slowly, 8 px per
    # event. The lower corners must cross the lower edge and the drag must
    # terminate at ry = 1080 (upper corners riding the lower edge).
    # PRE-FIX RED: the drag pins at ry = 1080 - 1120 = -40 (the lower
    # corners stuck ON the lower edge, "can not cross" - the report).
    box = _box(90)
    assert box[1] > box[0]  # portrait after the swap
    rh = 700.0 * box[1] / box[0]
    assert rh > DISPLAY[3]
    m = _model(box, [400.0, -560.0, 700.0, rh])
    _drag(m, 0.0, 800.0, 8.0, 100)
    assert m.rect[1] == pytest.approx(-560.0 + 800.0)  # mid-drag free leg
    assert m.rect[1] + rh > DISPLAY[3]  # lower corners past the lower edge
    assert m.rect[1] < DISPLAY[3]  # upper corners still inside: legal leg
    _drag(m, 0.0, 8.0 * 200, 8.0, 200)
    assert m.rect == pytest.approx([400.0, 1080.0, 700.0, rh])


def test_tall_bb_90deg_slow_drag_up_reaches_the_display_top_edge():
    # Mirror leg (the report's variant 2: "the upper BB corners could not
    # cross the upper edge"). Start parked at the bottom; drag UP slowly.
    # PRE-FIX RED: pinned at ry = 0 (upper corners stuck ON the upper edge).
    box = _box(90)
    rh = 700.0 * box[1] / box[0]
    m = _model(box, [400.0, 800.0, 700.0, rh])
    _drag(m, 0.0, -8.0 * 110, 8.0, 110)
    assert m.rect[1] == pytest.approx(800.0 - 880.0)  # past the top edge
    assert m.rect[1] + rh < DISPLAY[3]  # lower corners still inside: legal
    _drag(m, 0.0, -8.0 * 190, 8.0, 190)
    assert m.rect == pytest.approx([400.0, -rh, 700.0, rh])


def test_tall_bb_45deg_slow_drag_down_crosses_the_lower_edge():
    # 45 deg: the rotated mm box is square (cos = sin); w = h = 1300 > 1080.
    # The slow drag must pass through the placement that COVERS the display
    # vertically (no corner inside - the old rule's gap) and terminate at
    # the bottom. PRE-FIX RED: pinned at ry = 1080 - 1300 = -220.
    box = _box(45)
    assert box[0] == pytest.approx(box[1])  # square rotated box
    m = _model(box, [300.0, -650.0, 1300.0, 1300.0])
    _drag(m, 0.0, 10.0 * 200, 10.0, 200)
    assert m.rect == pytest.approx([300.0, 1080.0, 1300.0, 1300.0])


# --- BB wider than the display: the left/right analogue (both sides) ---

def test_wide_bb_slow_drag_right_reaches_the_display_right_edge():
    # Wide aspect (the swapped-height analogue; the clamp is pure axis-
    # aligned geometry). w = 2200 > 1920. Start with the right corners
    # inside; drag RIGHT slowly. The old rule pins x at 1920 - 2200 = -280
    # (the "on right increase, left edge of DB not crossable" report line:
    # the rect can never slide right until its left edge rides the right
    # edge). PRE-FIX RED for the final position.
    box = (320.0, 100.0)
    m = _model(box, [-1300.0, 300.0, 2200.0, 687.5])
    _drag(m, 10.0 * 400, 0.0, 10.0, 400)
    assert m.rect == pytest.approx([1920.0, 300.0, 2200.0, 687.5])


def test_wide_bb_slow_drag_left_reaches_the_display_left_edge():
    # Mirror: start with the LEFT corners inside; drag LEFT slowly.
    # PRE-FIX RED: pinned at x = 0 (right corners stuck ON the left edge).
    box = (320.0, 100.0)
    m = _model(box, [1000.0, 300.0, 2200.0, 687.5])
    _drag(m, -10.0 * 400, 0.0, 10.0, 400)
    assert m.rect == pytest.approx([-2200.0, 300.0, 2200.0, 687.5])


# --- speed independence (report: "can be forced by moving fast enough") ---

def test_clamp_outcome_independent_of_drag_speed():
    # Same start, same total displacement, slow per-event vs one fast jump:
    # the outcome must be IDENTICAL (the old clamp diverged: the slow lane
    # pinned the gap's current-side endpoint, the fast jump landed in the
    # far region).
    box = _box(90)
    rh = 700.0 * box[1] / box[0]
    start = [400.0, -560.0, 700.0, rh]
    slow = _model(box, list(start))
    _drag(slow, 0.0, 1640.0, 8.0, 205)
    fast = _model(box, list(start))
    fast.move_by(0.0, 1640.0 * fast.scale)
    assert slow.rect == pytest.approx(fast.rect)
    assert slow.rect[1] == pytest.approx(1080.0)


# --- position independence when the rotation is APPLIED (reorient lane) ---

def test_rotation_apply_keeps_the_center_regardless_of_park_position():
    # reorient() calls match_box_aspect with the ROTATED mm box: a landscape
    # rect of w = 1728 (h = 1080) becomes the portrait 1080 x 1728 (taller
    # than the display). The old clamp snapped the CENTER per the position
    # the rotation was applied at (the report: "dependend on where the area
    # was before the apply of 45 deg"): the gap tie/pin moved ry to 0 or
    # -648 depending on cy. The fixed rule keeps the center (the placement
    # is legal the moment the rect intersects the display), so all three
    # park positions keep their centers. PRE-FIX RED: all three cy legs
    # snapped instead (-648 / 0 / 0).
    box0 = _box(0)
    box90 = _box(90)
    for cy in (300.0, 540.0, 780.0):
        m = _model(box0, [96.0, cy - 540.0, 1728.0, 1080.0])
        s = 1728.0 / box0[0]  # sensitivity of the PREVIOUS box (reorient)
        m.box_mm = (box90[0], box90[1])
        out = m.match_box_aspect(s)
        assert out == pytest.approx([420.0, cy - 864.0, 1080.0, 1728.0]), cy
        assert gui.rect_overlaps_display(out, m.display_bbox())


# --- position independence at RESIZE (the width-analogue report line) ---

def test_resize_grows_past_the_display_from_any_anchor_position():
    # "increased size while the BB crosses the lower edge -> the upper edge
    # is not longer crossable" analogue on the width: two rect placements
    # with the SAME wanted size. The old rule accepted the growth only with
    # the origin inside (position-dependent); the anchored one (origin
    # outside, right corners the only inside ones) was REJECTED mid-grow
    # while its wanted rect covers the display - the defect. Both now
    # accept: the wanted rect still covers the display (overlap).
    box = (320.0, 100.0)
    a = _model(box, [-500.0, 300.0, 900.0, 281.25])  # anchor: right corners
    handle = a.handle_canvas()
    wanted = 3000.0 - 900.0  # grow w by 2100 px in desktop units
    a.resize_handle_to(handle[0] + wanted * a.scale, handle[1])
    assert a.rect[0] == pytest.approx(-500.0) and a.rect[1] == pytest.approx(300.0)
    # The wanted w survives the per-event float round-trip within one px;
    # rh is the aspect-locked int(round(w x 100/320)) of THAT w.
    assert a.rect[2] == pytest.approx(3000.0, abs=2.0)
    assert a.rect[3] == pytest.approx(a.rect[2] * 100.0 / 320.0, abs=1.0)
    b = _model(box, [100.0, 300.0, 900.0, 281.25])  # origin inside
    handle = b.handle_canvas()
    b.resize_handle_to(handle[0] + wanted * b.scale, handle[1])
    assert b.rect[0] == pytest.approx(100.0) and b.rect[1] == pytest.approx(300.0)
    assert b.rect[2] == pytest.approx(3000.0, abs=2.0)
    assert b.rect[3] == pytest.approx(b.rect[2] * 100.0 / 320.0, abs=1.0)
    assert gui.rect_overlaps_display(a.rect, a.display_bbox())
    assert gui.rect_overlaps_display(b.rect, b.display_bbox())


def test_resize_shrink_that_leaves_the_display_is_still_rejected():
    # Honesty leg (NOT a RED pin - same outcome under the old rule): the
    # rejection branch survives for placements that leave NO overlap at all.
    # Shrinking the only-inside corner (500, 700) back past the origin
    # pushes the whole rect off the display -> rejected, the last valid size
    # stands (no popup), and the field echo returns it.
    box = (100.0, 100.0)
    m = _model(box, [-1000.0, -800.0, 1500.0, 1500.0])
    assert gui.rect_overlaps_display(m.rect, m.display_bbox())
    handle = m.handle_canvas()
    m.resize_handle_to(handle[0] - 1484.0 * m.scale, handle[1])  # w -> 16
    assert m.rect == pytest.approx([-1000.0, -800.0, 1500.0, 1500.0])
