"""U381 legs A+B: 0x03 button/dial reports reach the action layer from the
interfaces the driver actually opens.

Evidence (maintainer/draft/WTD_pen_support_probe_data.md): the wired capture
sets ONE interface constant (line 5: TOUCH_INTERFACE = 0, named there as the
wired PEN data interface) for the whole file, so the recorded button rows
(lines 11-52, report id 0x03) and the rotary dial sweep (lines 54-212, report
id 0x03) arrive on the same HID interface as the pen rows (line 218 onward,
report id 0x02). The U71 live path fed process_button_report only from a
THIRD interface; the Pen and Touch readers each drop every report whose id is
not their own, so a recorded press row reached no action and the dial stayed
dead. The first three tests are the RED-before repro of that break (measured
before the U381 fix: the pen reader dropped the report at its report-id
filter, the touch reader at its own, and no Buttons thread spawned at all
because the recorded device offers two interfaces).

Byte sequences are copied from those recorded rows. No device, no GUI, no
live injection: the pins assert action selection through stub_send_input.
"""

import contextlib
import threading
import time

import pytest

from conftest import PlainDevice

from touch_controller import (
    ActionDispatcher,
    DriverConfig,
    TabletState,
    run_interface,
)

VK_VOLUME_UP = 0xAF
VK_VOLUME_DOWN = 0xAE
VK_CTRL = 162
MOUSEEVENTF_MOVE = 0x0001

# Recorded button 1 lifecycle, probe lines 11-14.
BTN1_TOUCH = [0x03, 0x80, 0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x00]
BTN1_PRESS = [0x03, 0x80, 0x00, 0x00, 0x01, 0x01, 0x00, 0x00, 0x00, 0x00]
BTN1_PRESS_RELEASE = [0x03, 0x80, 0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x00]
FULL_RELEASE = [0x03, 0x80, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00]

# Recorded button N press rows (probe lines 12, 17, 22, 27, 32, 37, 42, 47):
# byte 4 press bit and byte 5 contact bit both carry bit N-1.
RECORDED_PRESS_ROWS = {
    1: [0x03, 0x80, 0x00, 0x00, 0x01, 0x01, 0x00, 0x00, 0x00, 0x00],
    2: [0x03, 0x80, 0x00, 0x00, 0x02, 0x02, 0x00, 0x00, 0x00, 0x00],
    3: [0x03, 0x80, 0x00, 0x00, 0x04, 0x04, 0x00, 0x00, 0x00, 0x00],
    4: [0x03, 0x80, 0x00, 0x00, 0x08, 0x08, 0x00, 0x00, 0x00, 0x00],
    5: [0x03, 0x80, 0x00, 0x00, 0x10, 0x10, 0x00, 0x00, 0x00, 0x00],
    6: [0x03, 0x80, 0x00, 0x00, 0x20, 0x20, 0x00, 0x00, 0x00, 0x00],
    7: [0x03, 0x80, 0x00, 0x00, 0x40, 0x40, 0x00, 0x00, 0x00, 0x00],
    8: [0x03, 0x80, 0x00, 0x00, 0x80, 0x80, 0x00, 0x00, 0x00, 0x00],
}

# Recorded clockwise dial sweep, probe lines 54-68: byte 2 carries the rotary
# contact bit 0x80 plus the angle in the low 7 bits; a decreasing angle is CW.
RECORDED_CW_SWEEP = [[0x03, 0x80, angle, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00]
                     for angle in (0x94, 0x93, 0x92, 0x90, 0x8F, 0x8D, 0x8B, 0x8A,
                                   0x89, 0x88, 0x86, 0x84, 0x82, 0x81, 0x80)]

# Recorded pen tablet report, probe line 218 (same interface, id 0x02).
PEN_ROW = [0x02, 0x20, 0x00, 0x00, 0x32, 0xB6, 0x00, 0x00, 0x00, 0xFD]


@pytest.fixture
def config(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    return DriverConfig()


def key_downs(events, vk):
    """Key-down events recorded for one VK code: one per action firing."""
    return sum(1 for e in events if e[0] == "key" and e[1] == vk and e[2] == 0)


def key_ups(events, vk):
    return sum(1 for e in events if e[0] == "key" and e[1] == vk and e[2] == 0x0002)


@contextlib.contextmanager
def interface_stream(fake_hid, interface_type, reports, state, dispatcher, cfg,
                     interface_number=0):
    """Run one real interface reader over a scripted report queue.

    The fake hid module serves the reader's device() call, so run_interface
    runs its real body: the same per-interface read size and the same
    enabled/stop event pair the manager passes. Exiting the block clears the
    enabled event (the reader's pause path) and joins, so the held-input
    release of thread exit is part of the recorded events.
    """
    device = PlainDevice(reports=[list(r) for r in reports])
    fake_hid._queue.append(device)
    enabled = threading.Event()
    enabled.set()
    stop = threading.Event()
    thread = threading.Thread(
        target=run_interface,
        args=(interface_type,
              {"interface_number": interface_number, "path": b"fake-path", "product_id": 789},
              state, dispatcher, enabled, stop),
        kwargs={"idle_poll": 0.01, "cfg": cfg},
        daemon=True,
    )
    thread.start()
    try:
        yield device
    finally:
        enabled.clear()
        thread.join(timeout=2.0)
        assert not thread.is_alive()


def wait_until(predicate, timeout=0.5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and not predicate():
        time.sleep(0.002)
    return predicate()


# ==============================================================================
# RED-before repro (legs A and B): the recorded 0x03 stream on the interface
# the driver opens reached no action.
# ==============================================================================

def test_recorded_press_row_on_pen_interface_fires_mapped_action(fake_hid, config,
                                                                 stub_send_input):
    """Leg A repro: probe line 12 (button 1 press) on the pen interface."""
    config.action_mapping["btn1_press"] = "volume_up"
    state = TabletState()
    dispatcher = ActionDispatcher(state, config)
    reports = [PEN_ROW, BTN1_TOUCH, BTN1_PRESS, BTN1_PRESS_RELEASE, FULL_RELEASE]

    with interface_stream(fake_hid, "Pen", reports, state, dispatcher, config):
        assert wait_until(lambda: key_downs(stub_send_input, VK_VOLUME_UP) > 0), (
            "recorded button 1 press row reached no action on the pen interface")

    assert state.button_press_mask == 0


def test_recorded_press_row_on_touch_interface_fires_mapped_action(fake_hid, config,
                                                                   stub_send_input):
    """Leg A repro, touch interface: the same press row must fire there too."""
    config.action_mapping["btn1_press"] = "volume_up"
    state = TabletState()
    dispatcher = ActionDispatcher(state, config)

    with interface_stream(fake_hid, "Touch", [BTN1_TOUCH, BTN1_PRESS, FULL_RELEASE],
                          state, dispatcher, config, interface_number=1):
        assert wait_until(lambda: key_downs(stub_send_input, VK_VOLUME_UP) > 0), (
            "recorded button 1 press row reached no action on the touch interface")


def test_recorded_dial_sweep_on_pen_interface_fires_step_action(fake_hid, config,
                                                                stub_send_input):
    """Leg B repro: the recorded CW sweep (probe lines 54-68) on the pen interface."""
    config.action_mapping["dial_cw"] = "volume_up"
    config.action_mapping["dial_ccw"] = "volume_down"
    state = TabletState()
    dispatcher = ActionDispatcher(state, config)

    with interface_stream(fake_hid, "Pen", RECORDED_CW_SWEEP, state, dispatcher, config):
        # Threshold 4.0 over the 20 angle units of the recorded sweep = 5 steps.
        assert wait_until(lambda: key_downs(stub_send_input, VK_VOLUME_UP) >= 5), (
            "recorded dial sweep fired no CW step action")

    assert key_downs(stub_send_input, VK_VOLUME_DOWN) == 0


# ==============================================================================
# Post-fix contract
# ==============================================================================

def test_pen_data_still_flows_when_button_rows_interleave(fake_hid, config, stub_send_input):
    """Control leg: the report-id routing must not disturb the pen stream."""
    config.action_mapping["btn1_press"] = "volume_up"
    state = TabletState()
    dispatcher = ActionDispatcher(state, config)
    reports = [PEN_ROW, BTN1_PRESS, PEN_ROW]
    moved = lambda: any(e[0] == "mouse" and (e[1] & MOUSEEVENTF_MOVE)
                        for e in stub_send_input)

    with interface_stream(fake_hid, "Pen", reports, state, dispatcher, config):
        assert wait_until(moved), "pen report on the shared interface moved no cursor"
        assert wait_until(lambda: key_downs(stub_send_input, VK_VOLUME_UP) > 0)


@pytest.mark.parametrize("slot", sorted(RECORDED_PRESS_ROWS))
def test_every_recorded_press_row_fires_its_button_action(slot, fake_hid, config,
                                                          stub_send_input):
    """Buttons 1-8: each recorded press row fires its own mapped action."""
    for n in range(1, 9):
        config.action_mapping[f"btn{n}_press"] = "volume_down" if n == slot else "volume_up"
    state = TabletState()
    dispatcher = ActionDispatcher(state, config)

    with interface_stream(fake_hid, "Pen", [RECORDED_PRESS_ROWS[slot], FULL_RELEASE],
                          state, dispatcher, config):
        assert wait_until(lambda: key_downs(stub_send_input, VK_VOLUME_DOWN) > 0), (
            f"button {slot} press row fired no action")

    assert key_downs(stub_send_input, VK_VOLUME_UP) == 0


def test_heartbeat_report_still_dropped_on_pen_interface(fake_hid, config, stub_send_input):
    """Report id 0xC0 (probe line 10) keeps firing nothing."""
    config.action_mapping["btn1_press"] = "volume_up"
    state = TabletState()
    dispatcher = ActionDispatcher(state, config)
    heartbeat = [0xC0, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x01]

    with interface_stream(fake_hid, "Pen", [heartbeat], state, dispatcher, config):
        time.sleep(0.05)

    assert stub_send_input == []
    assert state.button_press_mask == 0


def test_thread_exit_releases_a_held_touch_modifier(fake_hid, config, stub_send_input):
    """Safety: pausing the reader mid-touch must drop the held modifier."""
    config.feature_toggles["btn1_touch"] = True
    config.action_mapping["btn1_touch"] = "ctrl"
    state = TabletState()
    dispatcher = ActionDispatcher(state, config)

    with interface_stream(fake_hid, "Pen", [BTN1_TOUCH], state, dispatcher, config):
        assert wait_until(lambda: key_downs(stub_send_input, VK_CTRL) > 0)

    assert key_ups(stub_send_input, VK_CTRL) == 1
    assert dispatcher.touch_held == {}
