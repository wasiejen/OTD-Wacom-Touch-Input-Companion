"""U414 L2 (#406) + U416 L3 (#406 defect round 1): pen-area rect projection.

The rect lane maps the rotated digitizer box onto a stored FRACTIONAL
virtual-desktop rectangle rect_frac=[rx, ry, rw, rh] (0..1 of the union
bounds): off_x = rx*65535, scale_x = rw*65535/extent_w (§2 generalized -
the normalized 0..65535 space IS the MOUSEEVENTF_VIRTUALDESK space, so the
pen path needs NO display metrics and runs on junk-metrics hosts).
`pen_area_norm_projection` stays the general px form (screen dims 1,1 ==
frac form - pinned). Branch order keeps the `pen_stretch_full_desktop`
toggle first; with no rect the pre-U414 lanes (isotropic / stretch_un-
measured) run byte-unchanged. U416 re-pin: RED-before 03c0a40 = the old
px-kwarg contract (junk-metrics pin used to assert the degraded lane).
"""

import pytest

from wtd.config import (DriverConfig, PEN_AREA_MAP_PREFIX)
from wtd.gestures import TabletState
from wtd.mapping import (cached_display_key, display_key_for_records, pen_area_norm_projection, select_live_pen_area_rect, update_pen_cursor)
from wtd.sender import ActionDispatcher
from wtd import config
from wtd import mapping
from wtd import protocol

ABS_FLAGS = 0x0001 | 0x8000 | 0x4000
VIRT = protocol.PEN_VIRTUAL_RESOLUTION
SPEC_791 = protocol.PEN_SPECS_BY_PRODUCT[791]
SCREEN = (0, 0, 3440, 1440)
# 16:10 rect consistent with the box aspect (325.12x203.2 mm -> 1.6) and
# fully inside the synthetic 3440x1440 desktop (500+1920, 120+1200):
RECT = [500, 120, 1920, 1200]
RECT_FRAC = [RECT[0] / 3440, RECT[1] / 1440, RECT[2] / 3440, RECT[3] / 1440]
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
    # WTD-BATCH: flush the parked absolute move (see test_pen_absolute_isotropy).
    dispatcher.flush_pending_move()
    assert events and events[-1][0] == "mouse" and events[-1][1] == ABS_FLAGS
    return events[-1][3], events[-1][4]


def _px(nx, screen_w):
    """Normalized 0..65535 value back to desktop px (integer rounding)."""
    return round(nx * screen_w / VIRT)


def test_rect_corners_land_at_the_rect_bounds(state, config, stub_send_input):
    nx0, ny0 = _map(state, config, SPEC_791, 0, 0, SCREEN, stub_send_input, RECT_FRAC)
    stub_send_input.clear()
    nx1, ny1 = _map(state, config, SPEC_791, SPEC_791["max_x"],
                    SPEC_791["max_y"], SCREEN, stub_send_input, RECT_FRAC)
    assert (_px(nx0, 3440), _px(ny0, 1440)) == (RECT[0], RECT[1])
    assert (_px(nx1, 3440), _px(ny1, 1440)) == (RECT[0] + RECT[2],
                                                RECT[1] + RECT[3])


# PENAREA (2026-11-16): the editor may place the rect PARTLY outside the
# display union, so rect fractions leave 0..1. Documented v1 semantics of
# the mapper for the off-screen part (see update_pen_cursor docstring).
OFFSCREEN_FRAC = [-0.5, 0.0, 1.0, 0.5]


def test_offscreen_rect_part_maps_to_the_desktop_edge_dead_zone(state, config,
                                                                stub_send_input):
    # The off-screen rect half aims at virtual-desktop coords OUTSIDE the
    # monitors; the mapper's existing nx/ny clamp pins the result to 0 or
    # 65535, so the cursor stands at the virtual-desktop edge while the pen
    # travels the off-screen part - a usable dead zone, no per-monitor
    # clipping (accepted v1 semantics, maintainer-requested PENAREA unit).
    # rx = -0.5: the pen's box-left corner aims 1720 px left of the desktop.
    nx, ny = _map(state, config, SPEC_791, 0, 0, SCREEN, stub_send_input,
                  OFFSCREEN_FRAC)
    assert (nx, ny) == (0, 0)
    # The on-screen corner still maps through normally (rx+rw = 0.5 of the
    # width, ry+rh = 0.5 of the height -> 1720, 720).
    stub_send_input.clear()
    nx, ny = _map(state, config, SPEC_791, SPEC_791["max_x"],
                  SPEC_791["max_y"], SCREEN, stub_send_input, OFFSCREEN_FRAC)
    assert (_px(nx, 3440), _px(ny, 1440)) == (1720, 720)
    # With ry+rh = 1.25 the pen's box-bottom corner aims below the
    # desktop's bottom edge -> pinned to 65535 (the far edge), never wrapped
    # around to the top.
    stub_send_input.clear()
    nx, ny = _map(state, config, SPEC_791, 0, SPEC_791["max_y"], SCREEN,
                  stub_send_input, [-0.5, 0.75, 1.0, 0.5])
    assert (nx, ny) == (0, VIRT)


def test_rect_lane_px_per_mm_equal_both_axes(state, config, stub_send_input):
    nx0, ny0 = _map(state, config, SPEC_791, 0, 0, SCREEN, stub_send_input, RECT_FRAC)
    stub_send_input.clear()
    nx1, ny1 = _map(state, config, SPEC_791, SPEC_791["max_x"],
                    SPEC_791["max_y"], SCREEN, stub_send_input, RECT_FRAC)
    box_mm = mapping.rotated_pen_box_mm(SPEC_791, 0)
    scale_x = (_px(nx1, 3440) - _px(nx0, 3440)) / box_mm[0]
    scale_y = (_px(ny1, 1440) - _px(ny0, 1440)) / box_mm[1]
    assert scale_x == pytest.approx(scale_y, rel=2e-3)


def test_rect_lane_survives_90deg_rotation(state, config, stub_send_input):
    # Rotation permutes which DEVICE corner is the box corner (the rotation
    # transform runs upstream of update_pen_cursor), so pin the mapped
    # output as the SET of the four rect corners fed the four rotated
    # device corners through the same upstream transform.
    config.orientation_degrees = 90
    center = mapping.pen_rotation_center(SPEC_791)
    corners = {(0, 0), (SPEC_791["max_x"], 0), (0, SPEC_791["max_y"]),
               (SPEC_791["max_x"], SPEC_791["max_y"])}
    mapped = set()
    for (dx, dy) in sorted(corners):
        rx_, ry_ = mapping.apply_orientation(
            (dx, dy), config, center)
        nx, ny = _map(state, config, SPEC_791, rx_, ry_, SCREEN,
                      stub_send_input, RECT_FRAC)
        stub_send_input.clear()
        mapped.add((_px(nx, 3440), _px(ny, 1440)))
    assert mapped == {(RECT[0], RECT[1]), (RECT[0] + RECT[2], RECT[1]),
                      (RECT[0], RECT[1] + RECT[3]),
                      (RECT[0] + RECT[2], RECT[1] + RECT[3])}


def test_stretch_toggle_beats_the_rect_lane(state, config, stub_send_input):
    nx0, ny0 = _map(state, config, SPEC_791, 0, 0, SCREEN, stub_send_input,
                    RECT_FRAC, stretch=True)
    assert (nx0, ny0) == (0, 0)
    stub_send_input.clear()
    nx1, ny1 = _map(state, config, SPEC_791, SPEC_791["max_x"],
                    SPEC_791["max_y"], SCREEN, stub_send_input,
                    RECT_FRAC, stretch=True)
    assert (nx1, ny1) == (VIRT, VIRT)


def test_rect_lane_runs_on_a_metrics_less_host(state, config, stub_send_input):
    # U416 / #406 defect: the maintainer host answers junk/None for the
    # virtual-screen metrics. Fractional rects need NO metrics - the pen
    # box must still land inside the stored rect region (U414's old pin
    # asserted the degraded full-fill here; the fractional rect replaces
    # it deliberately - the fix).
    ny_near = _map(state, config, SPEC_791, 0, 0, None, stub_send_input,
                   RECT_FRAC)[1]
    stub_send_input.clear()
    ny_far = _map(state, config, SPEC_791, 0, SPEC_791["max_y"], None,
                  stub_send_input, RECT_FRAC)[1]
    assert (_px(0, 1440), _px(ny_near, 1440), _px(ny_far, 1440)) == \
        (0, RECT[1], RECT[1] + RECT[3])
    assert (ny_near, ny_far) != (0, VIRT)  # NOT full-fill


def test_metrics_present_and_metrics_absent_project_identically(state, config,
                                                                stub_send_input):
    # Fractional rect: the SAME normalized output with and without metrics
    # (metrics only decorate the diag line), i.e. the normal-host path is
    # behavior-unchanged and the junk-host path is identical.
    with_m = _map(state, config, SPEC_791, SPEC_791["max_x"],
                  SPEC_791["max_y"], SCREEN, stub_send_input, RECT_FRAC)
    stub_send_input.clear()
    without_m = _map(state, config, SPEC_791, SPEC_791["max_x"],
                     SPEC_791["max_y"], None, stub_send_input, RECT_FRAC)
    assert with_m == without_m


def test_no_rect_control_is_the_isotropic_lane(state, config, stub_send_input):
    # Control: with pen_area_rect=None the U376 lane must be byte-identical
    # to its pinned isotropic output (used_w centered contain on 3440x1440).
    box_mm = mapping.rotated_pen_box_mm(SPEC_791, 0)
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


def test_frac_projection_is_the_px_form_at_unit_screen_size():
    # U416: one math function; fractions are the px form with the union
    # bounds divided out (screen dims 1,1). Round-trip proof.
    off_x, scale_x, off_y, scale_y = pen_area_norm_projection(
        RECT_FRAC, 65024, 40640, 1.0, 1.0)
    px_form = pen_area_norm_projection(RECT, 65024, 40640, 3440, 1440)
    assert (off_x, scale_x, off_y, scale_y) == pytest.approx(px_form)


def test_diag_names_the_pen_area_mode_with_rect_px_mm(state, config,
                                                       stub_send_input,
                                                       capsys, monkeypatch):
    monkeypatch.setenv(mapping.PEN_MAP_DIAG_ENV, "1")
    monkeypatch.setattr(mapping, "monitor_count", lambda: 2)
    state.pen_product_id = 791
    capsys.readouterr()
    _map(state, config, SPEC_791, 0, 0, SCREEN, stub_send_input, RECT_FRAC)
    out = capsys.readouterr().out
    assert out.startswith("pen_abs_map mode=pen_area product=791")
    assert ("px_mm_x=%.4f px_mm_y=%.4f band_x=na band_y=na\n"
            % (RECT[2] / 325.12, RECT[3] / 203.2)) in out


def test_live_rect_selection_exact_match_and_none(cfg_state_config, monkeypatch):
    cfg, state = cfg_state_config
    key = display_key_for_records(RECORDS)
    cfg.set_pen_area_entry(IDENTITY, key, RECORDS, RECT_FRAC)
    state.pen_identity = IDENTITY
    monkeypatch.setattr(mapping, "monitor_records", lambda: list(RECORDS))
    assert select_live_pen_area_rect(cfg, state.pen_identity) == pytest.approx(RECT_FRAC)
    monkeypatch.setattr(mapping, "monitor_records",
                        lambda: ["\\\\.\\DISPLAY1|0|0|1920x1080"])
    mapping.reset_pen_area_key_cache()
    assert select_live_pen_area_rect(cfg, state.pen_identity) is None
    assert select_live_pen_area_rect(cfg, None) is None


@pytest.fixture
def cfg_state_config(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    return DriverConfig(), TabletState()


def test_frac_rect_datum_is_virtual_desktop_origin_relative(state, config,
                                                             stub_send_input):
    # U414 STEP-2 P1 contract, U416 fractional form: the fraction is of
    # the union bounds and the normalized space starts AT the virtual
    # origin (metrics left/top never enter the math). On the maintainer's
    # pivoted layout the origin is (-1440, 0); the output must be the
    # SAME normalized corners as the (0,0)-origin run - an absolute-
    # datum leak would shift them.
    origin_screen = (-1440, 0, 4880, 2560)
    nx0, ny0 = _map(state, config, SPEC_791, 0, 0, origin_screen,
                    stub_send_input, RECT_FRAC)
    stub_send_input.clear()
    nx1, ny1 = _map(state, config, SPEC_791, SPEC_791["max_x"],
                    SPEC_791["max_y"], origin_screen, stub_send_input, RECT_FRAC)
    ref0 = _map(state, config, SPEC_791, 0, 0, SCREEN, stub_send_input, RECT_FRAC)
    stub_send_input.clear()
    ref1 = _map(state, config, SPEC_791, SPEC_791["max_x"],
                SPEC_791["max_y"], SCREEN, stub_send_input, RECT_FRAC)
    assert (nx0, ny0) == ref0 and (nx1, ny1) == ref1


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
                          1500 * step, screen_metrics=SCREEN, pen_area_rect=RECT_FRAC)
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

    monkeypatch.setattr(mapping, "monitor_records", fake_records)
    mapping.reset_pen_area_key_cache()
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

    monkeypatch.setattr(mapping, "monitor_records", fake_records)
    mapping.reset_pen_area_key_cache()
    key = display_key_for_records(RECORDS)
    assert cached_display_key(now=100.0) == key
    assert cached_display_key(now=101.0) == key
    assert len(calls) == 1
    assert cached_display_key(now=103.0) == key
    assert len(calls) == 2
