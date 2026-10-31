"""Device orientation rotation (U74): shared transform, config round-trip.

Synthetic reports drive touch and pen directly; no device, no GUI window.
Rotation convention: positive degrees turn the input clockwise on screen
(y points down), 1° steps, no quadrant quantization.
"""

import json

import pytest

from touch_controller import (
    DriverConfig,
    ActionDispatcher,
    TabletState,
    TOUCH_ROTATION_CENTER,
    ORIENTATION_MAX_DEGREES,
    apply_orientation,
    pen_rotation_center,
    process_pen_report,
    process_touch_report,
    rotate_point,
)


SPECS = {"max_x": 44704, "max_y": 27940, "width_mm": 223.52, "height_mm": 139.7}
ABS_FLAGS = 0x0001 | 0x8000 | 0x4000
MOVE_FLAG = 0x0001


def build_pen_report(status, x=0, y=0, pressure=0, hover=0):
    """Encode fields back with the documented Intuos-V1 expressions."""
    return [0x02, status | (pressure & 1),
            (x >> 9) & 0xFF, (x >> 1) & 0xFF,
            (y >> 9) & 0xFF, (y >> 1) & 0xFF,
            (pressure >> 3) & 0xFF, ((pressure >> 1) & 0x03) << 6,
            0x00, (hover & 0xFC) | ((x & 1) << 1) | (y & 1)]


def touch_block(slot, status, x, y):
    return [slot, status, (x >> 4) & 0xFF, (y >> 4) & 0xFF,
            ((x & 0xF) << 4) | (y & 0xF), 0, 0, 0]


def touch_report(blocks):
    return [0x02, len(blocks)] + [b for block in blocks for b in block]


@pytest.fixture
def config(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    return DriverConfig()


@pytest.fixture
def state():
    return TabletState()


@pytest.fixture
def dispatcher(state):
    return ActionDispatcher(state)


# --- rotation core -----------------------------------------------------------

def test_zero_degrees_passthrough_keeps_ints():
    point = (100, 200)
    rotated = rotate_point(*point, 0, 2047.5, 2047.5)
    assert rotated == point
    assert all(isinstance(v, int) for v in rotated)


def test_preset_quadrants_table():
    center = (10, 10)
    assert rotate_point(20, 10, 90, *center) == pytest.approx((10, 20))
    assert rotate_point(20, 10, 180, *center) == pytest.approx((0, 10))
    assert rotate_point(20, 10, 270, *center) == pytest.approx((10, 0))
    assert rotate_point(10, 0, 90, *center) == pytest.approx((20, 10))


def test_one_degree_step_is_not_quantized():
    center = (10, 10)
    x, y = rotate_point(20, 10, 1, *center)
    # A 1° rotation keeps the magnitude but lands on no exact quadrant value.
    assert x != 20 and y != 10
    assert abs((x - center[0]) ** 2 + (y - center[1]) ** 2 - 100) < 1e-9


def test_pen_rotation_center_from_model_ranges():
    assert pen_rotation_center(SPECS) == (22352, 13970)


def test_apply_orientation_uses_config_angle(config):
    config.orientation_degrees = 90
    rotated = apply_orientation((20, 10), config, (10, 10))
    assert rotated == pytest.approx((10, 20))


# --- config round-trip -------------------------------------------------------

def test_orientation_round_trips_through_save_dict(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    cfg = DriverConfig()
    assert cfg.orientation_degrees == 0
    cfg.orientation_degrees = 137
    path = tmp_path / "user.cfg"
    cfg.save_to_file(str(path))

    with open(path) as f:
        saved = json.load(f)
    assert saved["motion_and_thresholds"]["orientation_degrees"] == 137

    fresh = DriverConfig()
    assert fresh.load_from_file(str(path)) is True
    assert fresh.orientation_degrees == 137


def test_validate_accepts_whole_degrees_and_numeric_strings(config):
    validated, warnings, errors = config.validate_config_data(
        {"motion_and_thresholds": {"orientation_degrees": "90"}})
    assert errors == []
    assert validated["motion"]["orientation_degrees"] == 90
    assert isinstance(validated["motion"]["orientation_degrees"], int)


@pytest.mark.parametrize("bad", [360, -1, 90.5, "abc", True])
def test_validate_rejects_out_of_range_or_fraction(config, bad):
    _, _, errors = config.validate_config_data(
        {"motion_and_thresholds": {"orientation_degrees": bad}})
    assert errors, f"{bad!r} must be rejected"
    assert "orientation_degrees" in errors[0]


# --- touch path --------------------------------------------------------------

def test_touch_contacts_rotate_through_shared_path(config, state, dispatcher):
    config.orientation_degrees = 90
    process_touch_report(
        touch_report([touch_block(1, 0xC0, 1000, 2000)]), state, dispatcher, config)
    assert state.active_contacts[1] == pytest.approx((2095.0, 1000.0))


def test_touch_zero_orientation_state_is_unchanged(config, state, dispatcher):
    process_touch_report(
        touch_report([touch_block(1, 0xC0, 1000, 2000)]), state, dispatcher, config)
    assert state.active_contacts[1] == (1000, 2000)


def test_up_swipe_maps_to_rotated_screen_direction(config, state, dispatcher,
                                                   stub_send_input):
    config.orientation_degrees = 90
    # Finger starts, then moves 100 units up (negative y) on the tablet.
    process_touch_report(
        touch_report([touch_block(1, 0xC0, 1500, 1700)]), state, dispatcher, config)
    stub_send_input.clear()
    process_touch_report(
        touch_report([touch_block(1, 0x80, 1500, 1600)]), state, dispatcher, config)

    moves = [e for e in stub_send_input if e[0] == "mouse" and e[1] == MOVE_FLAG]
    assert moves, "the drag must send a cursor move"
    dx, dy = moves[0][3], moves[0][4]
    # Rotated 90° clockwise: an up-swipe drives the cursor right, not down.
    assert dx > 0
    assert abs(dy) < 2
    assert abs(dx - 160) < 2


def test_zero_orientation_drag_direction_is_unchanged(config, state, dispatcher,
                                                      stub_send_input):
    process_touch_report(
        touch_report([touch_block(1, 0xC0, 1500, 1700)]), state, dispatcher, config)
    stub_send_input.clear()
    process_touch_report(
        touch_report([touch_block(1, 0x80, 1500, 1600)]), state, dispatcher, config)

    moves = [e for e in stub_send_input if e[0] == "mouse" and e[1] == MOVE_FLAG]
    assert moves[0][3] == 0
    assert moves[0][4] < 0


# --- pen path ----------------------------------------------------------------

def test_pen_absolute_point_rotates_around_digitizer_center(config, state,
                                                            dispatcher, stub_send_input):
    config.orientation_degrees = 90
    process_pen_report(build_pen_report(0x20, x=29322, y=13970),
                       state, dispatcher, config, SPECS)
    # An east point rotates clockwise onto the tablet's south half:
    # (22352, 20940) device units -> the aspect-aware virtual desktop mapping.
    # The rotated box (x 8382..36322, y -8382..36322) spans the full desktop.
    assert state.pen_position == pytest.approx((22352.0, 20940.0))
    assert stub_send_input[-1] == ("mouse", ABS_FLAGS, 0, 32767, 42985)


def test_pen_absolute_rotated_extents_reach_full_desktop(config, state,
                                                         dispatcher, stub_send_input):
    """Device-free projection pin (#274): the rotated digitizer box maps to
    the full desktop at 90/270, corners exact."""
    config.orientation_degrees = 90
    # NW tablet corner turns clockwise onto the desktop east edge.
    process_pen_report(build_pen_report(0x20, x=0, y=0),
                       state, dispatcher, config, SPECS)
    assert stub_send_input[-1] == ("mouse", ABS_FLAGS, 0, 65535, 0)
    # SE corner lands on the desktop south-west corner.
    process_pen_report(build_pen_report(0x20, x=44704, y=27940),
                       state, dispatcher, config, SPECS)
    assert stub_send_input[-1] == ("mouse", ABS_FLAGS, 0, 0, 65535)

    config.orientation_degrees = 270
    # NE corner turns counter-clockwise onto the desktop north-west corner.
    process_pen_report(build_pen_report(0x20, x=44704, y=0),
                       state, dispatcher, config, SPECS)
    assert stub_send_input[-1] == ("mouse", ABS_FLAGS, 0, 0, 0)


def test_pen_absolute_zero_orientation_mapping_is_unchanged(config, state,
                                                            dispatcher, stub_send_input):
    """0° absolute mapping stays byte-identical to the fixed max_x/max_y scale."""
    process_pen_report(build_pen_report(0x20, x=22352, y=13970),
                       state, dispatcher, config, SPECS)
    assert stub_send_input[-1] == ("mouse", ABS_FLAGS, 0, 32767, 32767)


def test_pen_relative_delta_rotates_direction(config, state, dispatcher,
                                              stub_send_input):
    config.orientation_degrees = 90
    config.feature_toggles["pen_absolute_mode"] = False
    config.pen_speed = 8.0
    process_pen_report(build_pen_report(0x20, x=600), state, dispatcher, config, SPECS)
    stub_send_input.clear()
    process_pen_report(build_pen_report(0x20, x=1000), state, dispatcher, config, SPECS)
    # 400 device units right = 2 mm; rotated 90° it moves the cursor down.
    # int() floors the float product; the remainder stays in the accumulator.
    assert len(stub_send_input) == 1
    event = stub_send_input[0]
    assert event[0] == "mouse" and event[1] == MOVE_FLAG
    assert event[3] == 0 and 15 <= event[4] <= 16


def test_pen_zero_orientation_state_is_unchanged(config, state, dispatcher,
                                                 stub_send_input):
    process_pen_report(build_pen_report(0x20, x=1000, y=2000),
                       state, dispatcher, config, SPECS)
    assert state.pen_position == (1000, 2000)


# --- GUI entry + widget geometry (device-free, no Tk window) ------------------

import gui
from gui import ORIENTATION_PRESETS, tablet_pictogram_layout, parse_orientation_degrees


def test_orientation_presets_are_the_quadrants():
    assert ORIENTATION_PRESETS == (0, 90, 180, 270)


@pytest.mark.parametrize("raw,expected", [
    ("0", 0), ("90", 90), ("359", 359), ("137", 137),
    ("90.5", None), ("360", None), ("-1", None), ("", None), ("abc", None),
])
def test_parse_orientation_degrees(raw, expected):
    assert parse_orientation_degrees(raw) == expected


def test_pictogram_numbering_top_to_bottom_at_zero():
    layout = tablet_pictogram_layout(0)
    assert [n for n, _, _ in layout["buttons"]] == list(range(1, 9))
    by_number = {n: (x, y) for n, x, y in layout["buttons"]}
    # 1-4 above the central round, 5-8 mirrored below; 4 and 5 adjacent to it.
    assert by_number[1][1] < by_number[4][1] < 0 < by_number[5][1] < by_number[8][1]
    assert by_number[5][1] == pytest.approx(-by_number[4][1])
    assert by_number[8][1] == pytest.approx(-by_number[1][1])
    assert layout["center"] == pytest.approx((0, 0))
    # All controls stay inside the tablet rectangle.
    half_h = layout["rect"][2][1]
    assert all(abs(y) <= half_h for _, _, y in layout["buttons"])


def test_pictogram_buttons_rotate_with_the_angle():
    zero = {n: (x, y) for n, x, y in tablet_pictogram_layout(0)["buttons"]}
    quarter = {n: (x, y) for n, x, y in tablet_pictogram_layout(90)["buttons"]}
    assert len(quarter) == 8
    for number in range(1, 9):
        x, y = zero[number]
        assert quarter[number] == pytest.approx(rotate_point(x, y, 90, 0, 0))


def test_pictogram_rect_and_dial_rotate():
    zero = tablet_pictogram_layout(0)
    rotated = tablet_pictogram_layout(90)
    for corner, rotated_corner in zip(zero["rect"], rotated["rect"]):
        assert rotated_corner == pytest.approx(rotate_point(*corner, 90, 0, 0))
    assert rotated["dial_radius"] == zero["dial_radius"]


def test_apply_settings_stores_whole_degrees(config):
    gui.apply_settings_to_config(
        config, {"orientation_degrees": "45"},
        {"cursor_acceleration": False, "scroll_acceleration": False}, {}, {})
    assert config.orientation_degrees == 45
    assert isinstance(config.orientation_degrees, int)


def test_apply_settings_rejects_bad_orientation(config):
    accel = {"cursor_acceleration": False, "scroll_acceleration": False}
    with pytest.raises(ValueError):
        gui.apply_settings_to_config(
            config, {"orientation_degrees": "360"}, accel, {}, {})
    with pytest.raises(ValueError):
        gui.apply_settings_to_config(
            config, {"orientation_degrees": "90.5"}, accel, {}, {})
    assert config.orientation_degrees == 0
