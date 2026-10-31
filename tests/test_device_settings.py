"""Per-device settings: alias rule, overlay, and isolation (U73).

Device-free: tmp files only, no real device, no GUI window.
"""

import json
import threading

import pytest

from touch_controller import (
    ActionDispatcher,
    DeviceManagerThread,
    DriverConfig,
    MODEL_CLASS_ALIASES,
    TabletState,
    WIRELESS_DEFAULT_ALIAS,
    resolve_device_alias,
    run_interface,
)

from conftest import ExplodingDevice


def fresh_config(monkeypatch, tmp_path):
    """Build a DriverConfig with no user.cfg reachable from cwd."""
    monkeypatch.chdir(tmp_path)
    return DriverConfig()


def overlay_entry():
    return {
        "motion_and_thresholds": {"cursor_speed_low": 7.0, "max_tap_duration": 0.4},
        "feature_toggles": {"2f_tap": True},
        "action_mapping": {"1f_tap": "right_click"},
    }


# --- alias rule (U72 §4) ----------------------------------------------------

@pytest.mark.parametrize("product_id,expected", [
    (788, "wacom-intuos5-s"),
    (789, "wacom-intuos5-m"),
    (791, "wacom-intuos5-l"),
])
def test_wired_model_class_alias(product_id, expected):
    assert resolve_device_alias(product_id, False) == expected


def test_receiver_gets_placeholder_alias():
    assert resolve_device_alias(132, True) == WIRELESS_DEFAULT_ALIAS


def test_manual_binding_resolves_receiver_twin():
    assert resolve_device_alias(132, True, {"wacom-intuos5-m": [132]}) == "wacom-intuos5-m"


def test_wired_class_wins_over_binding():
    assert resolve_device_alias(789, False, {"other-alias": [789]}) == "wacom-intuos5-m"


def test_unknown_device_resolves_to_none():
    assert resolve_device_alias(999, False) is None
    assert resolve_device_alias(999, True, {"a": [132]}) == "wireless"


def test_alias_table_covers_declared_product_ids():
    """Every wired model id the manager enumerates maps to a class alias."""
    for product_id in (788, 789, 791):
        assert product_id in MODEL_CLASS_ALIASES


# --- per-device config fork -------------------------------------------------

def test_fork_applies_overlay_and_rebuilds_sensitivities(monkeypatch, tmp_path):
    cfg = fresh_config(monkeypatch, tmp_path)
    cfg.devices = {"wacom-intuos5-m": overlay_entry()}

    fork = cfg.fork_for_device("wacom-intuos5-m")

    assert fork.cursor_speed_low == 7.0
    assert fork.max_tap_duration == 0.4
    assert fork.feature_toggles["2f_tap"] is True
    assert fork.action_mapping["1f_tap"] == "right_click"
    assert fork.sensitivities["cursor_acceleration"][3] == 7.0


def test_fork_leaves_base_untouched_and_owns_its_dicts(monkeypatch, tmp_path):
    cfg = fresh_config(monkeypatch, tmp_path)
    cfg.devices = {"wacom-intuos5-m": overlay_entry()}

    fork = cfg.fork_for_device("wacom-intuos5-m")
    fork.feature_toggles["2f_tap"] = False
    fork.action_mapping["1f_tap"] = "undo"
    fork.cursor_speed_low = 1.0

    assert cfg.cursor_speed_low == 5.0
    assert cfg.feature_toggles["2f_tap"] is False
    assert cfg.action_mapping["1f_tap"] == "left_click"


def test_fork_of_unknown_alias_is_the_global_view(monkeypatch, tmp_path):
    cfg = fresh_config(monkeypatch, tmp_path)
    cfg.cursor_speed_low = 3.0
    cfg.devices = {"tablet-a": overlay_entry()}

    none_fork = cfg.fork_for_device(None)
    missing_fork = cfg.fork_for_device("absent-alias")

    for fork in (none_fork, missing_fork):
        assert fork.cursor_speed_low == 3.0
        assert fork.feature_toggles == cfg.feature_toggles
        assert fork.action_mapping == cfg.action_mapping


def test_invalid_overlay_entry_keeps_global(monkeypatch, tmp_path, capsys):
    cfg = fresh_config(monkeypatch, tmp_path)
    cfg.devices = {"wacom-intuos5-m": {
        "motion_and_thresholds": {"cursor_speed_low": "abc", "max_tap_duration": 0.4},
        "feature_toggles": {"2f_tap": True},
    }}

    fork = cfg.fork_for_device("wacom-intuos5-m")
    out = capsys.readouterr().out

    assert fork.cursor_speed_low == 5.0
    assert fork.max_tap_duration == DriverConfig().max_tap_duration
    assert fork.feature_toggles["2f_tap"] is False
    assert "Invalid devices entry" in out
    assert "cursor_speed_low" in out


# --- schema round-trip --------------------------------------------------------

def test_device_entry_round_trip_and_reload(monkeypatch, tmp_path):
    cfg = fresh_config(monkeypatch, tmp_path)
    cfg.set_device_entry("wacom-intuos5-m", overlay_entry())
    path = tmp_path / "user.cfg"

    cfg.save_to_file(str(path))
    fresh = DriverConfig()
    assert fresh.load_from_file(str(path)) is True
    assert fresh.devices["wacom-intuos5-m"] == overlay_entry()

    fork = fresh.fork_for_device("wacom-intuos5-m")
    assert fork.cursor_speed_low == 7.0
    assert fork.action_mapping["1f_tap"] == "right_click"


def test_set_device_entry_keeps_bindings(monkeypatch, tmp_path):
    cfg = fresh_config(monkeypatch, tmp_path)
    cfg.set_device_entry("wacom-intuos5-m", {}, bindings=[132])

    cfg.set_device_entry("wacom-intuos5-m", overlay_entry())

    assert cfg.devices["wacom-intuos5-m"]["bindings"] == [132]
    assert cfg.devices["wacom-intuos5-m"]["feature_toggles"] == {"2f_tap": True}


def test_unknown_device_reload_falls_back_to_global(monkeypatch, tmp_path):
    """Global sections apply first; only the known alias overlays them."""
    cfg = fresh_config(monkeypatch, tmp_path)
    cfg.cursor_speed_low = 3.0
    cfg.set_device_entry("wacom-intuos5-m", overlay_entry())
    path = tmp_path / "user.cfg"
    cfg.save_to_file(str(path))

    fresh = DriverConfig()
    fresh.load_from_file(str(path))
    unknown = fresh.fork_for_device(None)

    assert unknown.cursor_speed_low == 3.0
    assert json.loads(path.read_text())["devices"]["wacom-intuos5-m"]


# --- manual bindings ----------------------------------------------------------

def test_device_bindings_skips_invalid_entries(monkeypatch, tmp_path):
    cfg = fresh_config(monkeypatch, tmp_path)
    cfg.devices = {
        "bound-alias": {"bindings": [132]},
        "broken-alias": {"bindings": "132"},
        "plain-alias": {"feature_toggles": {}},
        "empty-alias": {"bindings": []},
    }

    assert cfg.device_bindings() == {"bound-alias": [132]}
# --- per-connection runtime isolation (U73 c) ---------------------------------

def touch_down_block(slot, x, y):
    return [slot, 0xC0, (x >> 4) & 0xFF, (y >> 4) & 0xFF, ((x & 0xF) << 4) | (y & 0xF), 0, 0, 0]


def release_block(slot):
    return [slot, 0x20, 0, 0, 0, 0, 0, 0]


def touch_report(blocks):
    return [0x02, len(blocks)] + [b for block in blocks for b in block]


def wired_pair_devices(product_id):
    vendor = DeviceManagerThread.VENDOR_ID
    paths = [b"ACM&W73&0&MI_00", b"ACM&W73&0&MI_01"]
    return [
        {"vendor_id": vendor, "product_id": product_id,
         "product_string": "Wacom Intuos5 touch M", "interface_number": 0,
         "path": paths[0]},
        {"vendor_id": vendor, "product_id": product_id,
         "product_string": "Wacom Intuos5 touch M", "interface_number": 1,
         "path": paths[1]},
    ]


def test_dispatcher_uses_connection_config(monkeypatch, tmp_path, stub_send_input):
    monkeypatch.chdir(tmp_path)
    base = DriverConfig()
    base.devices = {"wacom-intuos5-m": {
        "feature_toggles": {"2f_tap": True},
        "action_mapping": {"2f_tap": "middle_click"},
    }}
    fork = base.fork_for_device("wacom-intuos5-m")
    dispatcher = ActionDispatcher(TabletState(), fork)

    dispatcher.dispatch_gesture_event("2f_tap")

    flags = [event[1] for event in stub_send_input]
    assert ActionDispatcher.MOUSEEVENTF_MIDDLEDOWN in flags
    assert ActionDispatcher.MOUSEEVENTF_MIDDLEUP in flags


def test_dispatcher_without_cfg_falls_back_to_module_config(monkeypatch, tmp_path, stub_send_input):
    """2f_tap is off by default, so a plain dispatcher fires nothing."""
    monkeypatch.chdir(tmp_path)
    import touch_controller
    monkeypatch.setattr(touch_controller, "config", DriverConfig())
    dispatcher = ActionDispatcher(TabletState())

    dispatcher.dispatch_gesture_event("2f_tap")

    assert stub_send_input == []


def test_touch_thread_runs_connection_fork(monkeypatch, tmp_path, fake_hid, stub_send_input):
    """1f_tap fires with the fork's mapping, not the module config's action."""
    monkeypatch.chdir(tmp_path)
    base = DriverConfig()
    base.devices = {"wacom-intuos5-m": {"action_mapping": {"1f_tap": "middle_click"}}}
    fork = base.fork_for_device("wacom-intuos5-m")

    reports = [touch_report([touch_down_block(1, 100, 200)]),
               touch_report([release_block(1)])]
    fake_hid._queue.append(ExplodingDevice(reports))
    state = TabletState()
    dispatcher = ActionDispatcher(state, fork)
    enabled = threading.Event()
    enabled.set()

    run_interface("Touch", {"interface_number": 1, "path": b"x"},
                  state, dispatcher, enabled, threading.Event())

    flags = [event[1] for event in stub_send_input]
    assert ActionDispatcher.MOUSEEVENTF_MIDDLEDOWN in flags


def test_manager_connection_isolates_config(monkeypatch, tmp_path, fake_hid):
    monkeypatch.chdir(tmp_path)
    fake_hid.enumerate = lambda: wired_pair_devices(789)
    base = DriverConfig()
    base.devices = {"wacom-intuos5-m": {
        "motion_and_thresholds": {"cursor_speed_low": 7.0},
    }}
    stop = threading.Event()
    manager = DeviceManagerThread(threading.Event(), stop,
                                  poll_interval=0.1, base_config=base)

    manager.manage_connections()
    conn = manager.get_primary_connection()

    assert conn["alias"] == "wacom-intuos5-m"
    assert conn["config"] is not base
    assert conn["config"].cursor_speed_low == 7.0
    assert base.cursor_speed_low == 5.0
    assert conn["action"].config is conn["config"]

    stop.set()
    for thread in conn["threads"].values():
        thread.join(2)


def test_manager_binds_wireless_twin_via_manual_override(monkeypatch, tmp_path, fake_hid):
    """The receiver twin resolves to the alias its bindings point at."""
    monkeypatch.chdir(tmp_path)
    vendor = DeviceManagerThread.VENDOR_ID
    fake_hid.enumerate = lambda: [
        {"vendor_id": vendor, "product_id": 132,
         "product_string": "Wacom Wireless Receiver", "interface_number": 1,
         "path": b"ACM&R73&0&MI_01"},
        {"vendor_id": vendor, "product_id": 132,
         "product_string": "Wacom Wireless Receiver", "interface_number": 2,
         "path": b"ACM&R73&0&MI_02"},
    ]
    base = DriverConfig()
    base.devices = {"wacom-intuos5-m": {
        "bindings": [132],
        "motion_and_thresholds": {"cursor_speed_low": 7.0},
    }}
    stop = threading.Event()
    manager = DeviceManagerThread(threading.Event(), stop,
                                  poll_interval=0.1, base_config=base)

    manager.manage_connections()
    conn = manager.get_primary_connection()

    assert conn["alias"] == "wacom-intuos5-m"
    assert conn["config"].cursor_speed_low == 7.0

    stop.set()