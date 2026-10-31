"""Device identity: interface-path identity keys, alias lane, subset save (U87).

Device-free: tmp files only, no real device, no GUI window.
"""

import json
import threading

import pytest

from touch_controller import (
    DEVICE_ALIAS_PREFIX,
    DEVICE_SETTINGS_PREFIX,
    DeviceManagerThread,
    DriverConfig,
    device_identity_hash,
)


WACOM_PATH = b"ACM&W87&0&MI_00"


# --- identity rule (U87 a) ----------------------------------------------------

def test_identity_key_is_path_plus_serial():
    """Identity key is the interface path and serial, never a bare index."""
    one = device_identity_hash(WACOM_PATH, "SN1")
    same = device_identity_hash(WACOM_PATH, "SN1")
    other_serial = device_identity_hash(WACOM_PATH, "SN2")
    other_interface = device_identity_hash(b"ACM&W87&0&MI_01", "SN1")

    assert one == same
    assert one != other_serial
    assert one != other_interface
    assert len(one) == 12
    assert int(one, 16) >= 0


def test_empty_serial_still_keys_the_interface():
    """Serials measured empty on every Wacom interface (U72 §1): path rules alone."""
    assert device_identity_hash(WACOM_PATH) == device_identity_hash(WACOM_PATH, "")
    assert device_identity_hash(WACOM_PATH) != device_identity_hash(
        WACOM_PATH.decode("utf-8"), "SN1")


# --- alias lane persistence (U87 a) -------------------------------------------

def test_alias_rename_persists_across_reload(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    identity = device_identity_hash(WACOM_PATH)
    cfg = DriverConfig()
    cfg.set_device_alias(identity, "My Intuos")
    path = tmp_path / "user.cfg"

    cfg.save_to_file(str(path))
    fresh = DriverConfig()
    assert fresh.load_from_file(str(path)) is True

    assert fresh.alias_for_identity(identity) == "My Intuos"
    assert json.loads(path.read_text())[f"{DEVICE_ALIAS_PREFIX}{identity}"] == "My Intuos"


def test_unassigned_identity_falls_back_to_none(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    cfg = DriverConfig()

    assert cfg.alias_for_identity("0" * 12) is None
    assert cfg.alias_for_identity(None) is None


def test_alias_lane_rejects_non_string(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "user.cfg"
    identity = device_identity_hash(WACOM_PATH)
    path.write_text(json.dumps({f"{DEVICE_ALIAS_PREFIX}{identity}": 42}))

    cfg = DriverConfig()
    assert cfg.load_from_file(str(path)) is False
    assert cfg.device_aliases == {}
    out = capsys.readouterr().out
    assert "needs an alias name" in out


# --- per-device settings lane (U87 b, e) --------------------------------------

def test_identity_settings_round_trip(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    identity = device_identity_hash(WACOM_PATH)
    cfg = DriverConfig()
    cfg.set_device_identity_settings(identity, {
        "motion_and_thresholds": {"pen_speed": 4.0, "orientation_degrees": 90},
        "feature_toggles": {"pen_absolute_mode": False},
    })
    path = tmp_path / "user.cfg"

    cfg.save_to_file(str(path))
    fresh = DriverConfig()
    assert fresh.load_from_file(str(path)) is True
    assert fresh.device_settings[identity]["motion_and_thresholds"]["pen_speed"] == 4.0
    assert fresh.fork_for_device(None, identity).pen_speed == 4.0
    assert fresh.fork_for_device(None, identity).orientation_degrees == 90
    assert fresh.fork_for_device(None, identity).feature_toggles["pen_absolute_mode"] is False


def test_identity_settings_merge_keys_keep_others(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    identity = device_identity_hash(WACOM_PATH)
    cfg = DriverConfig()
    cfg.set_device_identity_settings(identity, {
        "motion_and_thresholds": {"pen_speed": 4.0}})

    cfg.set_device_identity_settings(identity, {
        "feature_toggles": {"pen_absolute_mode": False}})

    entry = cfg.device_settings[identity]
    assert entry["motion_and_thresholds"]["pen_speed"] == 4.0
    assert entry["feature_toggles"] == {"pen_absolute_mode": False}


def test_identity_overlay_wins_over_alias(monkeypatch, tmp_path):
    """Per-device values beat alias-class values; global stays the fallback."""
    monkeypatch.chdir(tmp_path)
    identity = device_identity_hash(WACOM_PATH)
    cfg = DriverConfig()
    cfg.cursor_speed_low = 3.0
    cfg.devices = {"wacom-intuos5-m": {
        "motion_and_thresholds": {"cursor_speed_low": 7.0}}}
    cfg.set_device_identity_settings(identity, {
        "motion_and_thresholds": {"cursor_speed_low": 11.0}})

    per_device = cfg.fork_for_device("wacom-intuos5-m", identity)
    by_alias = cfg.fork_for_device("wacom-intuos5-m", "absent-identity")
    fallback = cfg.fork_for_device(None, "absent-identity")

    assert per_device.cursor_speed_low == 11.0
    assert by_alias.cursor_speed_low == 7.0
    assert fallback.cursor_speed_low == 3.0


def test_invalid_identity_entry_keeps_previous(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    identity = device_identity_hash(WACOM_PATH)
    cfg = DriverConfig()
    cfg.device_settings = {identity: {
        "motion_and_thresholds": {"pen_speed": "abc"}}}

    fork = cfg.fork_for_device(None, identity)

    assert fork.pen_speed == DriverConfig().pen_speed
    out = capsys.readouterr().out
    assert "Invalid identity entry" in out


def test_identity_lane_rejects_bad_sections(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    identity = device_identity_hash(WACOM_PATH)
    path = tmp_path / "user.cfg"
    path.write_text(json.dumps({
        f"{DEVICE_SETTINGS_PREFIX}{identity}": {"motion_and_thresholds": {"pen_speed": "abc"}},
    }))

    cfg = DriverConfig()
    assert cfg.load_from_file(str(path)) is False
    assert cfg.device_settings == {}


# --- manager wiring ------------------------------------------------------------

def wired_pair_devices(product_id, serial=""):
    vendor = DeviceManagerThread.VENDOR_ID
    paths = [b"ACM&W87&0&MI_00", b"ACM&W87&0&MI_01"]
    return [
        {"vendor_id": vendor, "product_id": product_id,
         "product_string": "Wacom Intuos5 touch M", "interface_number": 0,
         "serial_number": serial, "path": paths[0]},
        {"vendor_id": vendor, "product_id": product_id,
         "product_string": "Wacom Intuos5 touch M", "interface_number": 1,
         "serial_number": serial, "path": paths[1]},
    ]


def start_manager(monkeypatch, tmp_path, fake_hid, base, devices):
    monkeypatch.chdir(tmp_path)
    fake_hid.enumerate = lambda: devices
    stop = threading.Event()
    manager = DeviceManagerThread(threading.Event(), stop,
                                  poll_interval=0.1, base_config=base)
    manager.manage_connections()
    return manager, stop


def test_connection_carries_identity_and_alias(monkeypatch, tmp_path, fake_hid):
    devices = wired_pair_devices(789, "SN9")
    base = DriverConfig()
    manager, stop = start_manager(monkeypatch, tmp_path, fake_hid, base, devices)

    conn = manager.get_primary_connection()
    assert conn["identity"] == device_identity_hash(b"ACM&W87&0&MI_00", "SN9")
    assert conn["alias"] == "wacom-intuos5-m"
    assert manager.list_connections()[0] is conn

    stop.set()
    for thread in conn["threads"].values():
        thread.join(2)


def test_stored_identity_alias_wins_over_model_class(monkeypatch, tmp_path, fake_hid):
    devices = wired_pair_devices(789, "SN9")
    identity = device_identity_hash(b"ACM&W87&0&MI_00", "SN9")
    base = DriverConfig()
    base.set_device_alias(identity, "Kitchen Intuos")
    base.set_device_identity_settings(identity, {
        "motion_and_thresholds": {"cursor_speed_low": 12.0}})

    manager, stop = start_manager(monkeypatch, tmp_path, fake_hid, base, devices)
    conn = manager.get_primary_connection()

    assert conn["alias"] == "Kitchen Intuos"
    assert conn["config"].cursor_speed_low == 12.0
    assert base.cursor_speed_low == 5.0

    stop.set()
    for thread in conn["threads"].values():
        thread.join(2)
