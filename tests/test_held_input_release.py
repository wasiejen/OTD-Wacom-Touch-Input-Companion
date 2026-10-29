"""Held-input release on thread exit (TODO #249).

The Win32 SendInput call is stubbed; the HID device is faked. No device,
no GUI, no real input injection.
"""

import threading

import pytest

import touch_controller
from touch_controller import ActionDispatcher, TabletState
from vk_codes import vk_codes_dict

from conftest import ExplodingDevice, FakeDevice


@pytest.fixture
def dispatcher(stub_send_input):
    state = TabletState()
    return ActionDispatcher(state), stub_send_input


def key_events(events):
    return [e for e in events if e[0] == "key"]


def mouse_events(events):
    return [e for e in events if e[0] == "mouse"]


def test_release_sends_ctrl_up_when_ctrl_held(dispatcher):
    ad, events = dispatcher
    ad.press_key("ctrl")
    events.clear()

    released = ad.release_held_inputs()

    assert released == ["ctrl"]
    assert key_events(events) == [("key", vk_codes_dict["ctrl"], ActionDispatcher.KEYEVENTF_KEYUP)]


def test_release_sends_left_up_when_button_held(dispatcher):
    ad, events = dispatcher
    ad.handle_left_hold()
    events.clear()

    released = ad.release_held_inputs()

    assert released == ["left_button"]
    assert mouse_events(events) == [
        ("mouse", ActionDispatcher.MOUSEEVENTF_LEFTUP, 0, 0, 0)
    ]
    assert ad.left_held is False
    assert ad.state.is_left_held is False


def test_release_when_nothing_held_emits_nothing(dispatcher):
    ad, events = dispatcher
    events.clear()

    assert ad.release_held_inputs() == []
    assert events == []


def test_release_is_idempotent(dispatcher):
    ad, events = dispatcher
    ad.press_key("ctrl")
    ad.handle_left_hold()

    ad.release_held_inputs()
    events.clear()

    assert ad.release_held_inputs() == []
    assert events == []
    assert ad.held_keys == set()


def test_release_covers_both_held_classes(dispatcher):
    ad, events = dispatcher
    ad.press_key("ctrl")
    ad.handle_left_hold()

    released = ad.release_held_inputs()

    assert set(released) == {"ctrl", "left_button"}
    assert ("key", vk_codes_dict["ctrl"], ActionDispatcher.KEYEVENTF_KEYUP) in events
    assert ("mouse", ActionDispatcher.MOUSEEVENTF_LEFTUP, 0, 0, 0) in events


def test_thread_exit_on_oserror_releases_ctrl(fake_hid, stub_send_input):
    state = TabletState()
    ad = ActionDispatcher(state)
    ad.press_key("ctrl")

    dev = ExplodingDevice()
    fake_hid._queue.append(dev)

    enabled = threading.Event()
    enabled.set()
    stop = threading.Event()

    touch_controller.run_interface(
        "Touch", {"interface_number": 1, "path": b"fake"}, state, ad, enabled, stop
    )

    assert ("key", vk_codes_dict["ctrl"], ActionDispatcher.KEYEVENTF_KEYUP) in stub_send_input
    assert dev.closed is True


def test_thread_exit_on_oserror_releases_left_button(fake_hid, stub_send_input):
    state = TabletState()
    ad = ActionDispatcher(state)
    ad.handle_left_hold()

    dev = ExplodingDevice()
    fake_hid._queue.append(dev)

    enabled = threading.Event()
    enabled.set()
    stop = threading.Event()

    touch_controller.run_interface(
        "Touch", {"interface_number": 1, "path": b"fake"}, state, ad, enabled, stop
    )

    assert ("mouse", ActionDispatcher.MOUSEEVENTF_LEFTUP, 0, 0, 0) in stub_send_input
    assert state.is_left_held is False


def test_run_interface_calls_release_held_inputs_on_exit(fake_hid):
    calls = []

    class RecordingDispatcher:
        def release_held_inputs(self):
            calls.append("released")

    dev = FakeDevice()
    fake_hid._queue.append(dev)

    enabled = threading.Event()
    enabled.set()
    stop = threading.Event()
    stop.set()

    touch_controller.run_interface(
        "Touch", {"interface_number": 1, "path": b"fake"}, TabletState(),
        RecordingDispatcher(), enabled, stop
    )

    assert calls == ["released"]
    assert dev.closed is True
