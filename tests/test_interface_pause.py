"""Interface-thread read cadence plus pause/stop responsiveness (TODO #251).

Covers both read paths and both traffic cases:
  - reports arrive  -> sampled at device rate, no idle wait taken
  - zero traffic    -> bounded wait, thread exits within one poll tick
A fake HID device stands in for hardware; no device, no GUI.
"""

import threading
import time

import pytest

from wtd.config import DriverConfig
from wtd.devices import run_interface
from wtd.gestures import TabletState
from wtd.sender import ActionDispatcher

from conftest import FakeDevice, PlainDevice

TICK = 0.02
PEN_PACKET = [0x02, 0xE0]


def start_thread(interface_type, dev, state, dispatcher, touch_event=None,
                 idle_poll=TICK):
    # TRAY unit (#448): the touch flag is no longer a run_interface
    # argument (thread kill retired); the caller wires the SAME event into
    # the dispatcher's gates and gets it back here to flip it.
    enabled = touch_event if touch_event is not None else threading.Event()
    enabled.set()
    stop = threading.Event()

    thread = threading.Thread(
        target=run_interface,
        args=(
            interface_type,
            {"interface_number": 1, "path": b"fake-path"},
            state,
            dispatcher, stop,
        ),
        # cfg is required since unit B2 (no module-global fallback).
        kwargs={"idle_poll": idle_poll, "cfg": DriverConfig()},
        daemon=True,
    )
    thread.start()

    deadline = time.monotonic() + 1.0
    while dev.read_calls == 0 and time.monotonic() < deadline:
        time.sleep(0.002)

    return thread, enabled, stop


def stop_and_join(thread, stop, timeout=1.0):
    stop.set()
    thread.join(timeout=timeout)
    return not thread.is_alive()


@pytest.fixture
def idle_touch_thread(fake_hid, stub_send_input):
    dev = FakeDevice()
    fake_hid._queue.append(dev)
    state = TabletState()
    dispatcher = ActionDispatcher(state, DriverConfig())
    thread, enabled, stop = start_thread("Touch", dev, state, dispatcher)
    yield dev, thread, enabled, stop
    stop_and_join(thread, stop)


def test_touch_pause_keeps_the_thread_reading(idle_touch_thread):
    # TRAY unit (#448) doctrine re-pin (was: pause EXITS the touch thread):
    # the flag now gates EMISSION only (emission pins in
    # tests/test_tray_touch_toggle.py); the reader thread stays alive and
    # keeps reading so the 0x03 reports routed on this interface and the
    # touch STATE machine stay live.
    dev, thread, enabled, stop = idle_touch_thread

    enabled.clear()
    before = dev.read_calls
    time.sleep(TICK * 4)

    assert thread.is_alive(), "the touch reader must survive a touch stop"
    assert dev.read_calls > before, "reading must continue while touch is off"
    assert dev.closed is False, "a touch stop must not close the device"


def test_touch_pause_does_not_disturb_the_pen_thread(fake_hid,
                                                     stub_send_input):
    # TRAY unit (#448): the pen thread never shares the touch state any
    # more - stopped touch leaves the pen reader alive, reading, and its
    # device open (the pen EMISSION lane is pinned live in
    # tests/test_tray_touch_toggle.py).
    dev = FakeDevice()
    fake_hid._queue.append(dev)
    state = TabletState()
    touch_state = threading.Event()
    touch_state.set()
    dispatcher = ActionDispatcher(state, DriverConfig(),
                                  touch_enabled_event=touch_state)

    thread, enabled, stop = start_thread("Pen", dev, state, dispatcher,
                                         touch_event=touch_state)
    assert enabled is touch_state
    touch_state.clear()
    before = dev.read_calls
    time.sleep(TICK * 4)

    assert thread.is_alive(), "a touch stop must not touch the pen reader"
    assert dev.read_calls > before
    assert dev.closed is False


def test_touch_pause_flag_still_leaves_stop_event_in_charge(idle_touch_thread):
    # TRAY unit (#448) re-pin of the old "pause exits within one tick":
    # the lifecycle lever is stop_event only; after a touch stop the flag
    # has no exit power, but stop_event still lands within the tick budget.
    dev, thread, enabled, stop = idle_touch_thread

    enabled.clear()
    time.sleep(TICK * 2)
    assert thread.is_alive(), "the flag alone must not end the reader now"

    started = time.monotonic()
    assert stop_and_join(thread, stop)
    elapsed = time.monotonic() - started
    assert elapsed < 1.0, f"stop_event took {elapsed:.3f}s to end the reader"


def test_stop_event_stops_thread_promptly(idle_touch_thread):
    dev, thread, _enabled, stop = idle_touch_thread

    stop.set()
    thread.join(timeout=0.5)

    assert not thread.is_alive()
    assert dev.closed is True


def test_device_is_opened_nonblocking(idle_touch_thread):
    dev, thread, _enabled, stop = idle_touch_thread

    assert dev.nonblocking is True


def test_timed_path_exercises_bounded_wait_when_silent(fake_hid, stub_send_input):
    dev = FakeDevice()
    fake_hid._queue.append(dev)
    state = TabletState()
    dispatcher = ActionDispatcher(state, DriverConfig())

    thread, _enabled, stop = start_thread("Touch", dev, state, dispatcher, idle_poll=TICK)
    time.sleep(TICK * 4)
    waits = dev.timeout_waits
    slept = dev.slept_seconds

    assert stop_and_join(thread, stop)
    assert waits >= 2, "silent device should wait on the timed read, not busy-spin"
    assert slept >= waits * TICK * 0.5


def test_reports_are_sampled_at_device_rate_with_timed_read(fake_hid, stub_send_input):
    dev = FakeDevice(reports=[PEN_PACKET] * 20)
    fake_hid._queue.append(dev)
    state = TabletState()
    dispatcher = ActionDispatcher(state, DriverConfig())

    thread, _enabled, stop = start_thread("Pen", dev, state, dispatcher, idle_poll=0.05)

    started = time.monotonic()
    deadline = time.monotonic() + 2.0
    while dev.reports and time.monotonic() < deadline:
        time.sleep(0.002)
    drained = time.monotonic() - started

    assert stop_and_join(thread, stop)
    assert not dev.reports, "thread did not drain the report queue"
    assert drained < 0.1, f"20 reports took {drained:.3f}s while idle_poll was 0.05s"
    # At most one wait: the read issued after the queue drained, never between reports.
    assert dev.timeout_waits <= 1, f"{dev.timeout_waits} idle waits taken while reports arrived"


def test_reports_are_sampled_at_device_rate_with_plain_read(fake_hid, stub_send_input):
    """The Windows hidapi wheel has no read_timeout; the fallback must not throttle."""
    dev = PlainDevice(reports=[PEN_PACKET] * 20)
    fake_hid._queue.append(dev)
    state = TabletState()
    dispatcher = ActionDispatcher(state, DriverConfig())

    thread, _enabled, stop = start_thread("Pen", dev, state, dispatcher, idle_poll=0.05)

    started = time.monotonic()
    deadline = time.monotonic() + 2.0
    while dev.reports and time.monotonic() < deadline:
        time.sleep(0.002)
    drained = time.monotonic() - started

    assert stop_and_join(thread, stop)
    assert not dev.reports, "thread did not drain the report queue"
    assert drained < 0.1, f"20 reports took {drained:.3f}s while idle_poll was 0.05s"


def test_plain_read_device_keeps_reading_while_touch_paused(fake_hid,
                                                            stub_send_input):
    # TRAY unit (#448) doctrine re-pin (was: the non-blocking read lane
    # exits on pause within one tick): the idle backoff paces reads, the
    # touch flag ends nothing; only stop_event still exits the loop.
    dev = PlainDevice()
    fake_hid._queue.append(dev)
    state = TabletState()
    dispatcher = ActionDispatcher(state, DriverConfig())

    thread, enabled, stop = start_thread("Touch", dev, state, dispatcher,
                                         idle_poll=TICK)

    enabled.clear()
    before = dev.read_calls
    time.sleep(TICK * 6)

    assert thread.is_alive()
    assert dev.read_calls > before, "idle pacing must continue while stopped"
    assert stop_and_join(thread, stop)
    assert dev.closed is True


def test_idle_device_paces_reads(fake_hid, stub_send_input):
    dev = FakeDevice()
    fake_hid._queue.append(dev)
    state = TabletState()
    dispatcher = ActionDispatcher(state, DriverConfig())

    thread, _enabled, stop = start_thread("Touch", dev, state, dispatcher, idle_poll=0.05)
    time.sleep(0.2)
    reads_in_window = dev.read_calls

    assert stop_and_join(thread, stop)
    assert reads_in_window <= 8, f"busy read loop: {reads_in_window} reads in 0.2s"


def test_touch_report_is_processed_by_thread(fake_hid, stub_send_input):
    report = [0x02, 1, 3, 0xC0, 0x10, 0x20, 0x30, 0, 0, 0]
    dev = FakeDevice(reports=[report])
    fake_hid._queue.append(dev)
    state = TabletState()
    dispatcher = ActionDispatcher(state, DriverConfig())

    thread, enabled, stop = start_thread("Touch", dev, state, dispatcher)

    deadline = time.monotonic() + 1.0
    while not state.active_contacts and time.monotonic() < deadline:
        time.sleep(0.002)

    stop.set()
    thread.join(timeout=0.5)

    assert state.active_contacts == {3: (259, 512)}
    assert not thread.is_alive()


def test_pen_proximity_is_processed_by_thread(fake_hid, stub_send_input):
    dev = FakeDevice(reports=[PEN_PACKET])
    fake_hid._queue.append(dev)
    state = TabletState()
    dispatcher = ActionDispatcher(state, DriverConfig())

    thread, enabled, stop = start_thread("Pen", dev, state, dispatcher)

    deadline = time.monotonic() + 1.0
    while not state.pen_in_proximity and time.monotonic() < deadline:
        time.sleep(0.002)

    stop.set()
    thread.join(timeout=0.5)

    assert state.pen_in_proximity is True
    assert not thread.is_alive()
