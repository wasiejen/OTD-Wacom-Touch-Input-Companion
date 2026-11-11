"""U414 L2 (#406, approved design proposal §2): pen-area rectangle projection.

The rect lane maps the rotated digitizer box onto a stored virtual-desktop
rectangle [rx, ry, rw, rh] (px): off_x = rx*65535/screen_w,
scale_x = rw*65535/(extent_w*screen_w) (§2). Branch order keeps the
`pen_stretch_full_desktop` toggle first; without a rect (or with junk
metrics) the pre-U414 lanes run byte-unchanged. RED-before at faadcbd:
TypeError on the new `pen_area_rect` kwarg / ImportError on the new API.
"""

import pytest

import touch_controller
from touch_controller import (
    ActionDispatcher,
    DriverConfig,
    TabletState,
    PEN_AREA_MAP_PREFIX,
    cached_display_key,
    display_key_for_records,
    pen_area_norm_projection,
    select_live_pen_area_rect,
    update_pen_cursor,
)

ABS_FLAGS = 0x0001 | 0x8000 | 0x4000
VIRT = touch_controller.PEN_VIRTUAL_RESOLUTION
SPEC_791 = touch_controller.PEN_SPECS_BY_PRODUCT[791]
SCREEN = (0, 0, 3440, 1440)
# 16:10 rect consistent with the box aspect (325.12x203.2 mm -> 1.6) and
# fully inside the synthetic 3440x1440 desktop (500+1920, 120+1200):
RECT = [500, 120, 1920, 1200]
RECORDS = ["\\\\.\\DISPLAY1|0|0|3440x1440"]
IDENTITY = "0123456789ab"


@pytest.fixture
def config(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    return DriverConfig()


@pytest.fixture
def state():
    return TabletState()


def _map(state, config, specs, x, y, metrics, events, rect=None, stretch=False):
    config.feature_toggles["pen_absolute_mode"] = True
    config.feature_toggles["pen_stretch_full_desktop"] = stretch
    dispatcher = ActionDispatcher(state, config)
    update_pen_cursor(state, dispatcher, config, specs, x, y,
                      screen_metrics=metrics, pen_area_rect=rect)
    assert events and events[-1][0] == "mouse" and events[-1][1] == ABS_FLAGS
    return events[-1][3], events[-1][4]


def _px(nx, screen_w):
    """Normalized 0..65535 value back to desktop px (integer rounding)."""
    return round(nx * screen_w / VIRT)


def test_rect_corners_land_at_the_rect_bounds(state, config, stub_send_input):
    nx0, ny0 = _map(state, config, SPEC_791, 0, 0, SCREEN, stub_send_input, RECT)
    stub_send_input.clear()
    nx1, ny1 = _map(state, config, SPEC_791, SPEC_791["max_x"],
                    SPEC_791["max_y"], SCREEN, stub_send_input, RECT)
    assert (_px(nx0, 3440), _px(ny0, 1440)) == (RECT[0], RECT[1])
    assert (_px(nx1, 3440), _px(ny1, 1440)) == (RECT[0] + RECT[2],
                                                RECT[1] + RECT[3])


def test_rect_lane_px_per_mm_equal_both_axes(state, config, stub_send_input):
    nx0, ny0 = _map(state, config, SPEC_791, 0, 0, SCREEN, stub_send_input, RECT)
    stub_send_input.clear()
    nx1, ny1 = _map(state, config, SPEC_791, SPEC_791["max_x"],
                    SPEC_791["max_y"], SCREEN, stub_send_input, RECT)
    box_mm = touch_controller.rotated_pen_box_mm(SPEC_791, 0)
    scale_x = (_px(nx1, 3440) - _px(nx0, 3440)) / box_mm[0]
    scale_y = (_px(ny1, 1440) - _px(ny0, 1440)) / box_mm[1]
    assert scale_x == pytest.approx(scale_y, rel=2e-3)


def test_rect_lane_survives_90deg_rotation(state, config, stub_send_input):
    # Rotation permutes which DEVICE corner is the box corner (the rotation
    # transform runs upstream of update_pen_cursor), so pin the mapped
    # output as the SET of the four rect corners fed the four rotated
    # device corners through the same upstream transform.
    config.orientation_degrees = 90
    center = touch_controller.pen_rotation_center(SPEC_791)
    corners = {(0, 0), (SPEC_791["max_x"], 0), (0, SPEC_791["max_y"]),
               (SPEC_791["max_x"], SPEC_791["max_y"])}
    mapped = set()
    for (dx, dy) in sorted(corners):
        rx_, ry_ = touch_controller.apply_orientation(
            (dx, dy), config, center)
        nx, ny = _map(state, config, SPEC_791, rx_, ry_, SCREEN,
                      stub_send_input, RECT)
        stub_send_input.clear()
        mapped.add((_px(nx, 3440), _px(ny, 1440)))
    assert mapped == {(RECT[0], RECT[1]), (RECT[0] + RECT[2], RECT[1]),
                      (RECT[0], RECT[1] + RECT[3]),
                      (RECT[0] + RECT[2], RECT[1] + RECT[3])}


def test_stretch_toggle_beats_the_rect_lane(state, config, stub_send_input):
    nx0, ny0 = _map(state, config, SPEC_791, 0, 0, SCREEN, stub_send_input,
                    RECT, stretch=True)
    assert (nx0, ny0) == (0, 0)
    stub_send_input.clear()
    nx1, ny1 = _map(state, config, SPEC_791, SPEC_791["max_x"],
                    SPEC_791["max_y"], SCREEN, stub_send_input,
                    RECT, stretch=True)
    assert (nx1, ny1) == (VIRT, VIRT)


def test_rect_with_junk_metrics_keeps_the_degraded_lane(state, config,
                                                        stub_send_input, monkeypatch):
    monkeypatch.setattr(touch_controller, "virtual_desktop_metrics", lambda: None)
    ny_near = _map(state, config, SPEC_791, 0, 0, None, stub_send_input, RECT)[1]
    stub_send_input.clear()
    ny_far = _map(state, config, SPEC_791, 0, SPEC_791["max_y"], None,
                  stub_send_input, RECT)[1]
    # stretch_unmeasured full-fill, NOT a rect projection (near 0, far full).
    assert (ny_near, ny_far) == (0, VIRT)


def test_no_rect_control_is_the_isotropic_lane(state, config, stub_send_input):
    # Control: with pen_area_rect=None the U376 lane must be byte-identical
    # to its pinned isotropic output (used_w centered contain on 3440x1440).
    box_mm = touch_controller.rotated_pen_box_mm(SPEC_791, 0)
    scale = min(3440 / box_mm[0], 1440 / box_mm[1])
    used_w = SPEC_791["max_x"] / 200.0 * scale
    off_x = (3440 - used_w) / 2.0 * VIRT / 3440
    nx0, _ = _map(state, config, SPEC_791, 0, 0, SCREEN, stub_send_input)
    assert abs(nx0 - int(off_x)) <= 1


def test_projection_math_matches_proposal_formula():
    off_x, scale_x, off_y, scale_y = pen_area_norm_projection(
        RECT, 65024, 40640, 3440, 1440)
    assert off_x == pytest.approx(RECT[0] * 65535 / 3440)
    assert scale_x == pytest.approx(RECT[2] * 65535 / (65024 * 3440))
    assert off_y == pytest.approx(RECT[1] * 65535 / 1440)
    assert scale_y == pytest.approx(RECT[3] * 65535 / (40640 * 1440))


def test_diag_names_the_pen_area_mode_with_rect_px_mm(state, config,
                                                       stub_send_input,
                                                       capsys, monkeypatch):
    monkeypatch.setenv(touch_controller.PEN_MAP_DIAG_ENV, "1")
    monkeypatch.setattr(touch_controller, "monitor_count", lambda: 2)
    state.pen_product_id = 791
    capsys.readouterr()
    _map(state, config, SPEC_791, 0, 0, SCREEN, stub_send_input, RECT)
    out = capsys.readouterr().out
    assert out.startswith("pen_abs_map mode=pen_area product=791")
    assert ("px_mm_x=%.4f px_mm_y=%.4f band_x=na band_y=na\n"
            % (RECT[2] / 325.12, RECT[3] / 203.2)) in out


def test_live_rect_selection_exact_match_and_none(cfg_state_config, monkeypatch):
    cfg, state = cfg_state_config
    key = display_key_for_records(RECORDS)
    cfg.set_pen_area_entry(IDENTITY, key, RECORDS, RECT)
    state.pen_identity = IDENTITY
    monkeypatch.setattr(touch_controller, "monitor_records", lambda: list(RECORDS))
    assert select_live_pen_area_rect(cfg, state.pen_identity) == RECT
    monkeypatch.setattr(touch_controller, "monitor_records",
                        lambda: ["\\\\.\\DISPLAY1|0|0|1920x1080"])
    touch_controller.reset_pen_area_key_cache()
    assert select_live_pen_area_rect(cfg, state.pen_identity) is None
    assert select_live_pen_area_rect(cfg, None) is None


@pytest.fixture
def cfg_state_config(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    return DriverConfig(), TabletState()


def test_rect_datum_is_virtual_desktop_origin_relative(state, config,
                                                        stub_send_input):
    # U414 STEP-2 P1: rx/ry are pixels from the virtual-desktop origin
    # (metrics left/top), NOT absolute coordinates. On the maintainer's
    # pivoted layout the virtual origin is (-1440, 0); an implementation
    # that baked the absolute datum in would land the near corner 1440 px
    # left of the rect here while the (0,0)-origin tests pass.
    origin_screen = (-1440, 0, 4880, 2560)
    nx0, ny0 = _map(state, config, SPEC_791, 0, 0, origin_screen,
                    stub_send_input, RECT)
    stub_send_input.clear()
    nx1, ny1 = _map(state, config, SPEC_791, SPEC_791["max_x"],
                    SPEC_791["max_y"], origin_screen, stub_send_input, RECT)
    assert (_px(nx0, 4880), _px(ny0, 2560)) == (RECT[0], RECT[1])
    assert (_px(nx1, 4880), _px(ny1, 2560)) == (RECT[0] + RECT[2],
                                                RECT[1] + RECT[3])


def test_relative_lane_ignores_the_rect(state, config, stub_send_input):
    # U414 STEP-2 P2-3: explicit relative control - the rect must never
    # touch the relative path (no absolute flags, no rect-sized deltas).
    config.feature_toggles["pen_absolute_mode"] = False
    config.feature_toggles["pen_stretch_full_desktop"] = False
    dispatcher = ActionDispatcher(state, config)
    # The first report is the origin (no delta); moving positions after it
    # must emit MOVE-only deltas.
    for step in range(6):
        update_pen_cursor(state, dispatcher, config, SPEC_791, 2000 * step,
                          1500 * step, screen_metrics=SCREEN, pen_area_rect=RECT)
    assert stub_send_input
    for event in stub_send_input:
        assert event[0] == "mouse"
        assert event[1] == 0x0001  # MOUSEEVENTF_MOVE only, no ABSOLUTE bit


def test_cached_display_key_caches_none_until_ttl(monkeypatch):
    # U414 STEP-2 P2-2: the None path must not re-walk monitors per report
    # on enumeration-failed hosts.
    calls = []

    def fake_records():
        calls.append(1)
        return []

    monkeypatch.setattr(touch_controller, "monitor_records", fake_records)
    touch_controller.reset_pen_area_key_cache()
    assert cached_display_key(now=100.0) is None
    assert cached_display_key(now=101.0) is None
    assert len(calls) == 1
    assert cached_display_key(now=103.0) is None
    assert len(calls) == 2


def test_cached_display_key_walks_once_per_ttl(monkeypatch):
    calls = []

    def fake_records():
        calls.append(1)
        return list(RECORDS)

    monkeypatch.setattr(touch_controller, "monitor_records", fake_records)
    touch_controller.reset_pen_area_key_cache()
    key = display_key_for_records(RECORDS)
    assert cached_display_key(now=100.0) == key
    assert cached_display_key(now=101.0) == key
    assert len(calls) == 1
    assert cached_display_key(now=103.0) == key
    assert len(calls) == 2
