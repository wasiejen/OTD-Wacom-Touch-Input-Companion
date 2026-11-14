"""Touch buttons + rotary dial (U71): 0x03 parser, step logic, config lanes.

Synthetic 10-byte 0x03 reports per the recorded probe layout
(maintainer/draft/WTD_pen_support_probe_data.md lines 10-212).
No device, no GUI.
"""

import json
import threading
import time

import pytest

from conftest import PlainDevice

import touch_controller
from touch_controller import (
    ActionDispatcher,
    BUTTON_CENTRAL_MASK,
    BUTTON_PRESS_MASKS,
    CENTRAL_BUTTON_SLOT,
    DIAL_ANGLE_RANGE,
    DeviceManagerThread,
    DriverConfig,
    TabletState,
    decode_button_report,
    dial_angle_delta,
    process_button_report,
)

BTN_KEYS = [f"btn{n}_press" for n in range(1, 9)] + ["central_press"]
DIAL_KEYS = ["dial_cw", "dial_ccw"]
VK_VOLUME_UP = 0xAF
VK_VOLUME_DOWN = 0xAE


def build_button_report(press=0, touch=0, central=0, dial=0):
    """Encode the recorded column layout: byte 2 dial, byte 3 central,
    byte 4 press bitmask, byte 5 touch-contact mask."""
    return [0x03, 0x80, dial, central, press, touch, 0, 0, 0, 0]


def dial_report(angle_byte):
    return build_button_report(dial=angle_byte)


# Recorded clockwise sweep rows (probe file lines 54-68), masked angle
# decreasing: 0x14, 0x13, 0x12, 0x10, 0x0F, 0x0D, 0x0B, 0x0A, 0x09, 0x08,
# 0x06, 0x04, 0x02, 0x01, 0x00.
CW_SWEEP = [0x94, 0x93, 0x92, 0x90, 0x8F, 0x8D, 0x8B, 0x8A,
            0x89, 0x88, 0x86, 0x84, 0x82, 0x81, 0x80]


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
    process_button_report(report, state, dispatcher, config)


def mouse_flags(events):
    return [e[1] for e in events if e[0] == "mouse"]


def dial_taps(events):
    """Volume-key taps: one dial step is a down plus an up event."""
    return [e[1] for e in events if e[0] == "key"
            and e[1] in (VK_VOLUME_UP, VK_VOLUME_DOWN)]


def dial_step_count(events, vk):
    """Emitted dial steps: one key-down event per step."""
    return sum(1 for e in events if e[0] == "key" and e[1] == vk and e[2] == 0)


def set_volume_dial(config):
    config.action_mapping["dial_cw"] = "volume_up"
    config.action_mapping["dial_ccw"] = "volume_down"


def test_recorded_press_row_decodes_columns():
    parsed = decode_button_report([0x03, 0x80, 0x00, 0x00, 0x01, 0x01, 0x00, 0x00, 0x00, 0x00])
    assert parsed == {"press": 0x01, "touch": 0x01, "central": False, "dial": 0x00}


def test_press_bitmask_is_adder_not_ids():
    """His comment: buttons 1+2 => 0x03, buttons 2+3 => 0x06."""
    assert decode_button_report(build_button_report(press=0x03))["press"] == 0x03
    assert decode_button_report(build_button_report(press=0x06))["press"] == 0x06


def test_short_packet_and_foreign_shape_are_tolerated(state, dispatcher, config, stub_send_input):
    assert decode_button_report([0x03, 0x80, 0x00]) is None
    assert decode_button_report([0x02, 0x80, 0x00, 0x00, 0x01, 0x00, 0, 0, 0, 0]) is None
    assert decode_button_report([0x03, 0x00, 0x00, 0x00, 0x01, 0x01, 0, 0, 0, 0]) is None
    process([0x03, 0x80, 0x00], state, dispatcher, config)
    assert stub_send_input == []
    assert state.button_press_mask == 0


def test_touch_contact_alone_fires_nothing(state, dispatcher, config, stub_send_input):
    """Recorded "button 1 touch contact" row carries only byte 5."""
    process(build_button_report(touch=0x01), state, dispatcher, config)
    assert stub_send_input == []
    assert state.button_press_mask == 0


def test_button_press_and_release_hold_mouse_action(state, dispatcher, config, stub_send_input):
    config.action_mapping["btn1_press"] = "left_click"
    process(build_button_report(press=0x01, touch=0x01), state, dispatcher, config)
    assert mouse_flags(stub_send_input) == [0x0002]
    # Recorded press-release row: contact mask stays, press bit clears.
    stub_send_input.clear()
    process(build_button_report(touch=0x01), state, dispatcher, config)
    assert mouse_flags(stub_send_input) == [0x0004]
    assert dispatcher.button_held == {}
    # Recorded full-release row must not send a second UP.
    stub_send_input.clear()
    process(build_button_report(), state, dispatcher, config)
    assert stub_send_input == []


def test_multi_button_bitmask_presses_both(state, dispatcher, config, stub_send_input):
    config.action_mapping["btn1_press"] = "left_click"
    config.action_mapping["btn2_press"] = "right_click"
    process(build_button_report(press=0x03, touch=0x03), state, dispatcher, config)
    assert mouse_flags(stub_send_input) == [0x0002, 0x0008]
    stub_send_input.clear()
    process(build_button_report(), state, dispatcher, config)
    assert sorted(mouse_flags(stub_send_input)) == [0x0004, 0x0010]


def test_bit_positions_reach_button_eight(state, dispatcher, config, stub_send_input):
    config.action_mapping["btn8_press"] = "right_click"
    process(build_button_report(press=0x80), state, dispatcher, config)
    assert dispatcher.button_held == {8: "right_click"}
    assert mouse_flags(stub_send_input) == [0x0008]


def test_non_mouse_button_action_taps_once(state, dispatcher, config, stub_send_input):
    config.action_mapping["btn3_press"] = "undo"
    process(build_button_report(press=0x04), state, dispatcher, config)
    assert len([e for e in stub_send_input if e[0] == "key"]) == 4
    stub_send_input.clear()
    process(build_button_report(), state, dispatcher, config)
    assert stub_send_input == []
    assert dispatcher.button_held == {}


def test_button_toggle_off_suppresses_press(state, dispatcher, config, stub_send_input):
    config.feature_toggles["btn1_press"] = False
    process(build_button_report(press=0x01), state, dispatcher, config)
    assert stub_send_input == []
    process(build_button_report(), state, dispatcher, config)
    assert stub_send_input == []


def test_central_button_press_bit(state, dispatcher, config, stub_send_input):
    """Recorded central press row carries byte 3 = 0x01 only."""
    config.action_mapping["central_press"] = "middle_click"
    process([0x03, 0x80, 0x00, BUTTON_CENTRAL_MASK, 0x00, 0x00, 0, 0, 0, 0],
            state, dispatcher, config)
    assert mouse_flags(stub_send_input) == [0x0020]
    assert dispatcher.button_held == {CENTRAL_BUTTON_SLOT: "middle_click"}
    stub_send_input.clear()
    process(build_button_report(), state, dispatcher, config)
    assert mouse_flags(stub_send_input) == [0x0040]


@pytest.mark.parametrize("key", BTN_KEYS)
def test_each_control_fires_its_mapped_action(key, state, dispatcher, config, stub_send_input):
    """DoD: all 9 controls fire through the mapping table."""
    config.action_mapping[key] = "volume_up"
    if key == "central_press":
        report = build_button_report(central=BUTTON_CENTRAL_MASK)
    else:
        slot = int(key.split("btn")[1].split("_")[0])
        report = build_button_report(press=BUTTON_PRESS_MASKS[slot - 1])
    process(report, state, dispatcher, config)
    process(build_button_report(), state, dispatcher, config)
    assert dial_taps(stub_send_input) == [VK_VOLUME_UP, VK_VOLUME_UP]


def test_release_held_inputs_releases_tablet_buttons(state, dispatcher, stub_send_input):
    dispatcher.button_held[2] = "right_click"
    released = dispatcher.release_held_inputs()
    assert mouse_flags(stub_send_input) == [0x0010]
    assert released == ["button_2"]
    assert dispatcher.button_held == {}


def test_dial_decreasing_sweep_emits_cw_steps(state, dispatcher, config, stub_send_input):
    set_volume_dial(config)
    for angle_byte in CW_SWEEP:
        process(dial_report(angle_byte), state, dispatcher, config)
    assert dial_step_count(stub_send_input, VK_VOLUME_UP) == 5
    assert dial_step_count(stub_send_input, VK_VOLUME_DOWN) == 0


def test_dial_increasing_sweep_emits_ccw_steps(state, dispatcher, config, stub_send_input):
    set_volume_dial(config)
    for angle_byte in (0x84, 0x86, 0x88, 0x8A, 0x8C):
        process(dial_report(angle_byte), state, dispatcher, config)
    assert dial_step_count(stub_send_input, VK_VOLUME_DOWN) == 2
    assert dial_step_count(stub_send_input, VK_VOLUME_UP) == 0
    assert state.dial_acc == 0.0


def test_dial_first_packet_sets_origin_only(state, dispatcher, config, stub_send_input):
    set_volume_dial(config)
    process(dial_report(0x94), state, dispatcher, config)
    assert stub_send_input == []
    assert state.dial_last_angle == 0x14


def test_dial_accumulates_fractional_steps(state, dispatcher, config, stub_send_input):
    set_volume_dial(config)
    for angle_byte in (0x86, 0x84):
        process(dial_report(angle_byte), state, dispatcher, config)
    assert stub_send_input == []
    assert state.dial_acc == -2.0
    process(dial_report(0x82), state, dispatcher, config)
    assert dial_step_count(stub_send_input, VK_VOLUME_UP) == 1
    assert state.dial_acc == 0.0


def test_dial_wrap_at_recorded_range_is_clockwise(state, dispatcher, config, stub_send_input):
    """Recorded wrap: 0x80 (0x00) followed by 0xC6 (0x46) is one CW unit."""
    set_volume_dial(config)
    for angle_byte in (0x84, 0x80, 0xC6, 0xC5):
        process(dial_report(angle_byte), state, dispatcher, config)
    assert dial_step_count(stub_send_input, VK_VOLUME_UP) == 1
    assert dial_step_count(stub_send_input, VK_VOLUME_DOWN) == 0
    assert state.dial_acc == -2.0


def test_dial_wrap_backwards_is_counter_clockwise(state, dispatcher, config, stub_send_input):
    set_volume_dial(config)
    for angle_byte in (0xC5, 0xC6, 0x80, 0x81, 0x83):
        process(dial_report(angle_byte), state, dispatcher, config)
    assert dial_step_count(stub_send_input, VK_VOLUME_DOWN) == 1
    assert dial_step_count(stub_send_input, VK_VOLUME_UP) == 0
    assert state.dial_acc == 1.0


def test_dial_delta_wraps_at_half_range():
    assert dial_angle_delta(0x46, 0x00) == -1
    assert dial_angle_delta(0x00, 0x46) == 1
    assert dial_angle_delta(0x10, 0x12) == -2


def test_lift_resets_dial_origin_and_accumulator(state, dispatcher, config, stub_send_input):
    set_volume_dial(config)
    for angle_byte in (0x86, 0x84):
        process(dial_report(angle_byte), state, dispatcher, config)
    process(build_button_report(), state, dispatcher, config)
    assert state.dial_last_angle is None
    assert state.dial_acc == 0.0
    stub_send_input.clear()
    process(dial_report(0x80), state, dispatcher, config)
    assert stub_send_input == []


def test_dial_toggle_off_suppresses_steps(state, dispatcher, config, stub_send_input):
    set_volume_dial(config)
    config.feature_toggles["dial_cw"] = False
    for angle_byte in CW_SWEEP:
        process(dial_report(angle_byte), state, dispatcher, config)
    assert stub_send_input == []
    assert state.dial_acc == 0.0


def test_non_positive_threshold_emits_no_steps(state, dispatcher, config, stub_send_input):
    set_volume_dial(config)
    config.dial_step_threshold = 0.0
    for angle_byte in CW_SWEEP:
        process(dial_report(angle_byte), state, dispatcher, config)
    assert stub_send_input == []


def test_button_report_touch_mask_never_gates_press(state, dispatcher, config, stub_send_input):
    """Contact and press columns stay independent."""
    config.action_mapping["btn5_press"] = "right_click"
    process(build_button_report(touch=0x3F), state, dispatcher, config)
    assert stub_send_input == []
    process(build_button_report(press=0x10, touch=0x3F), state, dispatcher, config)
    assert mouse_flags(stub_send_input) == [0x0008]


def test_new_keys_join_save_dict(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    cfg = DriverConfig()
    path = tmp_path / "user.cfg"
    cfg.save_to_file(str(path))
    data = json.loads(path.read_text())

    for key in BTN_KEYS + DIAL_KEYS:
        assert key in data["action_mapping"], key
        assert data["feature_toggles"][key] is True, key
    assert data["motion_and_thresholds"]["dial_step_threshold"] == cfg.dial_step_threshold


def test_button_config_round_trip(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    cfg = DriverConfig()
    cfg.action_mapping["btn1_press"] = "task_view"
    cfg.action_mapping["dial_cw"] = "prev"
    cfg.feature_toggles["central_press"] = False
    cfg.dial_step_threshold = 7.0
    path = tmp_path / "user.cfg"
    cfg.save_to_file(str(path))

    fresh = DriverConfig()
    assert fresh.load_from_file(str(path)) is True
    assert fresh.action_mapping["btn1_press"] == "task_view"
    assert fresh.action_mapping["dial_cw"] == "prev"
    assert fresh.feature_toggles["central_press"] is False
    assert fresh.dial_step_threshold == 7.0


def test_buttons_thread_processes_report_and_releases_on_exit(fake_hid, stub_send_input, monkeypatch):
    monkeypatch.setitem(touch_controller.config.action_mapping, "btn1_press", "left_click")
    dev = PlainDevice(reports=[build_button_report(press=0x01, touch=0x01)])
    fake_hid._queue.append(dev)
    state = TabletState()
    dispatcher = ActionDispatcher(state)
    enabled = threading.Event()
    enabled.set()
    stop = threading.Event()

    thread = threading.Thread(
        target=touch_controller.run_interface,
        args=("Buttons", {"interface_number": 2, "path": b"fake-path"},
              state, dispatcher, enabled, stop),
        kwargs={"idle_poll": 0.02},
        daemon=True,
    )
    thread.start()

    deadline = time.monotonic() + 1.0
    while state.button_press_mask == 0 and time.monotonic() < deadline:
        time.sleep(0.002)
    enabled.clear()
    thread.join(timeout=0.5)

    assert not thread.is_alive()
    assert state.button_press_mask == 0x01
    assert 0x0002 in mouse_flags(stub_send_input)
    assert 0x0004 in mouse_flags(stub_send_input), "thread exit must release the held button"


def make_wired_interfaces(extra=None):
    vendor = DeviceManagerThread.VENDOR_ID
    paths = {0: b"ACM&WI&22&MI_00", 1: b"ACM&WI&22&MI_01", 2: b"ACM&WI&22&MI_02"}
    interfaces = [0, 1] + ([extra] if extra is not None else [])
    return [
        {"vendor_id": vendor, "product_id": 789, "product_string": "Wacom Intuos Pro",
         "interface_number": iface, "path": paths[iface]}
        for iface in interfaces
    ]


def test_enumeration_attaches_optional_third_interface(fake_hid):
    fake_hid.enumerate = lambda: make_wired_interfaces(extra=2)
    manager = DeviceManagerThread(threading.Event(), threading.Event())

    pair = manager.enumerate_wacom_devices()[0]

    assert pair["buttons_device"]["interface_number"] == 2
    assert pair["touch_device"]["interface_number"] == 1


def test_enumeration_has_no_buttons_device_for_two_interfaces(fake_hid):
    fake_hid.enumerate = lambda: make_wired_interfaces()
    manager = DeviceManagerThread(threading.Event(), threading.Event())

    pair = manager.enumerate_wacom_devices()[0]

    assert pair["buttons_device"] is None


def test_manager_spawns_buttons_thread_next_to_pen_touch(fake_hid):
    fake_hid.enumerate = lambda: make_wired_interfaces(extra=2)
    for _ in range(3):
        fake_hid._queue.append(PlainDevice())

    enabled = threading.Event()
    enabled.set()
    stop = threading.Event()
    manager = DeviceManagerThread(enabled, stop)
    manager.manage_connections()

    conn = next(iter(manager.active_connections.values()))
    assert set(conn['threads']) == {"Pen", "Touch", "Buttons"}
    stop.set()
    for thread in conn['threads'].values():
        thread.join(timeout=2)
    assert all(not thread.is_alive() for thread in conn['threads'].values())


def test_manager_keeps_two_threads_without_third_interface(fake_hid):
    fake_hid.enumerate = lambda: make_wired_interfaces()
    for _ in range(2):
        fake_hid._queue.append(PlainDevice())

    enabled = threading.Event()
    enabled.set()
    stop = threading.Event()
    manager = DeviceManagerThread(enabled, stop)
    manager.manage_connections()

    conn = next(iter(manager.active_connections.values()))
    assert set(conn['threads']) == {"Pen", "Touch"}
    stop.set()
    for thread in conn['threads'].values():
        thread.join(timeout=2)
