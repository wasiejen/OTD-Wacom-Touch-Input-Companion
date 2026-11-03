"""Pen support (U70): parsing, cursor projection, press/release semantics.

Synthetic 10-byte 0x02 reports per the documented Intuos-V1 layout
(agent/research/2026-10-31_u68-otd-pen-layout.md). No device, no GUI.
"""

import threading
import time

import pytest

from conftest import FakeDevice

import touch_controller
from touch_controller import (
    ActionDispatcher,
    DriverConfig,
    TabletState,
    decode_pen_position,
    decode_pen_pressure,
    parse_pen_packet,
    pen_rotation_center,
    pen_specs_for_product,
    process_pen_report,
    rotated_pen_extents,
    update_pen_cursor,
)

SPECS = {"max_x": 44704, "max_y": 27940, "width_mm": 223.52, "height_mm": 139.7}
HOVER_ROW = [0x02, 0x20, 0x00, 0x00, 0x32, 0xB6, 0x00, 0x00, 0x00, 0xFD]
ABS_FLAGS = 0x0001 | 0x8000 | 0x4000


def build_pen_report(status, x=0, y=0, pressure=0, hover=0):
    """Encode fields back with the documented Intuos-V1 expressions."""
    return [0x02, status | (pressure & 1),
            (x >> 9) & 0xFF, (x >> 1) & 0xFF,
            (y >> 9) & 0xFF, (y >> 1) & 0xFF,
            (pressure >> 3) & 0xFF, ((pressure >> 1) & 0x03) << 6,
            0x00, (hover & 0xFC) | ((x & 1) << 1) | (y & 1)]


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


def process(report, state, dispatcher, config):
    process_pen_report(report, state, dispatcher, config, SPECS)


def mouse_flags(events):
    return [e[1] for e in events if e[0] == "mouse"]


def test_probe_hover_row_decodes_documented_position():
    assert decode_pen_position(HOVER_ROW) == (0, 25965)
    assert decode_pen_pressure(HOVER_ROW) == 0


def test_build_round_trips_full_range():
    report = build_pen_report(0x20, x=44704, y=27940, pressure=2047)
    assert decode_pen_position(report) == (44704, 27940)
    assert decode_pen_pressure(report) == 2047


def test_tablet_report_updates_state(state, dispatcher, config, stub_send_input):
    process(build_pen_report(0x20, x=1000, y=2000), state, dispatcher, config)
    assert state.pen_position == (1000, 2000)
    assert state.pen_pressure == 0
    assert state.pen_buttons == (False, False)


def test_rotation_report_carries_previous_pressure(state, dispatcher, config, stub_send_input):
    state.pen_pressure = 1200
    state.pen_buttons = (True, False)
    dispatcher.pen_button_held[1] = "right_click"
    stub_send_input.clear()
    process(build_pen_report(0x2A, x=500, y=400, pressure=300), state, dispatcher, config)
    assert state.pen_pressure == 1200
    assert state.pen_position == (500, 400)
    # Carried pressure drives the left-button press; the button stays held.
    assert mouse_flags(stub_send_input) == [ABS_FLAGS, 0x0002]


def test_absolute_mode_maps_full_range(state, dispatcher, config, stub_send_input):
    process(build_pen_report(0x20, x=44704, y=27940), state, dispatcher, config)
    assert stub_send_input[-1] == ("mouse", ABS_FLAGS, 0, 65535, 65535)


def test_absolute_mode_clamps_above_max(state, dispatcher, config, stub_send_input):
    process(build_pen_report(0x20, x=50000, y=30000), state, dispatcher, config)
    assert stub_send_input[-1] == ("mouse", ABS_FLAGS, 0, 65535, 65535)


def test_relative_mode_first_report_sets_origin_only(state, dispatcher, config, stub_send_input):
    config.feature_toggles["pen_absolute_mode"] = False
    process(build_pen_report(0x20, x=600), state, dispatcher, config)
    assert state.pen_position == (600, 0)
    assert stub_send_input == []


def test_relative_mode_scales_deltas_by_speed(state, dispatcher, config, stub_send_input):
    config.feature_toggles["pen_absolute_mode"] = False
    config.pen_speed = 8.0
    process(build_pen_report(0x20, x=600), state, dispatcher, config)
    process(build_pen_report(0x20, x=1000), state, dispatcher, config)
    # 400 device units = 2 mm at 200 units/mm; 8 px/mm -> 16 px.
    assert stub_send_input == [("mouse", 0x0001, 0, 16, 0)]


def test_relative_mode_accumulates_fractional_deltas(state, dispatcher, config, stub_send_input):
    config.feature_toggles["pen_absolute_mode"] = False
    config.pen_speed = 0.5
    process(build_pen_report(0x20, x=0), state, dispatcher, config)
    for x in (100, 200, 300):
        stub_send_input.clear()
        process(build_pen_report(0x20, x=x), state, dispatcher, config)
        assert stub_send_input == []
    process(build_pen_report(0x20, x=400), state, dispatcher, config)
    assert stub_send_input == [("mouse", 0x0001, 0, 1, 0)]


def test_pressure_press_and_release(state, dispatcher, config, stub_send_input):
    process(build_pen_report(0x20, pressure=100), state, dispatcher, config)
    process(build_pen_report(0x20, pressure=90), state, dispatcher, config)
    assert [e[1] for e in stub_send_input if e[0] == "mouse" and e[1] in (2, 4)] == [2]
    process(build_pen_report(0x20, pressure=10), state, dispatcher, config)
    assert stub_send_input[-1][1] == 4


def test_light_pressure_below_threshold_stays_released(state, dispatcher, config, stub_send_input):
    process(build_pen_report(0x20, pressure=10), state, dispatcher, config)
    assert [e for e in stub_send_input if e[0] == "mouse" and e[1] in (2, 4)] == []


def test_pen_button_defaults_right_and_middle(state, dispatcher, config, stub_send_input):
    process(build_pen_report(0x22), state, dispatcher, config)
    assert mouse_flags(stub_send_input).count(0x0008) == 1
    process(build_pen_report(0x24), state, dispatcher, config)
    assert mouse_flags(stub_send_input).count(0x0020) == 1
    assert mouse_flags(stub_send_input).count(0x0008) == 1  # button 1 stays held
    process(build_pen_report(0x20), state, dispatcher, config)
    assert mouse_flags(stub_send_input).count(0x0010) == 1
    assert mouse_flags(stub_send_input).count(0x0040) == 1


def test_pen_button_mapping_customizable(state, dispatcher, config, stub_send_input):
    config.action_mapping["pen_button_1"] = "middle_click"
    process(build_pen_report(0x22), state, dispatcher, config)
    assert 0x0020 in mouse_flags(stub_send_input)


def test_non_button_pen_action_taps_once(state, dispatcher, config, stub_send_input):
    config.action_mapping["pen_button_1"] = "undo"
    process(build_pen_report(0x22), state, dispatcher, config)
    keys = [e for e in stub_send_input if e[0] == "key"]
    assert len(keys) == 4
    stub_send_input.clear()
    process(build_pen_report(0x20), state, dispatcher, config)
    assert [e for e in stub_send_input if e[0] == "key"] == []
    assert dispatcher.pen_button_held == {}


def test_out_of_range_releases_held_inputs_and_resets_origin(state, dispatcher, config, stub_send_input):
    process(build_pen_report(0xE2, pressure=100), state, dispatcher, config)
    stub_send_input.clear()
    process(build_pen_report(0x80), state, dispatcher, config)
    released = [e[1] for e in stub_send_input if e[0] == "mouse"]
    assert sorted(released) == [0x0004, 0x0010]
    assert state.pen_position is None
    assert state.pen_pressure == 0
    assert state.pen_buttons == (False, False)


def test_short_packet_and_tool_report_are_tolerated(state, dispatcher, config, stub_send_input):
    parse_pen_packet([0x02, 0xE0], state)
    assert state.pen_in_proximity is True
    process_pen_report([0x02, 0xE0], state, dispatcher, config, SPECS)
    process_pen_report([0x02, 0xC2, 0x80, 0x25, 0x18, 0x05, 0x12, 0xC1, 0x00, 0x00],
                       state, dispatcher, config, SPECS)
    assert stub_send_input == []
    assert state.pen_position is None


def test_known_models_route_to_their_own_tables_quietly(capsys):
    # All three wired model ids resolve to their OWN table, no print.
    for product_id in (789, 788, 791):
        specs = pen_specs_for_product(product_id)
        assert specs is touch_controller.PEN_SPECS_BY_PRODUCT[product_id]
    assert capsys.readouterr().out == ""


def test_models_carry_valued_distinct_tables():
    # Maintainer device data (jsonc commit d1d70e12, supplied via task spec;
    # the commit is not resolvable in this repo): the three models carry
    # byte-exact, per-model distinct dicts.
    assert touch_controller.PEN_SPECS_BY_PRODUCT[788] == {
        "max_x": 4096, "max_y": 4096, "width_mm": 215.9, "height_mm": 134.6}
    assert touch_controller.PEN_SPECS_BY_PRODUCT[791] == {
        "max_x": 4096, "max_y": 4096, "width_mm": 215.9, "height_mm": 118.6}
    assert touch_controller.PEN_SPECS_BY_PRODUCT[789] == {
        "max_x": 44704, "max_y": 27940, "width_mm": 223.52, "height_mm": 139.7}


def test_filled_models_drive_the_pen_consumers():
    # The resolved 788/791 dicts drive the pen math byte-exactly: the square
    # 4096/4096 box centers at (2048.0, 2048.0) (not the 789 Medium center),
    # and the unrotated/rotated extents land on that square. Data-validity
    # flags (square extents, center, clamp) are recorded in the table comment
    # for maintainer hardware acceptance (TODO #311).
    specs_788 = pen_specs_for_product(788)
    specs_791 = pen_specs_for_product(791)
    assert pen_rotation_center(specs_788) == (2048.0, 2048.0)
    assert pen_rotation_center(specs_791) == (2048.0, 2048.0)
    assert rotated_pen_extents(specs_788, 0) == (0, 0, 4096, 4096)
    assert rotated_pen_extents(specs_791, 90) == (0, 0, 4096, 4096)


def test_filled_models_clamp_absolute_and_scale_relative(state, dispatcher, config,
                                                          stub_send_input):
    # The two P1 lanes the filled box feeds (reviewer fire-2):
    # absolute mode clamps coordinates above the 4096 square to the desktop
    # edge (data-validity flag); relative mode scales per-model mm/count
    # asymmetrically for 788: x 215.9/4096, y 134.6/4096 (at speed 8:
    # 400 counts -> 168 px x and 105 px y, not the 200 counts/mm Medium math).
    specs = pen_specs_for_product(788)
    update_pen_cursor(state, dispatcher, config, specs, 5000, 5000)
    assert stub_send_input[-1] == ("mouse", ABS_FLAGS, 0, 65535, 65535)
    config.feature_toggles["pen_absolute_mode"] = False
    config.pen_speed = 8.0
    state.pen_position = None  # origin reset: the next report starts the delta lane
    update_pen_cursor(state, dispatcher, config, specs, 0, 0)
    stub_send_input.clear()
    update_pen_cursor(state, dispatcher, config, specs, 400, 400)
    assert stub_send_input == [("mouse", 0x0001, 0, 168, 105)]


def test_wireless_receiver_id_prints_the_named_fallback_line(capsys):
    # 132 is the wireless receiver id: no table entry, so it falls back to
    # Medium with ONE byte-stable named line per call.
    assert pen_specs_for_product(132) is touch_controller.PEN_SPECS_DEFAULT
    assert capsys.readouterr().out == "pen_specs= no-table product=132 using=medium\n"


def test_unknown_product_prints_the_named_fallback_line(capsys):
    assert pen_specs_for_product(999) is touch_controller.PEN_SPECS_DEFAULT
    assert capsys.readouterr().out == "pen_specs= no-table product=999 using=medium\n"


def test_pen_config_round_trip(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    cfg = DriverConfig()
    cfg.pen_speed = 12.0
    cfg.feature_toggles["pen_absolute_mode"] = False
    cfg.action_mapping["pen_button_1"] = "left_click"
    path = tmp_path / "user.cfg"
    cfg.save_to_file(str(path))

    fresh = DriverConfig()
    assert fresh.load_from_file(str(path)) is True
    assert fresh.pen_speed == 12.0
    assert fresh.feature_toggles["pen_absolute_mode"] is False
    assert fresh.action_mapping["pen_button_1"] == "left_click"


def test_pen_thread_processes_full_report_and_logs_init_note(fake_hid, stub_send_input, capsys):
    dev = FakeDevice(reports=[build_pen_report(0xE0, x=1000, y=1000, pressure=100)])
    fake_hid._queue.append(dev)
    state = TabletState()
    dispatcher = ActionDispatcher(state)
    enabled = threading.Event()
    enabled.set()
    stop = threading.Event()

    thread = threading.Thread(
        target=touch_controller.run_interface,
        args=("Pen", {"interface_number": 1, "path": b"fake-path", "product_id": 789},
              state, dispatcher, enabled, stop),
        kwargs={"idle_poll": 0.02},
        daemon=True,
    )
    thread.start()

    deadline = time.monotonic() + 1.0
    while not state.pen_in_proximity and time.monotonic() < deadline:
        time.sleep(0.002)
    enabled.clear()
    thread.join(timeout=0.5)

    assert not thread.is_alive()
    assert state.pen_position == (1000, 1000)
    mouse_flags = [e[1] for e in stub_send_input if e[0] == "mouse"]
    assert ABS_FLAGS in mouse_flags
    assert 0x0002 in mouse_flags
    assert 0x0004 in mouse_flags, "thread exit must release the held left button"
    assert "Init report not sent" in capsys.readouterr().out
