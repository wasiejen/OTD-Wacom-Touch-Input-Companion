"""U376 (#380): absolute-mode isotropy and centering on ultrawide geometry.

Every case feeds synthetic virtual-desktop PIXEL extents through the real
absolute mapping consumer with SendInput stubbed by the conftest fixture --
the session inject guard stays armed, so no live desktop event is ever sent.

Pre-fix record (scratch probe on the UNCHANGED tree @ wtd_combined 004d6b9,
791 extents 65024x40640 = 325.12 x 203.2 mm through update_pen_cursor):
the emitted pair was nx 0..65535 / ny 12287..53247 for EVERY desktop, i.e.
px/mm 10.5807 x 4.4292 on 3440x1440 (ratio 2.3889 = 3440/1440), 15.7480 x
4.4292 on 5120x1440 (ratio 3.5555), 21.1614 x 4.4292 on a 6880x1440 duo
(ratio 4.7777); a 45 deg pen stroke landed at 22.71 deg / 15.71 deg /
11.82 deg and the top and bottom bands were each 18.75 % of the display
height. Only a square-aspect desktop gave ratio 1.0000, which is exactly the
signature of parity computed in the 0..65535 space Windows stretches to the
desktop aspect.
"""

import math

import pytest

import touch_controller
from touch_controller import (
    ActionDispatcher,
    DriverConfig,
    TabletState,
    apply_orientation,
    pen_rotation_center,
    pen_specs_for_product,
    update_pen_cursor,
)

ABS_FLAGS = 0x0001 | 0x8000 | 0x4000
VIRT = touch_controller.PEN_VIRTUAL_RESOLUTION
SPEC_791 = touch_controller.PEN_SPECS_BY_PRODUCT[791]
ULTRAWIDE_3440 = (0, 0, 3440, 1440)
ULTRAWIDE_5120 = (0, 0, 5120, 1440)
SQUARE_1440 = (0, 0, 1440, 1440)


@pytest.fixture
def config(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    return DriverConfig()


@pytest.fixture
def state():
    return TabletState()


def _map_corner(state, config, specs, x, y, metrics, events, stretch=False):
    """One absolute mapping through the consumer; returns the emitted pair."""
    config.feature_toggles["pen_absolute_mode"] = True
    config.feature_toggles["pen_stretch_full_desktop"] = stretch
    dispatcher = ActionDispatcher(state, config)
    update_pen_cursor(state, dispatcher, config, specs, x, y,
                      screen_metrics=metrics)
    assert events and events[-1][0] == "mouse" and events[-1][1] == ABS_FLAGS
    return events[-1][3], events[-1][4]


def axis_scales_px_per_mm(state, config, specs, metrics, events, stretch=False):
    """Map the two opposite box corners and turn them into px-per-mm scales.

    The px conversion happens here, on the assumed desktop geometry, so the
    measurement never trusts the mapping's own arithmetic.
    Returns (scale_x_px_per_mm, scale_y_px_per_mm, (nx0, ny0, nx1, ny1)).
    """
    nx0, ny0 = _map_corner(state, config, specs, 0, 0, metrics, events, stretch)
    events.clear()
    nx1, ny1 = _map_corner(state, config, specs, specs["max_x"], specs["max_y"],
                           metrics, events, stretch)
    scale_x = (nx1 - nx0) / VIRT * metrics[2] / specs["width_mm"]
    scale_y = (ny1 - ny0) / VIRT * metrics[3] / specs["height_mm"]
    return scale_x, scale_y, (nx0, ny0, nx1, ny1)


def assert_isotropic_and_45deg(scale_x, scale_y):
    """Equal px-per-mm on both axes means a 45 deg pen stroke maps at 45 deg."""
    assert scale_x == pytest.approx(scale_y, rel=1e-4)
    angle = math.degrees(math.atan(scale_y / scale_x))
    assert angle == pytest.approx(45.0, abs=0.1)


def band_px(low_norm, high_norm, screen_px):
    """Symmetric unreachable bands in px from a mapped axis' emitted pair."""
    return (low_norm / VIRT * screen_px, (VIRT - high_norm) / VIRT * screen_px)


# --- leg 2: isotropic centered default ---------------------------------------

def test_791_ultrawide_3440x1440_is_isotropic_and_centered(state, config, stub_send_input):
    # Fit rule: scale = min(3440/325.12, 1440/203.2) = 1440/203.2 px/mm, so
    # height spans the desktop and the width leaves two equal side bands of
    # (3440 - 325.12 * 1440/203.2)/2 = 568 px each. Pre-fix this pair was
    # 10.5807 x 4.4292 px/mm (ratio 2.3889), a 45 deg line ran 22.71 deg.
    scale = 1440 / 203.2
    scale_x, scale_y, corners = axis_scales_px_per_mm(
        state, config, SPEC_791, ULTRAWIDE_3440, stub_send_input)
    assert scale_x == pytest.approx(scale, rel=1e-4)
    assert scale_y == pytest.approx(scale, rel=1e-4)
    assert_isotropic_and_45deg(scale_x, scale_y)
    band_left, band_right = band_px(corners[0], corners[2], 3440)
    assert band_left == pytest.approx(568.0, abs=1.0)
    assert band_right == pytest.approx(568.0, abs=1.0)
    assert band_left == pytest.approx(band_right, abs=1.0)
    # The fit runs on height, so the vertical axis reaches both screen edges.
    assert corners[1] == 0
    assert corners[3] == VIRT


def test_791_ultrawide_5120x1440_is_isotropic_and_centered(state, config, stub_send_input):
    # Same fit rule: scale = min(5120/325.12, 1440/203.2) = 1440/203.2 px/mm.
    # Pre-fix the ratio here was 3.5555 and a 45 deg line ran 15.71 deg.
    scale = 1440 / 203.2
    scale_x, scale_y, corners = axis_scales_px_per_mm(
        state, config, SPEC_791, ULTRAWIDE_5120, stub_send_input)
    assert scale_x == pytest.approx(scale, rel=1e-4)
    assert scale_y == pytest.approx(scale, rel=1e-4)
    assert_isotropic_and_45deg(scale_x, scale_y)
    band_left, band_right = band_px(corners[0], corners[2], 5120)
    expected_band = (5120 - 325.12 * scale) / 2
    assert band_left == pytest.approx(expected_band, abs=1.0)
    assert band_right == pytest.approx(expected_band, abs=1.0)
    assert corners[1] == 0
    assert corners[3] == VIRT


def test_791_square_desktop_vertical_bands_are_equal(state, config, stub_send_input):
    # The non-zero vertical-band case: a 1:1 desktop fits the 16:10 box on
    # width, so the unreachable band sits top and bottom, equal, and matches
    # aspect math: used_h = 203.2 * 1440/325.12 = 900 px -> 270 px each side.
    scale = 1440 / 325.12
    scale_x, scale_y, corners = axis_scales_px_per_mm(
        state, config, SPEC_791, SQUARE_1440, stub_send_input)
    assert_isotropic_and_45deg(scale_x, scale_y)
    band_top, band_bottom = band_px(corners[1], corners[3], 1440)
    expected_band = (1440 - 203.2 * scale) / 2
    assert expected_band == pytest.approx(270.0, abs=0.5)
    assert band_top == pytest.approx(expected_band, abs=1.0)
    assert band_bottom == pytest.approx(expected_band, abs=1.0)
    assert corners[0] == 0
    assert corners[2] == VIRT


def _map_rotated_corner(state, config, specs, x, y, metrics, events, stretch=False):
    """Rotate a raw device corner (the process_pen_report order), then map it.

    update_pen_cursor expects the ALREADY rotated coordinate: orientation is
    applied upstream in process_pen_report, so a rotated-box pin must apply
    the same shared transform before mapping.
    """
    px, py = apply_orientation((x, y), config, pen_rotation_center(specs))
    return _map_corner(state, config, specs, px, py, metrics, events, stretch)


def test_791_rotated_90_ultrawide_is_isotropic_and_centered(state, config, stub_send_input):
    # 90 deg orientation swaps the mapped box: mm box 203.2 x 325.12, fit on
    # height -> scale = 1440/325.12 px/mm, so the tablet's 325.12 mm edge
    # covers the full height and its 203.2 mm edge 900 px of the width; the
    # centered block leaves (3440 - 900)/2 = 1270 px bands left and right.
    config.orientation_degrees = 90
    metrics = ULTRAWIDE_3440
    scale = 1440 / SPEC_791["width_mm"]
    nx0, ny0 = _map_rotated_corner(state, config, SPEC_791, 0, 0,
                                   metrics, stub_send_input)
    stub_send_input.clear()
    nx1, ny1 = _map_rotated_corner(state, config, SPEC_791, SPEC_791["max_x"], 0,
                                   metrics, stub_send_input)
    stub_send_input.clear()
    nx2, ny2 = _map_rotated_corner(state, config, SPEC_791, 0, SPEC_791["max_y"],
                                   metrics, stub_send_input)
    # The tablet's long edge (width_mm) now runs along the screen's y axis and
    # its short edge (height_mm) along x: one px/mm scale on both.
    dy_px = (ny1 - ny0) / VIRT * metrics[3]
    dx_px = abs(nx2 - nx0) / VIRT * metrics[2]
    assert abs(nx1 - nx0) / VIRT * metrics[2] < 2.0
    assert abs(ny2 - ny0) / VIRT * metrics[3] < 2.0
    scale_x = dx_px / SPEC_791["height_mm"]
    scale_y = dy_px / SPEC_791["width_mm"]
    assert scale_x == pytest.approx(scale, rel=1e-4)
    assert scale_y == pytest.approx(scale, rel=1e-4)
    assert_isotropic_and_45deg(scale_x, scale_y)
    # Both mapped corners stay inside the centered block, whose edges sit at
    # expected_band and metrics[2] - expected_band px.
    expected_band = (metrics[2] - SPEC_791["height_mm"] * scale) / 2
    assert expected_band == pytest.approx(1270.0, abs=0.5)
    leftmost = min(nx0, nx1, nx2) / VIRT * metrics[2]
    rightmost = max(nx0, nx1, nx2) / VIRT * metrics[2]
    assert leftmost == pytest.approx(expected_band, abs=1.0)
    assert rightmost == pytest.approx(metrics[2] - expected_band, abs=1.0)
    assert ny0 == 0
    assert ny1 == VIRT


def test_791_stretch_optin_control_stays_anisotropic(state, config, stub_send_input):
    # Control (stretch semantics unchanged): per-axis fill, so on 3440x1440
    # the two scales differ -- 3440/325.12 x 1440/203.2, ratio 1.4931 -- and
    # both box corners land on the desktop edges.
    scale_x, scale_y, corners = axis_scales_px_per_mm(
        state, config, SPEC_791, ULTRAWIDE_3440, stub_send_input, stretch=True)
    assert scale_x == pytest.approx(3440 / 325.12, rel=1e-4)
    assert scale_y == pytest.approx(1440 / 203.2, rel=1e-4)
    assert scale_x / scale_y == pytest.approx(1.4931, rel=1e-3)
    assert corners == (0, 0, VIRT, VIRT)


def test_autoscale_grown_box_keeps_one_px_per_mm_scale(state, config, stub_send_input):
    # Autoscale safety-net lane: growing the box grows the COUNT range and
    # keeps the DECLARED mm area -- the repo's pinned density model, the one
    # the relative lane uses for the same grown box. Under that model the
    # isotropic fit still runs one scale on both axes: the grown 70000 x 50000
    # count box (= 325.12 x 203.2 mm) fits 3440x1440 on height, so x covers
    # 325.12 * 1440/203.2 = 2304 px inside two equal 568 px bands.
    grown = dict(SPEC_791)
    grown["max_x"] = 70000
    grown["max_y"] = 50000
    scale = 1440 / 203.2
    scale_x, scale_y, corners = axis_scales_px_per_mm(
        state, config, grown, ULTRAWIDE_3440, stub_send_input)
    assert scale_x == pytest.approx(scale, rel=1e-4)
    assert scale_y == pytest.approx(scale, rel=1e-4)
    assert_isotropic_and_45deg(scale_x, scale_y)
    band_left, band_right = band_px(corners[0], corners[2], 3440)
    assert band_left == pytest.approx(568.0, abs=1.0)
    assert band_right == pytest.approx(568.0, abs=1.0)


# --- leg 3: wireless medium-preset variant -----------------------------------

def test_wireless_medium_default_preset_is_isotropic_but_clips_the_large_box(
        state, config, stub_send_input, capsys):
    # Wireless verdict (U376 leg 3): the receiver id 132 cannot detect the
    # paired tablet, so wireless_pen_size (default "medium") names the extents
    # preset. The mapping lane is the SAME isotropic lane, so the vertical
    # squash the maintainer saw disappears with the leg-2 fix; what stays is
    # preset clipping -- the 789 box ends at 44704 counts, 44704/65024 =
    # 68.75 % of the Large tablet's width, and coordinates past it clamp.
    capsys.readouterr()  # drain the config-load banner
    assert config.wireless_pen_size == "medium"
    specs = pen_specs_for_product(132, config)
    assert specs is touch_controller.PEN_SPECS_BY_PRODUCT[789]
    metrics = ULTRAWIDE_3440
    scale = 1440 / 139.7
    scale_x, scale_y, _ = axis_scales_px_per_mm(
        state, config, specs, metrics, stub_send_input)
    assert scale_x == pytest.approx(scale, rel=1e-4)
    assert scale_y == pytest.approx(scale, rel=1e-4)
    assert_isotropic_and_45deg(scale_x, scale_y)
    # The preset box edge maps to the right edge of the fitted block:
    # (3440 - 223.52 * scale)/2 = 568 px of band, i.e. the same block a
    # correctly-sized Large preset produces, reached by 68.75 % of the pen area.
    nx_box, _ = _map_corner(state, config, specs, 44704, 0, metrics, stub_send_input)
    stub_send_input.clear()
    nx_large, _ = _map_corner(state, config, specs, 65024, 0, metrics, stub_send_input)
    assert nx_box / VIRT * 3440 == pytest.approx(3440 - 568.0, abs=1.0)
    assert nx_large == VIRT  # past the preset box: clamped to the desktop edge


# --- metrics read and degraded lane ------------------------------------------

def test_virtual_desktop_metrics_reads_the_documented_indices(monkeypatch):
    reads = []
    table = {76: 0, 77: -100, 7: 3440, 8: 1440, 2: 2}

    def fake(index):
        reads.append(index)
        return table[index]

    monkeypatch.setattr(touch_controller.ctypes.windll.user32, "GetSystemMetrics", fake)
    assert touch_controller.virtual_desktop_metrics() == (0, -100, 3440, 1440)
    assert reads == [76, 77, 7, 8]
    assert touch_controller.monitor_count() == 2
    assert reads == [76, 77, 7, 8, 2]
    table[7] = 0
    assert touch_controller.virtual_desktop_metrics() is None
    # Positive junk must not drive the live mapping: a 3 x 3 pair was measured
    # on this project's headless test host, and it routes to the degraded lane.
    table.update({7: 3, 8: 3})
    assert touch_controller.virtual_desktop_metrics() is None


def test_virtual_desktop_metrics_is_none_when_the_host_offers_none(monkeypatch):
    def raise_error(index):
        raise OSError("no metrics")

    monkeypatch.setattr(touch_controller.ctypes.windll.user32, "GetSystemMetrics", raise_error)
    assert touch_controller.virtual_desktop_metrics() is None
    assert touch_controller.monitor_count() is None


def test_missing_metrics_keep_the_normalized_parity_lane(state, config, stub_send_input,
                                                         monkeypatch):
    # Degraded lane: with no usable desktop metrics the mapping cannot know
    # the desktop aspect, so it keeps equal FRACTIONS per mm -- the box aspect
    # stays true and px/mm parity holds only for a square-aspect desktop.
    # These numbers are the pre-fix default for ANY desktop (band fraction
    # 40640/65024 = 0.625 of the height).
    monkeypatch.setattr(touch_controller, "virtual_desktop_metrics", lambda: None)
    # Literal corners, not a recomputed formula: scale = 65535/65024 counts,
    # offset_y = (65535 - 65535 * 40640/65024)/2 = 12287.8125 and the far
    # corner 12287.8125 + 40959.375 = 53247.1875, both truncated by int().
    nx0, ny0 = _map_corner(state, config, SPEC_791, 0, 0, None, stub_send_input)
    stub_send_input.clear()
    nx1, ny1 = _map_corner(state, config, SPEC_791, SPEC_791["max_x"],
                           SPEC_791["max_y"], None, stub_send_input)
    assert (nx0, ny0) == (0, 12287)
    assert (nx1, ny1) == (VIRT, 53247)


# --- one-shot mapping diagnostic ---------------------------------------------

def test_mapping_diagnostic_line_names_mode_scales_and_bands_once(
        state, config, stub_send_input, capsys, monkeypatch):
    monkeypatch.setenv(touch_controller.PEN_MAP_DIAG_ENV, "1")
    monkeypatch.setattr(touch_controller, "virtual_desktop_metrics",
                        lambda: ULTRAWIDE_3440)
    monkeypatch.setattr(touch_controller, "monitor_count", lambda: 2)
    state.pen_product_id = 791
    state.pen_obs_max_x = 65024
    state.pen_obs_max_y = 40640
    _map_corner(state, config, SPEC_791, 0, 0, ULTRAWIDE_3440, stub_send_input)
    out = capsys.readouterr().out
    scale = 1440 / 203.2
    assert out == (
        "pen_abs_map mode=isotropic product=791 declared=65024x40640 "
        "box=65024x40640 box_mm=325.1200x203.2000 obs=65024x40640 "
        "screen=3440x1440 monitors=2 px_mm_x=%.4f px_mm_y=%.4f "
        "band_x=568.00_px band_y=0.00_px\n" % (scale, scale))
    _map_corner(state, config, SPEC_791, 1000, 1000, ULTRAWIDE_3440, stub_send_input)
    assert capsys.readouterr().out == ""


def test_mapping_diagnostic_stays_quiet_when_gated_off(
        state, config, stub_send_input, capsys, monkeypatch):
    monkeypatch.delenv(touch_controller.PEN_MAP_DIAG_ENV, raising=False)
    monkeypatch.setattr(touch_controller, "virtual_desktop_metrics",
                        lambda: ULTRAWIDE_3440)
    _map_corner(state, config, SPEC_791, 0, 0, ULTRAWIDE_3440, stub_send_input)
    assert capsys.readouterr().out == ""


def test_mapping_diagnostic_names_the_stretch_branch_once(
        state, config, stub_send_input, capsys, monkeypatch):
    monkeypatch.setenv(touch_controller.PEN_MAP_DIAG_ENV, "1")
    monkeypatch.setattr(touch_controller, "virtual_desktop_metrics",
                        lambda: ULTRAWIDE_3440)
    monkeypatch.setattr(touch_controller, "monitor_count", lambda: 2)
    state.pen_product_id = 791
    _map_corner(state, config, SPEC_791, 0, 0, ULTRAWIDE_3440,
                stub_send_input, stretch=True)
    out = capsys.readouterr().out
    assert out.startswith("pen_abs_map mode=stretch product=791")
    assert ("px_mm_x=%.4f px_mm_y=%.4f band_x=0.00_px band_y=0.00_px"
            % (3440 / 325.12, 1440 / 203.2)) in out
