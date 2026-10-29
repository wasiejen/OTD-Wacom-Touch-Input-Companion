"""Pure parsing, motion math, device pairing and dispatcher-table tests.

These lock the device-free seams the fix tests rely on: byte parsing, the
piecewise motion curve, HID enumeration pairing, and action lookup.
"""

import threading

import pytest

import touch_controller
from touch_controller import (
    ActionDispatcher,
    DeviceManagerThread,
    TabletState,
    calculate_piecewise_accelerated_delta,
    compute_centroid_and_spread,
    parse_sub_block,
)
from vk_codes import vk_codes_dict


@pytest.fixture
def config(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    return touch_controller.DriverConfig()


@pytest.fixture
def device_manager():
    return DeviceManagerThread(threading.Event(), threading.Event())


def test_parse_sub_block_rejects_short_block():
    assert parse_sub_block((0x03, 0xC0, 0x10)) is None


def test_parse_sub_block_rejects_empty_header():
    assert parse_sub_block((0, 0, 0, 0, 0, 0, 0, 0)) is None


def test_parse_sub_block_reports_sync():
    assert parse_sub_block((0x81, 0x02, 0, 0, 0, 0, 0, 0)) == ("SYNC", 0x02, None)


def test_parse_sub_block_decodes_coordinates():
    slot, status, pos = parse_sub_block((0x03, 0xC0, 0x10, 0x20, 0x34, 0, 0, 0))
    assert (slot, status) == (0x03, 0xC0)
    assert pos == ((0x10 << 4) | (0x34 >> 4), (0x20 << 4) | (0x34 & 0x0F))


def test_centroid_of_no_contacts():
    assert compute_centroid_and_spread({}) == ((0, 0), 0.0)


def test_centroid_and_spread_of_two_contacts():
    (cx, cy), spread = compute_centroid_and_spread({0: (0, 0), 1: (10, 0)})
    assert (cx, cy) == (5.0, 0.0)
    assert spread == pytest.approx(5.0)


def test_deadzone_discards_motion(config):
    config.cursor_deadzone = 1.0
    config.sync_sensitivities()
    assert calculate_piecewise_accelerated_delta(0.5, 0.0, config, "cursor_acceleration") == (0.0, 0.0)


def test_low_speed_uses_base_multiplier(config):
    dx, dy = calculate_piecewise_accelerated_delta(3.0, 0.0, config, "cursor_acceleration")
    assert dx == pytest.approx(3.0 * config.cursor_min_sens)
    assert dy == 0.0


def test_high_speed_uses_cap_multiplier(config):
    dx, _ = calculate_piecewise_accelerated_delta(100.0, 0.0, config, "cursor_acceleration")
    assert dx == pytest.approx(100.0 * config.cursor_max_sens)


def test_mid_speed_interpolates(config):
    dx, _ = calculate_piecewise_accelerated_delta(42.5, 0.0, config, "cursor_acceleration")
    slope = (config.cursor_max_sens - config.cursor_min_sens) / (config.cursor_speed_high - config.cursor_speed_low)
    gain = config.cursor_min_sens + slope * (42.5 - config.cursor_speed_low)
    assert dx == pytest.approx(42.5 * gain)


def test_disabled_acceleration_keeps_base_multiplier(config):
    config.feature_toggles["cursor_acceleration"] = False
    dx, _ = calculate_piecewise_accelerated_delta(100.0, 0.0, config, "cursor_acceleration")
    assert dx == pytest.approx(100.0 * config.cursor_min_sens)


def test_scroll_direction_signs_apply(config):
    config.scroll_sensitivity_y = -1.0
    config.sync_sensitivities()
    _, dy = calculate_piecewise_accelerated_delta(0.0, 20.0, config, "scroll_acceleration")
    assert dy < 0


def test_parent_id_strips_interface_suffix(device_manager):
    base = "\\hid#vid_056a&pid_0084"
    assert device_manager.get_device_parent_id(f"{base}&MI_01#abc".encode()) == base


def test_parent_id_handles_undecodable_bytes(device_manager):
    assert device_manager.get_device_parent_id(b"\xff&MI_00") == ""


def test_enumerate_pairs_wired_interfaces(device_manager, monkeypatch):
    devices = [
        {"vendor_id": 0x056A, "product_id": 132, "product_string": "Wacom Tablet",
         "path": b"base&MI_00", "interface_number": 0},
        {"vendor_id": 0x056A, "product_id": 132, "product_string": "Wacom Tablet",
         "path": b"base&MI_01", "interface_number": 1},
    ]
    fake = types_module(devices)
    monkeypatch.setattr(touch_controller, "hid", fake)

    paired = device_manager.enumerate_wacom_devices()

    assert len(paired) == 1
    assert paired[0]["pen_device"]["interface_number"] == 0
    assert paired[0]["touch_device"]["interface_number"] == 1


def test_enumerate_pairs_wireless_interfaces(device_manager, monkeypatch):
    devices = [
        {"vendor_id": 0x056A, "product_id": 791, "product_string": "Wacom Wireless Receiver",
         "path": b"base&MI_01", "interface_number": 1},
        {"vendor_id": 0x056A, "product_id": 791, "product_string": "Wacom Wireless Receiver",
         "path": b"base&MI_02", "interface_number": 2},
    ]
    fake = types_module(devices)
    monkeypatch.setattr(touch_controller, "hid", fake)

    paired = device_manager.enumerate_wacom_devices()

    assert len(paired) == 1
    assert paired[0]["touch_device"]["interface_number"] == 2


def test_enumerate_ignores_incomplete_pairs(device_manager, monkeypatch):
    devices = [
        {"vendor_id": 0x056A, "product_id": 132, "product_string": "Wacom Tablet",
         "path": b"base&MI_00", "interface_number": 0},
    ]
    monkeypatch.setattr(touch_controller, "hid", types_module(devices))
    assert device_manager.enumerate_wacom_devices() == []


def test_unknown_action_warns(stub_send_input, capsys):
    ad = ActionDispatcher(TabletState())
    ad.execute_mapped_action("does_not_exist")
    assert "Unknown action name" in capsys.readouterr().out


def test_none_action_is_silent(stub_send_input, capsys):
    ad = ActionDispatcher(TabletState())
    ad.execute_mapped_action(None)
    assert capsys.readouterr().out == ""
    assert stub_send_input == []


def test_mapped_tap_sends_key_down_and_up(stub_send_input):
    ad = ActionDispatcher(TabletState())
    ad.execute_mapped_action("undo")

    flags = ActionDispatcher
    assert stub_send_input == [
        ("key", vk_codes_dict["ctrl"], flags.KEYEVENTF_KEYDOWN),
        ("key", vk_codes_dict["z"], flags.KEYEVENTF_KEYDOWN),
        ("key", vk_codes_dict["z"], flags.KEYEVENTF_KEYUP),
        ("key", vk_codes_dict["ctrl"], flags.KEYEVENTF_KEYUP),
    ]


def types_module(devices):
    import types

    module = types.ModuleType("hid")
    module.enumerate = lambda: list(devices)
    module.device = lambda: None
    return module
