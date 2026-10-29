"""Interface-thread pause/stop responsiveness (TODO #251).

A fake HID device emits nothing, so these tests prove the exit path does not
depend on device traffic. No device, no GUI.
"""

import threading
import time

import pytest

import touch_controller
from touch_controller import ActionDispatcher, TabletState, run_interface

from conftest import FakeDevice

TICK = 0.02


def start_thread(interface_type, dev, state, dispatcher, idle_poll=TICK):
    enabled = threading.Event()
    enabled.set()
    stop = threading.Event()

    thread = threading.Thread(
        target=run_interface,
        args=(
            interface_type,
            {"interface_number": 1, "path": b"fake-path"},
            state,
            dispatcher,
            enabled,
            stop,
        ),
        kwargs={"idle_poll": idle_poll},
        daemon=True,
    )
    thread.start()

    deadline = time.monotonic() + 1.0
    while dev.read_calls == 0 and time.monotonic() < deadline:
        time.sleep(0.005)

    return thread, enabled, stop


@pytest.fixture
def idle_touch_thread(fake_hid, stub_send_input):
    dev = FakeDevice()
    fake_hid._queue.append(dev)
    state = TabletState()
    dispatcher = ActionDispatcher(state)
    thread, enabled, stop = start_thread("Touch", dev, state, dispatcher)
    yield dev, thread, enabled, stop
    stop.set()
    enabled.clear()
    thread.join(timeout=1.0)


def test_pause_stops_touch_thread_with_zero_traffic(idle_touch_thread):
    dev, thread, enabled, stop = idle_touch_thread

    enabled.clear()
    thread.join(timeout=0.5)

    assert not thread.is_alive()
    assert dev.closed is True


def test_pause_stops_pen_thread_with_zero_traffic(fake_hid, stub_send_input):
    dev = FakeDevice()
    fake_hid._queue.append(dev)
    state = TabletState()
    dispatcher = ActionDispatcher(state)

    thread, enabled, stop = start_thread("Pen", dev, state, dispatcher)
    enabled.clear()
    thread.join(timeout=0.5)

    assert not thread.is_alive()
    assert dev.closed is True


def test_pause_takes_effect_within_one_poll_tick(idle_touch_thread):
    dev, thread, enabled, stop = idle_touch_thread

    enabled.clear()
    started = time.monotonic()
    thread.join(timeout=0.5)
    elapsed = time.monotonic() - started

    assert not thread.is_alive()
    assert elapsed < TICK * 5, f"exit took {elapsed:.3f}s, poll tick {TICK}s"


def test_stop_event_stops_thread_promptly(idle_touch_thread):
    dev, thread, enabled, stop = idle_touch_thread

    stop.set()
    thread.join(timeout=0.5)

    assert not thread.is_alive()
    assert dev.closed is True


def test_device_is_opened_nonblocking(idle_touch_thread):
    dev, thread, enabled, stop = idle_touch_thread

    assert dev.nonblocking is True


def test_idle_device_paces_reads(fake_hid, stub_send_input):
    dev = FakeDevice()
    fake_hid._queue.append(dev)
    state = TabletState()
    dispatcher = ActionDispatcher(state)

    thread, enabled, stop = start_thread("Touch", dev, state, dispatcher, idle_poll=0.05)
    time.sleep(0.2)
    reads_in_window = dev.read_calls

    enabled.clear()
    thread.join(timeout=1.0)

    assert not thread.is_alive()
    assert reads_in_window <= 8, f"busy read loop: {reads_in_window} reads in 0.2s"


def test_touch_report_is_processed_by_thread(fake_hid, stub_send_input):
    report = [0x02, 1, 3, 0xC0, 0x10, 0x20, 0x30, 0, 0, 0]
    dev = FakeDevice(reports=[report])
    fake_hid._queue.append(dev)
    state = TabletState()
    dispatcher = ActionDispatcher(state)

    thread, enabled, stop = start_thread("Touch", dev, state, dispatcher)

    deadline = time.monotonic() + 1.0
    while not state.active_contacts and time.monotonic() < deadline:
        time.sleep(0.005)

    enabled.clear()
    thread.join(timeout=0.5)

    assert state.active_contacts == {3: (259, 512)}
    assert not thread.is_alive()


def test_pen_proximity_is_processed_by_thread(fake_hid, stub_send_input):
    dev = FakeDevice(reports=[[0x02, 0xE0]])
    fake_hid._queue.append(dev)
    state = TabletState()
    dispatcher = ActionDispatcher(state)

    thread, enabled, stop = start_thread("Pen", dev, state, dispatcher)

    deadline = time.monotonic() + 1.0
    while not state.pen_in_proximity and time.monotonic() < deadline:
        time.sleep(0.005)

    enabled.clear()
    thread.join(timeout=0.5)

    assert state.pen_in_proximity is True
    assert not thread.is_alive()
