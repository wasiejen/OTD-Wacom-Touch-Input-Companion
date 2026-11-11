"""U415 L1 (#407 ruling): redrawn tablet DRAWING geometry table.

Vector-style tkinter-native shapes (the PNG is construction reference
only and never displayed). Headless pins: geometry table invariants, the
button strip is LEFT ONLY (no mirrored empty strip), the pen-area rect
sits right of the strip inside the frame. RED-before at 7350fc5:
ImportError on TABLET_DRAWINGS / the new layout keys.
"""

import pytest

import gui
from gui import (
    PICTOGRAM_HEIGHT,
    PICTOGRAM_WIDTH,
    TABLET_DRAWINGS,
    OrientationWidget,
    pictogram_view_scale,
    resolve_drawing_size,
    tablet_pictogram_layout,
)
from touch_controller import PEN_SPECS_BY_PRODUCT, WIRELESS_TABLET_SIZES

# Digitizer sizes the drawing mirrors (PEN_SPECS mm), keyed by size name.
SPEC_BY_SIZE = {"small": 788, "medium": 789, "large": 791}


def test_tablet_drawings_cover_the_size_vocabulary_and_differ():
    assert set(TABLET_DRAWINGS) == set(WIRELESS_TABLET_SIZES)
    bodies = {size: TABLET_DRAWINGS[size]["body_mm"] for size in WIRELESS_TABLET_SIZES}
    assert bodies["small"][0] < bodies["medium"][0] < bodies["large"][0]
    assert bodies["small"][1] < bodies["medium"][1] < bodies["large"][1]


def test_tablet_drawings_digitizers_match_the_pen_specs_mm():
    for size, product_id in SPEC_BY_SIZE.items():
        specs = PEN_SPECS_BY_PRODUCT[product_id]
        assert TABLET_DRAWINGS[size]["digitizer_mm"] == (
            specs["width_mm"], specs["height_mm"])


def test_layout_default_frame_is_the_generalized_pictogram():
    layout = tablet_pictogram_layout(0)
    half_w, half_h = PICTOGRAM_WIDTH / 2, PICTOGRAM_HEIGHT / 2
    assert layout["rect"][0] == pytest.approx((-half_w, -half_h))
    assert layout["rect"][2] == pytest.approx((half_w, half_h))


def test_layout_strip_is_left_only_no_mirrored_column():
    # His 11-02 defect: the old graphic mirrored the button column onto the
    # right side WITHOUT buttons. The redrawn table keeps every control on
    # the left half; only the pen-area rect lives right of the strip.
    layout = tablet_pictogram_layout(0)
    assert all(x < 0 for _, x, _ in layout["buttons"])
    assert len(layout["buttons"]) == 8
    rightmost_button = max(x for _, x, _ in layout["buttons"])
    pen_left = min(x for x, _ in layout["pen_area"])
    assert pen_left > rightmost_button + layout["button_radius"]


def test_layout_pen_area_rect_inside_frame_matching_digitizer():
    size_table = TABLET_DRAWINGS["medium"]
    layout = tablet_pictogram_layout(0, size_table["body_mm"][0],
                                     size_table["body_mm"][1],
                                     digitizer=size_table["digitizer_mm"])
    xs = [x for x, _ in layout["rect"]]
    ys = [y for _, y in layout["rect"]]
    for x, y in layout["pen_area"]:
        assert min(xs) <= x <= max(xs)
        assert min(ys) <= y <= max(ys)
    pen_w = max(x for x, _ in layout["pen_area"]) - min(x for x, _ in layout["pen_area"])
    pen_h = max(y for _, y in layout["pen_area"]) - min(y for _, y in layout["pen_area"])
    assert pen_w == pytest.approx(size_table["digitizer_mm"][0])
    assert pen_h == pytest.approx(size_table["digitizer_mm"][1])


def test_layout_pen_area_and_buttons_rotate_with_the_angle():
    from touch_controller import rotate_point
    zero = tablet_pictogram_layout(0)
    quarter = tablet_pictogram_layout(90)
    for corner, rotated in zip(zero["pen_area"], quarter["pen_area"]):
        assert rotated == pytest.approx(rotate_point(*corner, 90, 0, 0))


# --- L2: rotation keeps the drawing inside the canvas (his open defect) -----

CANVAS_W = OrientationWidget.CANVAS_WIDTH
CANVAS_H = OrientationWidget.CANVAS_HEIGHT


def _all_drawn_features(layout):
    """(cx, cy, radius) for every item the widget draws (headless mirror)."""
    features = [(x, y, 0.0) for x, y in layout["rect"]]
    features += [(x, y, 0.0) for x, y in layout["pen_area"]]
    cx, cy = layout["center"]
    features.append((cx, cy, layout["dial_radius"]))
    features.append((cx, cy, layout["button_radius"] * 1.2))
    for _, x, y in layout["buttons"]:
        features.append((x, y, layout["button_radius"]))
    return features


def _fits_canvas(scale, layout):
    # Drawn geometry: a feature at mm (x, y) with mm radius r spans
    # (|x| + r) * scale canvas px (the widget scales the radius too).
    half_w, half_h = CANVAS_W / 2, CANVAS_H / 2
    for x, y, radius in _all_drawn_features(layout):
        if (abs(x) + radius) * scale > half_w or (abs(y) + radius) * scale > half_h:
            return False
    return True


@pytest.mark.parametrize("size", ("small", "medium", "large"))
@pytest.mark.parametrize("degrees", (0, 45, 90, 137, 180, 225, 270, 315))
def test_view_scale_fits_the_drawing_at_every_angle_and_size(degrees, size):
    # The U74d bug: scale fixed on the UNROTATED height, so at 90 deg the
    # drawing escaped the canvas. The view scale must refit per angle --
    # U415 STEP-1 P2-2: for EVERY table size, not just medium.
    drawing = TABLET_DRAWINGS[size]
    layout = tablet_pictogram_layout(degrees, drawing["body_mm"][0],
                                     drawing["body_mm"][1],
                                     digitizer=drawing["digitizer_mm"])
    scale = pictogram_view_scale(layout, CANVAS_W, CANVAS_H)
    assert _fits_canvas(scale, layout)
    assert scale > 0
    # And the fit is not degenerate: the widest extent reaches >= 60% of
    # the canvas half-extent (no shrinking to a dot).
    widest = max(abs(x) + r for x, _, r in _all_drawn_features(layout)) * scale
    assert widest >= 0.6 * min(CANVAS_W, CANVAS_H) / 2


@pytest.mark.parametrize("size", ("small", "large"))
def test_wired_size_combos_keep_the_left_only_strip(size):
    # U415 STEP-1 P2-1: the ruling's table entries, not just the legacy
    # default layout, must keep every control left and the pen area right.
    drawing = TABLET_DRAWINGS[size]
    layout = tablet_pictogram_layout(0, drawing["body_mm"][0],
                                     drawing["body_mm"][1],
                                     digitizer=drawing["digitizer_mm"])
    assert all(x < 0 for _, x, _ in layout["buttons"])
    rightmost_button = max(x for _, x, _ in layout["buttons"])
    pen_left = min(x for x, _ in layout["pen_area"])
    assert pen_left > rightmost_button + layout["button_radius"]


def test_layout_button_labels_do_not_renumber_under_rotation():
    for degrees in (0, 90, 180, 270, 137):
        layout = tablet_pictogram_layout(degrees)
        assert sorted(n for n, _, _ in layout["buttons"]) == list(range(1, 9))


@pytest.fixture(scope="module")
def tk_root():
    import tkinter as tk
    root = tk.Tk()
    root.withdraw()
    yield root
    root.destroy()


def test_widget_bbox_stays_inside_canvas_at_90_degrees(tk_root):
    widget = OrientationWidget(tk_root, degrees=0)
    widget.draw_degrees(90)
    bbox = widget.bbox("all")
    assert bbox is not None
    assert 0 <= bbox[0] and bbox[2] <= CANVAS_W
    assert 0 <= bbox[1] and bbox[3] <= CANVAS_H
    widget.destroy()


# --- L3: the drawing is sized by the SELECTED tablet size (#407 ruling) -----


def test_resolve_drawing_size_maps_products_and_wireless_choice():
    assert resolve_drawing_size(788) == "small"
    assert resolve_drawing_size(789) == "medium"
    assert resolve_drawing_size(791) == "large"
    # Wireless receiver 132 cannot detect its tablet: the user's chosen
    # size drives the drawing; unset falls back to the driver's medium.
    assert resolve_drawing_size(132, "small") == "small"
    assert resolve_drawing_size(132, "large") == "large"
    assert resolve_drawing_size(132, None) == "medium"
    assert resolve_drawing_size(None) == "medium"
    assert resolve_drawing_size(9999, "nonsense") == "medium"


def test_size_variants_produce_different_drawings():
    layouts = {}
    for size in ("small", "medium", "large"):
        drawing = TABLET_DRAWINGS[size]
        layouts[size] = tablet_pictogram_layout(0, drawing["body_mm"][0],
                                                drawing["body_mm"][1],
                                                digitizer=drawing["digitizer_mm"])
    pen_w = {size: max(x for x, _ in layouts[size]["pen_area"])
            - min(x for x, _ in layouts[size]["pen_area"])
            for size in layouts}
    assert pen_w["small"] < pen_w["medium"] < pen_w["large"]
    bodies = {size: layouts[size]["rect"][2][0] - layouts[size]["rect"][0][0]
              for size in layouts}
    assert bodies["small"] < bodies["medium"] < bodies["large"]


def test_widget_redraw_picks_up_a_changed_size(tk_root):
    # U415 STEP-1 P2-3 contract: the live-apply refresh hook sets
    # widget.size then calls draw_degrees; the redraw must follow the
    # new size (stale-size drawings kept the old scale until rebuild).
    widget = OrientationWidget(tk_root, degrees=90, size="medium")
    widget.size = "small"
    widget.draw_degrees(90)
    direct = OrientationWidget(tk_root, degrees=90, size="small")
    other = OrientationWidget(tk_root, degrees=90, size="large")
    assert widget._scale == pytest.approx(direct._scale, rel=1e-9)
    assert widget._scale != pytest.approx(other._scale, rel=0.02)
    widget.destroy()
    direct.destroy()
    other.destroy()


def test_widget_size_changes_the_drawing(tk_root):
    small = OrientationWidget(tk_root, degrees=0, size="small")
    large = OrientationWidget(tk_root, degrees=0, size="large")
    box_small, box_large = small.bbox("all"), large.bbox("all")
    assert box_small is not None and box_large is not None
    # The fit keeps both inside the canvas, but the drawn proportions
    # differ: the larger body needs a smaller mm scale -> compare the
    # scale the widgets computed, not the canvas bbox (both fill it).
    assert small._scale != pytest.approx(large._scale, rel=0.05)
    small.destroy()
    large.destroy()
