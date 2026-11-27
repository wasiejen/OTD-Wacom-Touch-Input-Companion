"""Clipboard, lock and escape button actions (TODO #472).

Five assignable actions: copy (Ctrl+C), cut (Ctrl+X), paste (Ctrl+V),
lock_workstation and escape (Esc). The keyboard trio and Esc are pinned at
synthetic key-event level: the stub_send_input fixture records the emitted
SendInput events, so no live desktop event is injected and no clipboard
content is read or written anywhere in this file.

lock_workstation is NOT a key tap and the pins hold that line: Windows reads
the physical Win/L state for the lock hotkey, so an injected Win+L locks
nothing. The action calls the Win32 LockWorkStation API - the same call the
Start-menu Lock entry makes - and the pins assert that recorded call while the
conftest session guard keeps every un-stubbed LockWorkStation call (in this
file or any other) from ever reaching the desktop.
"""

import ctypes
import json

import pytest

import conftest
from vk_codes import vk_codes_dict
from wtd.config import DriverConfig
from wtd.gestures import TabletState, process_button_report
from wtd.sender import ActionDispatcher

NEW_ACTIONS = ("copy", "cut", "paste", "lock_workstation", "escape")

# The four actions that are shortcut taps. lock_workstation is excluded on
# purpose: it is a session API call, not an injected chord.
KEY_TAP_ACTIONS = ("copy", "cut", "paste", "escape")

# Emitted key sequence per tap action, as (vk name, release) events: every key
# down in order, then every key up in reverse (the send_key_shortcut contract).
EXPECTED_TAPS = {
    "copy": (("ctrl", False), ("c", False), ("c", True), ("ctrl", True)),
    "cut": (("ctrl", False), ("x", False), ("x", True), ("ctrl", True)),
    "paste": (("ctrl", False), ("v", False), ("v", True), ("ctrl", True)),
    "escape": (("esc", False), ("esc", True)),
}

DOWN = ActionDispatcher.KEYEVENTF_KEYDOWN
UP = ActionDispatcher.KEYEVENTF_KEYUP


def key_events(events):
    """The recorded key events as (vk, release) tuples, in emission order."""
    return [(e[1], e[2] == UP) for e in events if e[0] == "key"]


def expected_events(action):
    return [(vk_codes_dict[name], release) for name, release in EXPECTED_TAPS[action]]


def build_button_report(press=0):
    """Encode the recorded 0x03 column layout: byte 4 is the press bitmask."""
    return [0x03, 0x80, 0, 0, press, 0, 0, 0, 0, 0]


@pytest.fixture
def config(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    return DriverConfig()


@pytest.fixture
def state():
    return TabletState()


@pytest.fixture
def dispatcher(state, config):
    return ActionDispatcher(state, config)


# -- the vocabulary: assignable and validated -------------------------------

@pytest.mark.parametrize("action", NEW_ACTIONS)
def test_new_action_is_an_assignable_action(action, config, dispatcher):
    assert action in config.AVAILABLE_ACTIONS
    assert action in dispatcher.ACTION_DISPATCH


@pytest.mark.parametrize("action", NEW_ACTIONS)
def test_config_file_accepts_the_new_action_names(action, monkeypatch, tmp_path):
    """The action-name validator takes the new names (TODO #472)."""
    monkeypatch.chdir(tmp_path)
    cfg = DriverConfig()
    path = tmp_path / "user.cfg"
    path.write_text(json.dumps({"action_mapping": {"btn1_press": action}}))

    assert cfg.load_from_file(str(path)) is True
    assert cfg.action_mapping["btn1_press"] == action


def test_unknown_action_is_still_rejected(monkeypatch, tmp_path, capsys):
    """Widening the action set keeps the unknown-action rejection intact."""
    monkeypatch.chdir(tmp_path)
    cfg = DriverConfig()
    path = tmp_path / "user.cfg"
    path.write_text(json.dumps({"action_mapping": {"btn1_press": "not_a_real_action"}}))

    assert cfg.load_from_file(str(path)) is False
    out = capsys.readouterr().out
    assert "not_a_real_action" in out
    assert cfg.action_mapping["btn1_press"] != "not_a_real_action"


# -- the shortcut taps: key-event level, one tap per press edge --------------

@pytest.mark.parametrize("action", KEY_TAP_ACTIONS)
def test_button_press_edge_taps_the_mapped_action(action, state, config, dispatcher,
                                                 stub_send_input):
    config.action_mapping["btn1_press"] = action

    process_button_report(build_button_report(press=0x01), state, dispatcher, config)
    assert key_events(stub_send_input) == expected_events(action)

    stub_send_input.clear()
    process_button_report(build_button_report(press=0x00), state, dispatcher, config)
    assert stub_send_input == []
    assert dispatcher.button_held == {}


@pytest.mark.parametrize("action", KEY_TAP_ACTIONS)
def test_gesture_lane_taps_the_mapped_action(action, state, config, dispatcher,
                                            stub_send_input):
    """The same actions work on a gesture key through the gesture dispatch."""
    config.action_mapping["4f_swipe_up"] = action
    config.feature_toggles["4f_swipe_up"] = True

    dispatcher.dispatch_gesture_event("4f_swipe_up")
    assert key_events(stub_send_input) == expected_events(action)


# -- the session lock: an API call, never a key chord -----------------------

def test_lock_workstation_calls_the_session_lock_and_no_input(state, config,
                                                             dispatcher,
                                                             stub_lock_workstation,
                                                             stub_send_input):
    """The lock action is one LockWorkStation call and zero injected events."""
    dispatcher.execute_mapped_action("lock_workstation")

    assert stub_lock_workstation == ["LockWorkStation"]
    assert stub_send_input == [], (
        "an injected Win+L never locks Windows - the action must not tap keys")


def test_lock_workstation_failure_is_logged_not_raised(state, config, dispatcher,
                                                      monkeypatch, capsys):
    """A refused lock costs one log line, never an exception on the reader thread."""
    monkeypatch.setattr(ctypes.windll.user32, "LockWorkStation", lambda: 0)

    dispatcher.execute_mapped_action("lock_workstation")

    assert "LockWorkStation failed" in capsys.readouterr().out


def test_button_press_edge_locks_once(state, config, dispatcher, stub_lock_workstation):
    """The tablet-button lane drives the lock on the press edge only."""
    config.action_mapping["btn1_press"] = "lock_workstation"

    process_button_report(build_button_report(press=0x01), state, dispatcher, config)
    assert stub_lock_workstation == ["LockWorkStation"]

    process_button_report(build_button_report(press=0x00), state, dispatcher, config)
    assert stub_lock_workstation == ["LockWorkStation"]
    assert dispatcher.button_held == {}


def test_lock_without_the_stub_is_blocked_by_the_session_guard():
    """No lane can lock the desktop: the conftest guard owns LockWorkStation.

    The probe runs inside the selfcheck window, so the blocked call raises and
    is NOT counted as an offender - the guard never reaches Win32 either.
    """
    with conftest.inject_guard_selfcheck():
        with pytest.raises(RuntimeError, match="LIVE SESSION LOCK BLOCKED"):
            ctypes.windll.user32.LockWorkStation()
