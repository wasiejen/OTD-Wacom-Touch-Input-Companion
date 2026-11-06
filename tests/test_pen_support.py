"""Pen support (U70): parsing, cursor projection, press/release semantics.

Synthetic 10-byte 0x02 reports per the documented Intuos-V1 layout
(agent/research/2026-10-31_u68-otd-pen-layout.md). No device, no GUI.
"""

import json
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


def process_791(report, state, dispatcher, config):
    """The authoritative 791 OTD table: the maintainer's Large tablet lane."""
    process_pen_report(report, state, dispatcher, config,
                       touch_controller.PEN_SPECS_BY_PRODUCT[791])


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


def test_absolute_mode_default_maps_full_range_1to1(state, dispatcher, config, stub_send_input):
    # U344 goal 3: the DEFAULT mapping keeps equal px-per-mm on both axes
    # (pen writing is the primary use). The 789 box (223.52 x 139.7 mm)
    # scales at the longer axis: x spans the full desktop, y the CENTERED
    # band 12287..53247 in virtual space. On a 25:9 ultrawide the pen area
    # then covers a center slice, not the stretched full screen.
    process(build_pen_report(0x20, x=0, y=0), state, dispatcher, config)
    assert stub_send_input[-1] == ("mouse", ABS_FLAGS, 0, 0, 12287)
    process(build_pen_report(0x20, x=44704, y=27940), state, dispatcher, config)
    assert stub_send_input[-1] == ("mouse", ABS_FLAGS, 0, 65535, 53247)
    y_range = 53247 - 12287
    assert abs(y_range / 139.7 - 65535 / 223.52) < 0.05


def test_absolute_mode_stretch_opt_in_stretches_full_area(state, dispatcher,
                                                          config, stub_send_input):
    # Opt-in flag (U344 goal 3): the maintainer's ultrawide observation —
    # the whole 25:9 screen stretched across the full pen area, per-axis
    # scales, the pre-U344 default behavior.
    config.feature_toggles["pen_stretch_full_desktop"] = True
    process(build_pen_report(0x20, x=44704, y=27940), state, dispatcher, config)
    assert stub_send_input[-1] == ("mouse", ABS_FLAGS, 0, 65535, 65535)


def test_absolute_mode_clamps_above_max(state, dispatcher, config, stub_send_input):
    # Direct consumer call (no autoscale observation): coordinates above
    # the declared box clamp on the long axis; the 1:1 default scales y
    # into the centered band, the stretch opt-in clamps both axes.
    update_pen_cursor(state, dispatcher, config, SPECS, 50000, 30000)
    assert stub_send_input[-1] == ("mouse", ABS_FLAGS, 0, 65535, 56267)
    config.feature_toggles["pen_stretch_full_desktop"] = True
    stub_send_input.clear()
    update_pen_cursor(state, dispatcher, config, SPECS, 50000, 30000)
    assert stub_send_input[-1] == ("mouse", ABS_FLAGS, 0, 65535, 65535)


def test_stretch_toggle_defaults_and_round_trips(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    cfg = DriverConfig()
    assert cfg.feature_toggles["pen_stretch_full_desktop"] is False
    cfg.feature_toggles["pen_stretch_full_desktop"] = True
    path = tmp_path / "user.cfg"
    cfg.save_to_file(str(path))
    fresh = DriverConfig()
    assert fresh.load_from_file(str(path)) is True
    assert fresh.feature_toggles["pen_stretch_full_desktop"] is True


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
    # U370 (HW-ACCEPT-2026-11-04): all three tables are byte-exact per-model
    # dicts from OpenTabletDriver at pinned commit
    # 2c4b9a5649eb34cd74bc8b1b1041dbb0356ae762, Digitizer block of
    # Wacom/PTH-451.json (788), Wacom/PTH-651.json (789) and
    # Wacom/PTH-851.json (791).
    assert touch_controller.PEN_SPECS_BY_PRODUCT[788] == {
        "max_x": 31496, "max_y": 19685, "width_mm": 157.48, "height_mm": 98.425}
    assert touch_controller.PEN_SPECS_BY_PRODUCT[791] == {
        "max_x": 65024, "max_y": 40640, "width_mm": 325.12, "height_mm": 203.2}
    assert touch_controller.PEN_SPECS_BY_PRODUCT[789] == {
        "max_x": 44704, "max_y": 27940, "width_mm": 223.52, "height_mm": 139.7}


def test_pen_tables_are_200_counts_per_mm_on_every_axis():
    # U370 aspect audit: every OTD table row is exactly 200.0 counts/mm on
    # both axes (the old filled 4096 square ran 18.97 x 30.43 / 34.54), and
    # every row spans 16:10 in counts -- so the default 1:1 lane maps a
    # centered band, not an aspect-skewed fill.
    for specs in touch_controller.PEN_SPECS_BY_PRODUCT.values():
        assert abs(specs["max_x"] / specs["width_mm"] - 200.0) < 1e-6
        assert abs(specs["max_y"] / specs["height_mm"] - 200.0) < 1e-6
        assert abs(specs["max_x"] / specs["max_y"] - 1.6) < 1e-6


def test_authoritative_models_drive_the_pen_consumers():
    # U370: the resolved 788/791 dicts drive the pen math byte-exactly. The
    # non-square OTD boxes center per axis (788 at (15748.0, 9842.5), 791 at
    # (32512.0, 20320.0)) and the unrotated extents carry the true 16:10
    # counts, not the old filled 4096 square.
    specs_788 = pen_specs_for_product(788)
    specs_791 = pen_specs_for_product(791)
    assert pen_rotation_center(specs_788) == (15748.0, 9842.5)
    assert pen_rotation_center(specs_791) == (32512.0, 20320.0)
    assert rotated_pen_extents(specs_788, 0) == (0, 0, 31496, 19685)
    assert rotated_pen_extents(specs_791, 0) == (0, 0, 65024, 40640)


def test_authoritative_models_clamp_absolute_and_scale_relative(state, dispatcher, config,
                                                                 stub_send_input):
    # U370 repin of the two P1 lanes on the authoritative 788 OTD box:
    # absolute mode clamps coordinates past the 31496 x 19685 box to the
    # desktop edge; relative mode scales per-model mm/count -- 788 runs
    # exactly 200 counts/mm on both axes, so at speed 8: 400 counts ->
    # 2.0 mm x 8 -> x int(15.99999...) = 15, y int(16.0) = 16 (the x float
    # lands below 16 by float noise; the fraction stays accumulated).
    specs = pen_specs_for_product(788)
    update_pen_cursor(state, dispatcher, config, specs, 40000, 40000)
    assert stub_send_input[-1] == ("mouse", ABS_FLAGS, 0, 65535, 65535)
    config.feature_toggles["pen_absolute_mode"] = False
    config.pen_speed = 8.0
    state.pen_position = None  # origin reset: the next report starts the delta lane
    update_pen_cursor(state, dispatcher, config, specs, 0, 0)
    stub_send_input.clear()
    update_pen_cursor(state, dispatcher, config, specs, 400, 400)
    assert stub_send_input == [("mouse", 0x0001, 0, 15, 16)]


def test_autoscale_grows_the_box_and_prints_one_named_line(state, dispatcher, config,
                                                            stub_send_input, capsys):
    # U344 defensive lane kept as the safety net under the U370 corrected
    # 791 extents: coordinates past the OTD 65024 x 40640 box grow the
    # mapping box per axis and ONE byte-stable named line fires; later
    # reports stay quiet. The grown 16:10 box maps per the 1:1 default lane.
    process_791(build_pen_report(0x20, x=70000, y=50000), state, dispatcher, config)
    assert capsys.readouterr().out == (
        "pen_extents_autoscale declared_x=65024 declared_y=40640 "
        "observed_x=70000 observed_y=50000 using=observed\n")
    # Grown box corner (70000,50000): x spans the full desktop, y lands in
    # the centered band at int(9362.142857 + 46810.714286 + 1e-9) = 56172.
    assert stub_send_input[-1] == ("mouse", ABS_FLAGS, 0, 65535, 56172)
    assert state.pen_obs_max_x == 70000
    assert state.pen_obs_max_y == 50000
    stub_send_input.clear()
    process_791(build_pen_report(0x20, x=2500, y=2500), state, dispatcher, config)
    assert capsys.readouterr().out == ""
    assert stub_send_input[-1] == ("mouse", ABS_FLAGS, 0, 2340, 11702)
    assert state.pen_obs_max_x == 70000
    assert state.pen_obs_max_y == 50000


def test_effective_specs_drive_rotation_center_and_extents(state):
    # The grown box feeds BOTH consumers: rotation center and extents take
    # the grown max, not the declared 65024x40640.
    specs = touch_controller.PEN_SPECS_BY_PRODUCT[791]
    state.pen_obs_max_x = 65535
    state.pen_obs_max_y = 65535
    grown = touch_controller.pen_effective_specs(specs, state)
    assert grown is not specs
    assert grown["max_x"] == 65535
    assert grown["max_y"] == 65535
    assert grown["width_mm"] == 325.12
    assert touch_controller.pen_rotation_center(grown) == (32767.5, 32767.5)
    assert touch_controller.rotated_pen_extents(grown, 0) == (0, 0, 65535, 65535)


def test_effective_specs_passthrough_within_declared_box(state):
    # A model reporting inside its declared box (789 lane) returns the
    # declared dict unchanged: no copy, no grow.
    specs = touch_controller.PEN_SPECS_BY_PRODUCT[789]
    state.pen_obs_max_x = 1000
    state.pen_obs_max_y = 1000
    assert touch_controller.pen_effective_specs(specs, state) is specs


def test_in_range_reports_keep_the_box_and_stay_quiet(state, dispatcher, config,
                                                       stub_send_input, capsys):
    # 789 full-box coordinate: observed max lands ON the declared edge, the
    # box does not grow and no named line prints.
    process(build_pen_report(0x20, x=44704, y=27940), state, dispatcher, config)
    assert capsys.readouterr().out == ""
    assert state.pen_obs_max_x == 44704
    assert state.pen_obs_max_y == 27940
    assert touch_controller.pen_effective_specs(SPECS, state) is SPECS


def test_relative_mode_uses_the_grown_box(state, dispatcher, config, stub_send_input):
    # Relative mode scales by the GROWN box too: the 791 OTD box (200
    # counts/mm) grows to the observed 65935 range, so counts scale below
    # the declared physical density.
    config.feature_toggles["pen_absolute_mode"] = False
    config.pen_speed = 8.0
    process_791(build_pen_report(0x20, x=65535, y=65535), state, dispatcher, config)
    stub_send_input.clear()
    process_791(build_pen_report(0x20, x=65935, y=65935), state, dispatcher, config)
    # 400 counts at 325.12/65935 counts/mm (x) and 203.2/65935 (y), speed 8:
    # x 15.779 px -> 15, y 9.862 px -> 9 (fractional parts stay accumulated).
    assert stub_send_input == [("mouse", 0x0001, 0, 15, 9)]
    assert state.pen_obs_max_x == 65935
    assert state.pen_obs_max_y == 65935


def test_791_default_lane_maps_aspect_correct_1to1(state, dispatcher, config, stub_send_input):
    # U370 goal (maintainer finding: diagonal shallower than 1:1 on the
    # filled 4096 square): with the authoritative 791 box (65024 x 40640,
    # 16:10) the DEFAULT absolute lane keeps one scale on both axes. The
    # x axis spans the full desktop (longer axis), the y box lands in the
    # centered band 12287..53247 -- the same 16:10 band as the 789 pin --
    # so px-per-mm is equal on x and y and every corner of the pen area
    # is reachable. Pre-change (square 4096 box) both axes ran to 65535.
    specs = pen_specs_for_product(791)
    update_pen_cursor(state, dispatcher, config, specs, 0, 0)
    assert stub_send_input[-1] == ("mouse", ABS_FLAGS, 0, 0, 12287)
    stub_send_input.clear()
    update_pen_cursor(state, dispatcher, config, specs, 65024, 40640)
    assert stub_send_input[-1] == ("mouse", ABS_FLAGS, 0, 65535, 53247)
    stub_send_input.clear()
    update_pen_cursor(state, dispatcher, config, specs, 32512, 20320)
    assert stub_send_input[-1] == ("mouse", ABS_FLAGS, 0, 32767, 32767)


def test_791_stretch_optin_still_fills_the_desktop(state, dispatcher, config, stub_send_input):
    # U344 goal-3 opt-in kept by the U370 ruling: pen_stretch_full_desktop
    # = True keeps the old per-axis stretch -- on the new 16:10 box both
    # axes run to 65535, the aspect is NOT preserved (opt-in).
    config.feature_toggles["pen_stretch_full_desktop"] = True
    specs = pen_specs_for_product(791)
    update_pen_cursor(state, dispatcher, config, specs, 0, 0)
    assert stub_send_input[-1] == ("mouse", ABS_FLAGS, 0, 0, 0)
    stub_send_input.clear()
    update_pen_cursor(state, dispatcher, config, specs, 65024, 40640)
    assert stub_send_input[-1] == ("mouse", ABS_FLAGS, 0, 65535, 65535)


def test_autoscale_floor_cannot_shrink_below_declared_extents(state):
    # U370 leg 4: the autoscale safety net stays GROW-ONLY against the
    # corrected declared extents. Observed coordinates inside the OTD 791
    # box return the declared dict unchanged (identity); a mixed exceed
    # grows only the x axis and the y axis can never fall below the
    # declared 40640 floor.
    specs = touch_controller.PEN_SPECS_BY_PRODUCT[791]
    state.pen_obs_max_x = 40000
    state.pen_obs_max_y = 40000
    assert touch_controller.pen_effective_specs(specs, state) is specs
    state.pen_obs_max_x = 70000
    grown = touch_controller.pen_effective_specs(specs, state)
    assert grown["max_x"] == 70000
    assert grown["max_y"] == 40640
    assert grown["width_mm"] == 325.12
    assert grown["height_mm"] == 203.2


def test_wireless_size_selects_only_the_model_extents_preset(config, state, dispatcher,
                                                             stub_send_input):
    # Maintainer ruling 2026-11-04 (U370 leg 5): a wireless package carries
    # the SAME coordinates as wired (only fewer bundled fields), so
    # wireless_pen_size selects the model extents preset and NOTHING else
    # -- no separate wireless coordinate scaling exists. The routed dict is
    # the model table object itself, and the wireless-routed mapping emits
    # byte-identical events to the wired 791 lane for the same coordinate.
    config.wireless_pen_size = "large"
    routed = pen_specs_for_product(132, config)
    assert routed is touch_controller.PEN_SPECS_BY_PRODUCT[791]
    update_pen_cursor(state, dispatcher, config, routed, 65024, 40640)
    wireless_event = stub_send_input[-1]
    stub_send_input.clear()
    update_pen_cursor(state, dispatcher, config,
                      touch_controller.PEN_SPECS_BY_PRODUCT[791], 65024, 40640)
    assert wireless_event == stub_send_input[-1]


def test_wireless_receiver_id_prints_the_named_fallback_line(capsys):
    # 132 is the wireless receiver id: no table entry, so it falls back to
    # Medium with ONE byte-stable named line per call.
    assert pen_specs_for_product(132) is touch_controller.PEN_SPECS_DEFAULT
    assert capsys.readouterr().out == "pen_specs= no-table product=132 using=medium\n"


def test_wireless_size_from_config_routes_the_receiver(capsys, config):
    # U344 goal 2: the wireless addon carries receiver id 132 on ALL tablet
    # sizes, so the size is not detectable. The user config names it; the
    # default is medium (his observed wireless behavior: behaves like a
    # Medium pad) and the named line reports the routing byte-exactly.
    assert config.wireless_pen_size == "medium"
    capsys.readouterr()  # drain the fixture's config-load banner
    assert pen_specs_for_product(132, config) is touch_controller.PEN_SPECS_BY_PRODUCT[789]
    assert capsys.readouterr().out == "pen_specs= wireless_size=medium product=132 using=789\n"


def test_wireless_size_large_and_small_select_their_tables(capsys, config):
    capsys.readouterr()  # drain the fixture's config-load banner
    config.wireless_pen_size = "large"
    assert pen_specs_for_product(132, config) is touch_controller.PEN_SPECS_BY_PRODUCT[791]
    assert capsys.readouterr().out == "pen_specs= wireless_size=large product=132 using=791\n"
    config.wireless_pen_size = "small"
    assert pen_specs_for_product(132, config) is touch_controller.PEN_SPECS_BY_PRODUCT[788]
    assert capsys.readouterr().out == "pen_specs= wireless_size=small product=132 using=788\n"


def test_unknown_product_with_config_keeps_the_no_table_line(capsys, config):
    # An id that is not the receiver keeps the plain fallback line: the
    # size setting applies to the receiver only.
    capsys.readouterr()  # drain the fixture's config-load banner
    assert pen_specs_for_product(999, config) is touch_controller.PEN_SPECS_DEFAULT
    assert capsys.readouterr().out == "pen_specs= no-table product=999 using=medium\n"


def test_wireless_size_round_trips_through_the_config_file(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    cfg = DriverConfig()
    cfg.wireless_pen_size = "large"
    path = tmp_path / "user.cfg"
    cfg.save_to_file(str(path))
    fresh = DriverConfig()
    assert fresh.load_from_file(str(path)) is True
    assert fresh.wireless_pen_size == "large"


def test_wireless_size_is_validated_and_normalized(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "size.cfg"
    path.write_text(json.dumps(
        {"motion_and_thresholds": {"wireless_pen_size": " Large "}}))
    cfg = DriverConfig()
    assert cfg.load_from_file(str(path)) is True
    assert cfg.wireless_pen_size == "large"

    bad = tmp_path / "bad.cfg"
    bad.write_text(json.dumps(
        {"motion_and_thresholds": {"wireless_pen_size": "huge"}}))
    cfg2 = DriverConfig()
    assert cfg2.load_from_file(str(bad)) is False
    assert cfg2.wireless_pen_size == "medium"


def test_wireless_thread_resolves_the_size_from_config(fake_hid, stub_send_input,
                                                       capsys, config):
    dev = FakeDevice(reports=[build_pen_report(0xE0, x=100, y=100)])
    fake_hid._queue.append(dev)
    state = TabletState()
    dispatcher = ActionDispatcher(state)
    enabled = threading.Event()
    enabled.set()
    stop = threading.Event()
    config.wireless_pen_size = "large"

    thread = threading.Thread(
        target=touch_controller.run_interface,
        args=("Pen", {"interface_number": 1, "path": b"fake-path", "product_id": 132},
              state, dispatcher, enabled, stop),
        kwargs={"idle_poll": 0.02, "cfg": config},
        daemon=True,
    )
    thread.start()
    deadline = time.monotonic() + 1.0
    while not state.pen_in_proximity and time.monotonic() < deadline:
        time.sleep(0.002)
    enabled.clear()
    thread.join(timeout=0.5)

    out = capsys.readouterr().out
    assert "pen_specs= wireless_size=large product=132 using=791" in out
    assert not thread.is_alive()


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
