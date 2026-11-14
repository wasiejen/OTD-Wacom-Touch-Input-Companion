"""U386 / TODO #386: Apply Live reaches the RUNNING interface readers.

Evidence (projects/OTD-Wacom-Touch-Input-Companion/handover_u378.md, U381
reviewer P1): manage_connections built conn_config = base_config.fork_for_device
(...) and stored it in the connection dict, but never passed it to the
run_interface spawns, so every reader resolved its cfg from the module global
config (run_interface: cfg = config if cfg is None else cfg). The GUI edits
that fork (main(): gui_config = conn['config']; apply_settings mutates it in
place), so a live "Apply Live" reached no running reader and every setting
took effect only after a restart. The U381 button/touch lanes looked green
because their harness passes cfg explicitly to run_interface.

RED-before (measured against e24de31 before the fix, one run per test):
- test_manager_reader_uses_connection_fork: the running reader fired the
  GLOBAL decoy action (volume_up); the fork's action never fired.
- test_apply_live_reaches_running_reader: after the mid-run fork mutation the
  reader still fired the GLOBAL action; the new fork action never fired.
- test_manager_passes_fork_to_every_reader_spawn: every run_interface call
  carried cfg=None (the global fallback), not the connection fork.

The fix is the config-fork refresh contract: every reader thread runs on the
connection's own config object, and the GUI apply lane mutates that object in
place, so every per-report read sees the applied values. No device, no GUI,
no live injection: action selection is asserted through stub_send_input.
"""

import threading
import time

import pytest

from conftest import PlainDevice

import touch_controller
from touch_controller import (
    DeviceManagerThread,
    DriverConfig,
    run_interface,
)

VK_VOLUME_UP = 0xAF
VK_VOLUME_DOWN = 0xAE

# Recorded button 1 press rows, probe lines 11-12 (see
# tests/test_button_dial_routing.py for the capture source).
BTN1_TOUCH = [0x03, 0x80, 0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x00]
BTN1_PRESS = [0x03, 0x80, 0x00, 0x00, 0x01, 0x01, 0x00, 0x00, 0x00, 0x00]


def key_downs(events, vk):
    return sum(1 for e in events if e[0] == "key" and e[1] == vk and e[2] == 0)


def wait_until(predicate, timeout=1.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and not predicate():
        time.sleep(0.002)
    return predicate()


def wired_trio_devices(product_id):
    """Three wired interfaces (Pen MI_00, Touch MI_01, Buttons MI_02).

    The third interface makes manage_connections spawn the optional Buttons
    thread, so the pin covers all three reader spawns.
    """
    vendor = DeviceManagerThread.VENDOR_ID
    return [
        {"vendor_id": vendor, "product_id": product_id,
         "product_string": "Wacom Intuos5 touch M", "interface_number": iface,
         "path": path}
        for iface, path in enumerate((b"ACM&W87&0&MI_00", b"ACM&W87&0&MI_01",
                                      b"ACM&W87&0&MI_02"))
    ]


@pytest.fixture
def decoy_global(monkeypatch, tmp_path):
    """A module-global config that names a DIFFERENT action than any fork.

    Any reader still resolving cfg = config fires volume_up, so every pin
    below discriminates fork vs global instead of passing vacuously.
    """
    monkeypatch.chdir(tmp_path)
    global_cfg = DriverConfig()
    global_cfg.action_mapping["btn1_press"] = "volume_up"
    monkeypatch.setattr(touch_controller, "config", global_cfg)
    return global_cfg


def start_manager_on_fork(fake_hid, fork_action):
    """Spawn the manager over three plain fake devices; return the handles.

    The queue order follows the spawn order, but no pin below relies on it:
    the press rows are fed to EVERY queued device, and the 0x03 id-route
    fires on whichever interface owns a device, so the assertion holds no
    matter which thread opened which fake.
    """
    base = DriverConfig()
    base.action_mapping["btn1_press"] = fork_action
    fake_hid.enumerate = lambda: wired_trio_devices(789)
    devices = [PlainDevice(reports=[]) for _ in range(3)]
    fake_hid._queue.extend(devices)
    enabled = threading.Event()
    enabled.set()
    stop = threading.Event()
    manager = DeviceManagerThread(enabled, stop, poll_interval=0.1,
                                  base_config=base)
    manager.manage_connections()
    conn = manager.get_primary_connection()
    return manager, stop, conn, devices


def feed_press(devices):
    for dev in devices:
        dev.reports.extend([BTN1_TOUCH, BTN1_PRESS])


def stop_manager(stop, conn):
    stop.set()
    for thread in conn["threads"].values():
        thread.join(2)
        assert not thread.is_alive()


def test_manager_reader_uses_connection_fork(fake_hid, decoy_global,
                                             stub_send_input):
    """RED-before leg 1: the spawned reader must run on the connection fork.

    Pre-fix the reader resolved cfg = module global and fired the decoy's
    volume_up instead of the fork's volume_down.
    """
    manager, stop, conn, devices = start_manager_on_fork(fake_hid, "volume_down")
    try:
        assert conn["config"].action_mapping["btn1_press"] == "volume_down"
        assert conn["config"] is not decoy_global
        feed_press(devices)
        assert wait_until(lambda: key_downs(stub_send_input, VK_VOLUME_DOWN) > 0), (
            "running reader fired no fork action: the fork never reached the thread")
        assert key_downs(stub_send_input, VK_VOLUME_UP) == 0, (
            "running reader fired the module-global action, not the fork's")
    finally:
        stop_manager(stop, conn)


def test_apply_live_reaches_running_reader(fake_hid, decoy_global, stub_send_input):
    """RED-before leg 2: a mid-run fork mutation (the Apply Live shape) fires.

    The fork starts on the decoy action; one press row is consumed; then the
    test mutates the fork object exactly as gui.apply_settings_to_config does
    (an in-place mapping write, no rebuild), feeds a second press, and the
    SAME running reader must fire the NEW action.
    """
    manager, stop, conn, devices = start_manager_on_fork(fake_hid, "volume_up")
    try:
        fork = conn["config"]
        feed_press(devices)
        assert wait_until(lambda: key_downs(stub_send_input, VK_VOLUME_UP) > 0)

        fork.action_mapping["btn1_press"] = "volume_down"  # == Apply Live
        stub_send_input.clear()
        feed_press(devices)
        assert wait_until(lambda: key_downs(stub_send_input, VK_VOLUME_DOWN) > 0), (
            "post-Apply press fired no new fork action: Apply Live never "
            "reached the running reader")
        assert key_downs(stub_send_input, VK_VOLUME_UP) == 0, (
            "reader still fires the pre-Apply action after Apply Live")
    finally:
        stop_manager(stop, conn)


def test_manager_passes_fork_to_every_reader_spawn(fake_hid, decoy_global,
                                                   monkeypatch):
    """RED-before leg 3: every run_interface spawn carries the fork as cfg.

    The spawn shape is pinned directly: manage_connections must pass
    conn['config'] to ALL three readers (Pen, Touch, and the optional
    Buttons thread). Pre-fix every call recorded cfg=None, i.e. the
    module-global fallback.
    """
    calls = []

    def recording_run_interface(interface_type, device_info, state, dispatcher,
                                enabled, stop, idle_poll=None, cfg=None):
        calls.append((interface_type, cfg))

    monkeypatch.setattr(touch_controller, "run_interface", recording_run_interface)
    base = DriverConfig()
    fake_hid.enumerate = lambda: wired_trio_devices(789)
    enabled = threading.Event()
    enabled.set()
    stop = threading.Event()
    manager = DeviceManagerThread(enabled, stop, poll_interval=0.1,
                                  base_config=base)
    manager.manage_connections()
    conn = manager.get_primary_connection()
    stop.set()

    assert [iface for iface, _ in calls] == ["Pen", "Touch", "Buttons"]
    for iface, cfg in calls:
        assert cfg is conn["config"], (
            f"{iface} reader spawned with cfg={cfg!r}; expected the "
            "connection fork object, not the module-global fallback")
    assert conn["config"] is not decoy_global
