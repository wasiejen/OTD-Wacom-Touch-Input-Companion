"""Interface-thread read cadence plus pause/stop responsiveness (TODO #251).

Covers both read paths and both traffic cases:
  - reports arrive  -> sampled at device rate, no idle wait taken
  - zero traffic    -> bounded wait, thread exits within one poll tick
A fake HID device stands in for hardware; no device, no GUI.
"""

import threading
import time

import pytest

import touch_controller
from touch_controller import ActionDispatcher, TabletState, run_interface

from conftest import FakeDevice, PlainDevice

TICK = 0.02
PEN_PACKET = [0x02, 0xE0]


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
        time.sleep(0.002)

    return thread, enabled, stop


def stop_and_join(thread, enabled, stop, timeout=1.0):
    stop.set()
    enabled.clear()
    thread.join(timeout=timeout)
    return not thread.is_alive()


@pytest.fixture
def idle_touch_thread(fake_hid, stub_send_input):
    dev = FakeDevice()
    fake_hid._queue.append(dev)
    state = TabletState()
    dispatcher = ActionDispatcher(state)
    thread, enabled, stop = start_thread("Touch", dev, state, dispatcher)
    yield dev, thread, enabled, stop
    stop_and_join(thread, enabled, stop)


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


def test_timed_path_exercises_bounded_wait_when_silent(fake_hid, stub_send_input):
    dev = FakeDevice()
    fake_hid._queue.append(dev)
    state = TabletState()
    dispatcher = ActionDispatcher(state)

    thread, enabled, stop = start_thread("Touch", dev, state, dispatcher, idle_poll=TICK)
    time.sleep(TICK * 4)
    waits = dev.timeout_waits
    slept = dev.slept_seconds

    assert stop_and_join(thread, enabled, stop)
    assert waits >= 2, "silent device should wait on the timed read, not busy-spin"
    assert slept >= waits * TICK * 0.5


def test_reports_are_sampled_at_device_rate_with_timed_read(fake_hid, stub_send_input):
    dev = FakeDevice(reports=[PEN_PACKET] * 20)
    fake_hid._queue.append(dev)
    state = TabletState()
    dispatcher = ActionDispatcher(state)

    thread, enabled, stop = start_thread("Pen", dev, state, dispatcher, idle_poll=0.05)

    started = time.monotonic()
    deadline = time.monotonic() + 2.0
    while dev.reports and time.monotonic() < deadline:
        time.sleep(0.002)
    drained = time.monotonic() - started

    assert stop_and_join(thread, enabled, stop)
    assert not dev.reports, "thread did not drain the report queue"
    assert drained < 0.1, f"20 reports took {drained:.3f}s while idle_poll was 0.05s"
    # At most one wait: the read issued after the queue drained, never between reports.
    assert dev.timeout_waits <= 1, f"{dev.timeout_waits} idle waits taken while reports arrived"


def test_reports_are_sampled_at_device_rate_with_plain_read(fake_hid, stub_send_input):
    """The Windows hidapi wheel has no read_timeout; the fallback must not throttle."""
    dev = PlainDevice(reports=[PEN_PACKET] * 20)
    fake_hid._queue.append(dev)
    state = TabletState()
    dispatcher = ActionDispatcher(state)

    thread, enabled, stop = start_thread("Pen", dev, state, dispatcher, idle_poll=0.05)

    started = time.monotonic()
    deadline = time.monotonic() + 2.0
    while dev.reports and time.monotonic() < deadline:
        time.sleep(0.002)
    drained = time.monotonic() - started

    assert stop_and_join(thread, enabled, stop)
    assert not dev.reports, "thread did not drain the report queue"
    assert drained < 0.1, f"20 reports took {drained:.3f}s while idle_poll was 0.05s"


def test_plain_read_device_exits_on_pause_within_one_tick(fake_hid, stub_send_input):
    dev = PlainDevice()
    fake_hid._queue.append(dev)
    state = TabletState()
    dispatcher = ActionDispatcher(state)

    thread, enabled, stop = start_thread("Touch", dev, state, dispatcher, idle_poll=TICK)

    enabled.clear()
    started = time.monotonic()
    thread.join(timeout=0.5)
    elapsed = time.monotonic() - started

    assert not thread.is_alive()
    assert elapsed < TICK * 5, f"exit took {elapsed:.3f}s, poll tick {TICK}s"
    assert dev.closed is True


def test_idle_device_paces_reads(fake_hid, stub_send_input):
    dev = FakeDevice()
    fake_hid._queue.append(dev)
    state = TabletState()
    dispatcher = ActionDispatcher(state)

    thread, enabled, stop = start_thread("Touch", dev, state, dispatcher, idle_poll=0.05)
    time.sleep(0.2)
    reads_in_window = dev.read_calls

    assert stop_and_join(thread, enabled, stop)
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
        time.sleep(0.002)

    enabled.clear()
    thread.join(timeout=0.5)

    assert state.active_contacts == {3: (259, 512)}
    assert not thread.is_alive()


def test_pen_proximity_is_processed_by_thread(fake_hid, stub_send_input):
    dev = FakeDevice(reports=[PEN_PACKET])
    fake_hid._queue.append(dev)
    state = TabletState()
    dispatcher = ActionDispatcher(state)

    thread, enabled, stop = start_thread("Pen", dev, state, dispatcher)

    deadline = time.monotonic() + 1.0
    while not state.pen_in_proximity and time.monotonic() < deadline:
        time.sleep(0.002)

    enabled.clear()
    thread.join(timeout=0.5)

    assert state.pen_in_proximity is True
    assert not thread.is_alive()
