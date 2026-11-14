"""Lifecycle pins for the Phase A churn lanes (estate A1: pins only, no fixes).

Scope: proposals/2026-11-14_wtd-architecture-review.md Items 1/2/7/10, as
scheduled by proposals/2026-11-15_wtd-improvement-plan.md Phase A. The
device-free suite covered every feature lane but none of the four lifetime
lanes the review found, so this module pins them BEFORE the fixes land: every
fix unit below lands on a pin that already states its requirement.

Lanes:
  P1 shutdown release (Item 1, unit A2): a reader holding a modifier or the
     left button must run release_held_inputs within a bounded join window
     once stop_event is set, and main()'s own shutdown path must leave no
     reader alive behind it.
  P2 Apply-Live durability (Item 2, unit A3): a live-applied value must
     survive pause -> reconnect, or the loss stays pinned red.
  P3 concurrent reader exits (Item 7, unit A4): two readers sharing one
     dispatcher must not release the same modifier twice.
  P4 multi-device primary bind (Item 10): get_primary_connection picks one
     deterministic entry while two devices are connected.

Harness rules this module keeps: no device, no GUI, no live injection (every
injected event is counted through the conftest stub_send_input stub, the
opt-in side of the session injection guard), and no fixed sleeps. The lanes
synchronize on threading events and on the fake devices' own lifecycle
signals, so a slow machine waits longer but never reads state that has not
happened yet. Two bounded waits are exempt, and both are device-side rather
than test-side: main()'s own 1.0 s startup readout (touch_controller.py:3626),
which the driver cannot skip, and READER_DWELL_SECONDS inside the fake devices
(conftest's FakeDevice.read_timeout models the same dwell) - the shutdown
lanes' margin has to be owned by the device, not by machine speed, so removing
that dwell would silently kill the red-without-join margin.

No lane pins an unmet requirement any more: the Phase A fix units (A2 shutdown
join, A3 Apply-Live write-through, A4 held-input lock) all landed, so every lane
asserts its requirement directly. The convention stays for the next one - a pin
that cannot pass yet calls pytest.xfail with a reason naming the unit that must
flip it, and the reason carries the measurement. No lane pins a defect as
the desired outcome: the gate states the requirement once, and an xfail branch
only records that the current build does not meet it. The gate is
deliberately imperative instead of a strict marker: a blanket marker would
also swallow a broken churn harness or a release lane that stopped injecting,
which are different bugs the reader must see as failures.
"""

import sys
import threading
import time
import types

import pytest

import touch_controller
from touch_controller import (
    ActionDispatcher,
    DeviceManagerThread,
    DriverConfig,
    TabletState,
    run_interface,
)
import gui
from vk_codes import vk_codes_dict

from conftest import PlainDevice, SignalDevice

JOIN_WINDOW_SECONDS = 1.0
EVENT_WAIT_SECONDS = 2.0
# The seam window is the release race's own budget, deliberately SHORTER than
# the join budget above: under a fixed product one release call arrives at the
# seam alone and must wake, inject, and finish long before the joins time out.
SEAM_WAIT_SECONDS = 0.5
# Device-side dwell: how long a fake reader stays inside read() AFTER the
# product set stop_event - a real read hands back its next report or times out
# rather than waking on the flag (conftest's FakeDevice.read_timeout models the
# same dwell with a bounded sleep). The shutdown lanes' margin IS this dwell,
# so it does not depend on machine speed: a shutdown that only sets the flag
# returns inside it (readers still in flight), a shutdown that joins waits it
# out. Kept well below the product's SHUTDOWN_JOIN_BUDGET_SECONDS.
READER_DWELL_SECONDS = 0.3
# Upper bound for main() returning: its own 1.0 s startup readout plus one full
# join budget, with slack for a slow machine.
MAIN_RETURN_LIMIT_SECONDS = 4.0
ATTACH_WAIT_SECONDS = 2.0
TICK = 0.02

BASE_ACTION = "volume_up"
APPLIED_ACTION = "volume_down"


class SlowReaderDevice(PlainDevice):
    """Plain device whose reader leaves read() only a dwell after stop_event.

    read() blocks until the test attaches the product's stop_event (the
    shutdown lane hands it over from the stub GUI launch, which happens before
    main()'s finally sets it), then dwells READER_DWELL_SECONDS longer before
    handing back an empty report. A shutdown path that only sets the flag
    therefore returns while these readers are still in flight; a shutdown path
    that joins the recorded handles finds them gone. Every parked handle is
    recorded so the lane can name its own threads.
    """

    def __init__(self):
        super().__init__()
        self.first_read = threading.Event()
        self.reader_threads = []
        self._attached = threading.Event()
        self._stop_event = None

    def attach_stop_event(self, stop_event):
        self._stop_event = stop_event
        self._attached.set()

    def read(self, max_size=64):
        self.reader_threads.append(threading.current_thread())
        self.first_read.set()
        if self._attached.wait(ATTACH_WAIT_SECONDS) and self._stop_event is not None:
            self._stop_event.wait(ATTACH_WAIT_SECONDS)
            # A read woken by the flag still owes the rest of its dwell.
            # Nothing ever sets this event, so the wait is exactly the dwell.
            threading.Event().wait(READER_DWELL_SECONDS)
        return []


class WedgedDevice(PlainDevice):
    """Plain device whose read() never returns until the test lets it go.

    Models the wedged HID read Item 1 names: the reader cannot reach its own
    release lane at all, so only the shutdown path's last-resort dispatcher
    sweep can put a held input down. The lane always releases the wedge in its
    finally, and the product is never asked to - that is the point.
    """

    def __init__(self):
        super().__init__()
        self.first_read = threading.Event()
        self.reader_threads = []
        self.wedge = threading.Event()

    def read(self, max_size=64):
        self.reader_threads.append(threading.current_thread())
        self.first_read.set()
        self.wedge.wait()
        return []


def key_ups(events, vk_code):
    return [e for e in events
            if e[0] == "key" and e[1] == vk_code
            and e[2] == ActionDispatcher.KEYEVENTF_KEYUP]


def mouse_events(events, dw_flags):
    return [e for e in events if e[0] == "mouse" and e[1] == dw_flags]


def start_reader(fake_hid, interface_type, state, dispatcher, enabled, stop):
    """Spawn one daemon reader over its own signal device, as the manager does.

    Returns only after the reader entered its read loop (device signal, not a
    sleep), so the caller's stop/pause always lands on a running reader.
    """
    dev = SignalDevice()
    fake_hid._queue.append(dev)
    thread = threading.Thread(
        target=run_interface,
        args=(interface_type, {"interface_number": 1, "path": b"fake-path"},
              state, dispatcher, enabled, stop),
        # cfg is required since unit B2 (no module-global fallback): a harness
        # reader gets its own defaults config.
        kwargs={"idle_poll": TICK, "cfg": DriverConfig()},
        daemon=True,
    )
    thread.start()
    assert dev.first_read.wait(EVENT_WAIT_SECONDS), "reader never entered its read loop"
    return thread, dev


def join_readers(*threads):
    for thread in threads:
        thread.join(EVENT_WAIT_SECONDS)


def live_threads(threads):
    return [t for t in threads if t.is_alive()]


def wired_pair(product_id, parent_slug):
    """One wired pen+touch interface pair with its own parent instance path."""
    vendor = DeviceManagerThread.VENDOR_ID
    return [
        {"vendor_id": vendor, "product_id": product_id,
         "product_string": "Wacom Intuos5 touch M", "interface_number": 0,
         "path": ("HID#%s&MI_00" % parent_slug).encode()},
        {"vendor_id": vendor, "product_id": product_id,
         "product_string": "Wacom Intuos5 touch M", "interface_number": 1,
         "path": ("HID#%s&MI_01" % parent_slug).encode()},
    ]


# ==============================================================================
# P1 - shutdown release while a reader holds input (review Item 1 / unit A2)
# ==============================================================================

def test_shutdown_stop_event_releases_held_modifier_in_join_window(fake_hid,
                                                                   stub_send_input):
    """Item 1 / A2: a quit with Ctrl held must still release the modifier.

    The dispatcher holds Ctrl when the app quits. main()'s finally sets
    stop_event and returns without joining (touch_controller.py:3662-3666) and
    every reader is a daemon thread (:3247-3258), so in the live build the
    reader's finally (:3041-3050) only runs if that thread happens to notice
    the flag before the interpreter tears the process down - the race Item 1
    names. What this pin asserts is the narrower property: once the reader is
    joined, the release ran and the Ctrl-up landed inside one bounded join
    window. The test is the joiner - main() does not join yet, and the lane
    below pins that missing joiner.
    """
    state = TabletState()
    dispatcher = ActionDispatcher(state)
    dispatcher.press_key("ctrl")
    stub_send_input.clear()

    enabled = threading.Event()
    enabled.set()
    stop = threading.Event()
    thread, dev = start_reader(fake_hid, "Touch", state, dispatcher, enabled, stop)

    stop.set()
    thread.join(JOIN_WINDOW_SECONDS)

    assert not thread.is_alive(), "reader outlived the join window after stop_event"
    assert len(key_ups(stub_send_input, vk_codes_dict["ctrl"])) == 1, (
        "Ctrl-up did not land exactly once on stop_event exit: %s" % stub_send_input)
    assert dev.closed_event.wait(EVENT_WAIT_SECONDS), "reader never closed its device"
    assert dev.closed is True


def test_shutdown_stop_event_releases_left_button_in_join_window(fake_hid,
                                                                 stub_send_input):
    """Item 1 / A2: the same shutdown window with the left button held.

    Same shape as the modifier lane, second held class: a drag in progress at
    quit must end with exactly one LEFTUP, or the button stays pressed
    system-wide.
    """
    state = TabletState()
    dispatcher = ActionDispatcher(state)
    dispatcher.handle_left_hold()
    stub_send_input.clear()

    enabled = threading.Event()
    enabled.set()
    stop = threading.Event()
    thread, dev = start_reader(fake_hid, "Touch", state, dispatcher, enabled, stop)

    stop.set()
    thread.join(JOIN_WINDOW_SECONDS)

    assert not thread.is_alive(), "reader outlived the join window after stop_event"
    assert len(mouse_events(stub_send_input,
                            ActionDispatcher.MOUSEEVENTF_LEFTUP)) == 1, (
        "LEFTUP did not land exactly once on stop_event exit: %s" % stub_send_input)
    assert state.is_left_held is False
    assert dev.closed_event.wait(EVENT_WAIT_SECONDS), "reader never closed its device"
    assert dev.closed is True


def drive_main(fake_hid, monkeypatch, tmp_path, devices, on_launch=None):
    """Run touch_controller.main() device-free over the given fake devices.

    argv, the module config and the gui import are patched; the stub GUI closes
    as soon as it is launched. At that moment every device is handed the
    product's own stop_event (before main()'s finally sets it) and on_launch
    runs, so a lane can put the runtime mid-gesture exactly while the GUI is
    open. Returns the seconds main() spent, so a lane can assert a bounded
    shutdown without sleeping. The 1.0 s main() costs on its own is its startup
    readout (touch_controller.py:3626).
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["touch_controller.py"])
    monkeypatch.setattr(touch_controller, "config", DriverConfig())
    fake_hid.enumerate = lambda: wired_pair(789, "VID_056A&PID_0315")
    fake_hid._queue.extend(devices)

    gui_stub = types.ModuleType("gui")

    def stub_launch_gui(state, gui_config, touch_enabled_event, stop_event,
                        device_info=None):
        for dev in devices:
            attach = getattr(dev, "attach_stop_event", None)
            if attach is not None:
                attach(stop_event)
        if on_launch is not None:
            on_launch(state, device_info, stop_event)
        return None

    gui_stub.launch_gui = stub_launch_gui
    monkeypatch.setitem(sys.modules, "gui", gui_stub)

    started = time.monotonic()
    touch_controller.main()
    return time.monotonic() - started


def test_shutdown_path_leaves_no_reader_alive_behind_it(fake_hid, stub_send_input,
                                                        monkeypatch, tmp_path):
    """Item 1 / A2: main()'s own shutdown path waits for the readers it recorded.

    The two pins above show the release lands when somebody joins; this lane
    pins the joiner, and A2 owns it: main()'s finally sets stop_event and then
    shutdown_readers() joins the handles DeviceManagerThread.live_reader_threads()
    reports, inside one bounded budget.

    SlowReaderDevice keeps the margin honest: its reader leaves read() only
    READER_DWELL_SECONDS after the flag goes up, so a shutdown that only sets
    the flag returns while the readers are still in flight, while a shutdown
    that joins them finds them gone. The lane measures the outcome, not the
    mechanism: any route that returns with zero live readers passes, and
    returning while readers are still running is what fails here.
    """
    slow = [SlowReaderDevice(), SlowReaderDevice()]

    readers = []
    try:
        elapsed = drive_main(fake_hid, monkeypatch, tmp_path, slow)
        for dev in slow:
            assert dev.first_read.wait(EVENT_WAIT_SECONDS), (
                "main() never drove a reader into its read loop")
        readers = [t for dev in slow for t in dev.reader_threads]
        assert readers, "main() spawned no reader thread"

        behind = live_threads(readers)
        assert not behind, (
            "main()'s shutdown path left "
            f"{len(behind)} reader(s) alive - the finally has to join the "
            "reader handles the manager recorded "
            "(DeviceManagerThread.live_reader_threads) before returning, or "
            "the daemon threads' release lane stays a race at process exit "
            "(Item 1 / A2).")
        assert elapsed < MAIN_RETURN_LIMIT_SECONDS, (
            f"main() took {elapsed:.2f}s - the shutdown join is not bounded")
    finally:
        join_readers(*readers)

    # Reached only when the body did not fail: by now the readers ran their
    # finallys, and this lane holds nothing they could release, so a single
    # injected event would mean a new leak lane.
    assert stub_send_input == [], (
        "the shutdown path injected on teardown: %s" % stub_send_input)


def test_shutdown_sweeps_a_wedged_readers_held_input(fake_hid, stub_send_input,
                                                     monkeypatch, tmp_path):
    """Item 1 / A2: a reader that never returns must still have its input released.

    The last half of owned shutdown: WedgedDevice models the wedged HID read,
    so neither reader can reach its own release lane. The lane puts the live
    dispatcher mid-gesture (Ctrl held) while the stub GUI is open, lets main()
    shut down, and requires that the shutdown path force-releases through the
    connection's dispatcher (shutdown_readers -> release_held_inputs) and still
    returns inside its budget. Without the sweep nothing releases: the Ctrl-up
    only appears after this test lets the wedge go, which is the stuck-key
    class Item 1 reports.
    """
    wedged = [WedgedDevice(), WedgedDevice()]
    held = {}

    def mid_gesture(state, device_info, stop_event):
        dispatcher = device_info["connection"]["action"]
        held["dispatcher"] = dispatcher
        dispatcher.press_key("ctrl")
        # Keep only what the shutdown itself releases.
        stub_send_input.clear()

    readers = []
    try:
        elapsed = drive_main(fake_hid, monkeypatch, tmp_path, wedged,
                             on_launch=mid_gesture)
        for dev in wedged:
            assert dev.first_read.wait(EVENT_WAIT_SECONDS), (
                "main() never drove a reader into its read loop")
        readers = [t for dev in wedged for t in dev.reader_threads]
        assert len(readers) == 2, "both readers of the pair should be wedged"

        assert elapsed < MAIN_RETURN_LIMIT_SECONDS, (
            f"main() took {elapsed:.2f}s - a wedged read hung the shutdown "
            "instead of costing at most the join budget")
        assert all(t.is_alive() for t in readers), (
            "the wedged readers exited on their own: this run did not test "
            "the last-resort sweep at all")

        assert len(key_ups(stub_send_input, vk_codes_dict["ctrl"])) == 1, (
            "the shutdown sweep did not force-release the Ctrl a wedged "
            f"reader was holding: {stub_send_input}")
        assert held["dispatcher"].held_keys == set(), (
            "the sweep left the dispatcher claiming a held key")
    finally:
        for dev in wedged:
            dev.wedge.set()
        join_readers(*readers)

    assert len(key_ups(stub_send_input, vk_codes_dict["ctrl"])) == 1, (
        "the released Ctrl-up landed twice (sweep plus the reader's own "
        f"finally): {stub_send_input}")


# ==============================================================================
# P2 - Apply-Live durability across pause -> reconnect (Item 2 / unit A3)
# ==============================================================================

class _FakeVar:
    """Tk variable stand-in for the apply lane."""

    def __init__(self, value=True):
        self._value = value

    def get(self):
        return self._value


class _FakeEntry:
    """Tk entry/combo stand-in for the apply lane."""

    def __init__(self, value):
        self._value = value

    def get(self):
        return self._value


class _FakeMessageBox:
    """Collects the dialogs apply_settings would show instead of showing them."""

    def __init__(self):
        self.errors = []

    def showerror(self, title, message):
        self.errors.append((title, message))


class ApplyWindow:
    """Duck-typed SettingsWindow running the REAL apply_settings body.

    A3's contract lives in the GUI apply call, so these lanes drive that method
    itself rather than a stand-in: the fake carries only what apply_settings
    reads (entries, the accel/feature vars, the action combos, saved actions,
    device_info) and binds the real apply_settings plus the real write-through
    helper, the same pattern the FakeWindow pins use in
    tests/test_action_mapping.py.
    """

    def __init__(self, config, device_info, combos):
        self.config = config
        self.device_info = device_info
        self.entries = {}
        self.cursor_accel_var = _FakeVar(True)
        self.scroll_accel_var = _FakeVar(True)
        self.feature_vars = {}
        self.action_combos = {key: _FakeEntry(value)
                              for key, value in combos.items()}
        self.saved_actions = {}
        self.apply_settings = types.MethodType(gui.SettingsWindow.apply_settings, self)
        self._write_through_focused_entry = types.MethodType(
            gui.SettingsWindow._write_through_focused_entry, self)


def live_base_config(action_values=None):
    """A base config with the caller's gesture values applied up front."""
    base = DriverConfig()
    for key, value in (action_values or {}).items():
        base.action_mapping[key] = value
    return base


def grant_live_vocabulary(base, conn):
    """Give the base config the action vocabulary main() gives it.

    main() reads the list off the live dispatcher and stores it on the base
    config (touch_controller.py:3703-3716). A stored identity entry is validated
    against that list when a connection reforks, so a churn lane that skipped
    this step would watch a legitimate Apply rejected as an unknown action
    instead of testing the durability it names.
    """
    base.set_available_actions(list(conn["action"].ACTION_DISPATCH.keys()))


def start_manager(enabled, stop, base):
    """A manager over the queued fake devices, already past its first pass."""
    manager = DeviceManagerThread(enabled, stop, poll_interval=0.1, base_config=base)
    manager.manage_connections()
    return manager


def churn(manager, enabled, all_threads):
    """Pause, wait the readers out, resume, run one reconnect pass.

    Returns the respawned connections. This is the churn Item 2 reports on: the
    pause drops the enable event, both readers break out, and the next pass
    deletes the dead entry and forks a fresh config for the identity.
    """
    enabled.clear()
    join_readers(*all_threads)
    stuck = live_threads(all_threads)
    assert not stuck, (
        f"{len(stuck)} reader(s) outlived the pause: the manager keeps a live "
        "connection in that case, so this lane would certify survival without "
        "ever reforking anything")
    enabled.set()
    manager.manage_connections()
    return manager.list_connections()


def test_apply_live_survives_pause_reconnect(fake_hid, monkeypatch, tmp_path):
    """Item 2 / A3: a live-applied value outlives its connection.

    "Apply Live" runs the real gui.SettingsWindow.apply_settings against the
    connection's own config fork and, since A3, writes the applied sections
    through to that device's identity entry in base_config. The lane then pauses,
    resumes and lets the manager respawn the connection with a fresh fork: the
    applied value must come back on the respawned connection's config, because
    that fork is what every reader receives (the reader side of the same contract
    is owned by test_manager_passes_fork_to_every_reader_spawn in
    tests/test_apply_live_propagation.py).

    Before A3 the respawn reforked straight from base_config, so the reader went
    back to the pre-Apply action mid-session and nothing said so - the data-loss
    lane Item 2 reports. Until A3 landed, this lane pinned that loss as an
    expected failure; it now fails red if the write-through regresses.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(gui, "messagebox", _FakeMessageBox())
    monkeypatch.setattr(gui, "ToastNotification", lambda *args, **kwargs: None)
    base = live_base_config({"btn1_press": BASE_ACTION})
    fake_hid.enumerate = lambda: wired_pair(789, "VID_056A&PID_0315")
    fake_hid._queue.extend(PlainDevice() for _ in range(4))

    enabled = threading.Event()
    enabled.set()
    stop = threading.Event()
    all_threads = []
    try:
        manager = start_manager(enabled, stop, base)
        conn = manager.get_primary_connection()
        assert conn is not None, "the fake pair produced no connection"
        all_threads.extend(conn["threads"].values())
        assert conn["config"] is not base
        grant_live_vocabulary(base, conn)

        win = ApplyWindow(conn["config"],
                          {"connection": conn, "base_config": base},
                          {"btn1_press": APPLIED_ACTION})
        assert win.apply_settings() is True, "the apply lane rejected its own values"
        assert conn["config"].action_mapping["btn1_press"] == APPLIED_ACTION

        respawned = churn(manager, enabled, all_threads)
        assert respawned, "no connection after the respawn"
        fresh = next(c for c in respawned if c["product_id"] == 789)
        assert fresh["config"] is not conn["config"], (
            "the respawn handed back the very same config object: nothing "
            "reforked, so a surviving value here would prove nothing")
        all_threads = list(fresh["threads"].values())

        survived = fresh["config"].action_mapping["btn1_press"]
        assert survived == APPLIED_ACTION, (
            f"Apply Live was lost across the churn: the respawn reads '{survived}' "
            f"although the user applied '{APPLIED_ACTION}' through "
            "gui.SettingsWindow.apply_settings - the apply call has to write the "
            "applied sections through to the focused device's entry so the refork "
            "inherits them (Item 2 / A3).")
    finally:
        stop.set()
        join_readers(*all_threads)


def test_apply_live_does_not_leak_between_devices(fake_hid, monkeypatch, tmp_path):
    """Item 2 / A3, divergence leg: an Apply reaches one device, never the other.

    Two wired pairs share one base config. Apply on A, then on B, then churn
    both: A's respawn must read A's value and B's respawn B's. The entry the
    apply call writes is keyed by the connection's identity, which is what keeps
    the devices apart - so the entries themselves are asserted too, in both
    directions, and a leak or a clobber shows up whether or not the reader sees
    it.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(gui, "messagebox", _FakeMessageBox())
    monkeypatch.setattr(gui, "ToastNotification", lambda *args, **kwargs: None)
    base = live_base_config({"btn1_press": BASE_ACTION})
    fake_hid.enumerate = lambda: (wired_pair(789, "VID_056A&PID_0315")
                                  + wired_pair(788, "VID_056A&PID_030C"))
    fake_hid._queue.extend(PlainDevice() for _ in range(4))
    third = "volume_mute"

    enabled = threading.Event()
    enabled.set()
    stop = threading.Event()
    all_threads = []
    try:
        manager = start_manager(enabled, stop, base)
        conns = manager.list_connections()
        assert len(conns) == 2, f"expected two connections, got {len(conns)}"
        all_threads.extend(t for c in conns for t in c["threads"].values())
        a = next(c for c in conns if c["product_id"] == 789)
        b = next(c for c in conns if c["product_id"] == 788)
        grant_live_vocabulary(base, a)

        ApplyWindow(a["config"], {"connection": a, "base_config": base},
                    {"btn1_press": APPLIED_ACTION}).apply_settings()
        assert a["config"].action_mapping["btn1_press"] == APPLIED_ACTION
        assert b["config"].action_mapping["btn1_press"] == BASE_ACTION, (
            "device A's Apply live-mutated device B's config")
        assert (base.device_settings.get(b["identity"], {})
                .get("action_mapping", {})
                .get("btn1_press", BASE_ACTION) == BASE_ACTION), (
            "device A's Apply wrote into device B's entry")

        ApplyWindow(b["config"], {"connection": b, "base_config": base},
                    {"btn1_press": third}).apply_settings()
        assert (base.device_settings[a["identity"]]["action_mapping"]["btn1_press"]
                == APPLIED_ACTION), "device B's Apply clobbered device A's entry"
        assert (base.device_settings[b["identity"]]["action_mapping"]["btn1_press"]
                == third)

        respawned = churn(manager, enabled, all_threads)
        assert len(respawned) == 2, "both devices should have respawned"
        all_threads = [t for c in respawned for t in c["threads"].values()]
        ra = next(c for c in respawned if c["product_id"] == 789)
        rb = next(c for c in respawned if c["product_id"] == 788)
        assert ra["config"].action_mapping["btn1_press"] == APPLIED_ACTION, (
            "device A lost its own Apply across the churn")
        assert rb["config"].action_mapping["btn1_press"] == third, (
            "device B did not inherit its own Apply, or picked up device A's")
    finally:
        stop.set()
        join_readers(*all_threads)


# ==============================================================================
# P3 - concurrent reader exits on one dispatcher (Item 7 / unit A4)
# ==============================================================================

def test_two_sequential_reader_exits_release_the_modifier_once(fake_hid,
                                                               stub_send_input):
    """Item 7 / A4, serialized leg: the second exit releases nothing again.

    Both readers share one dispatcher, exactly as the Pen and Touch threads of
    one connection do. With the exits serialized, the second
    release_held_inputs pass finds held_keys empty and must inject no second
    Ctrl-up. This leg pins idempotence across serialized exits; the next leg
    pins the once-only claim when the two exits overlap.
    """
    state = TabletState()
    dispatcher = ActionDispatcher(state)
    dispatcher.press_key("ctrl")
    stub_send_input.clear()

    shared_stop = threading.Event()
    first_enabled = threading.Event()
    first_enabled.set()
    second_enabled = threading.Event()
    second_enabled.set()
    thread1, dev1 = start_reader(fake_hid, "Touch", state, dispatcher,
                                 first_enabled, shared_stop)
    thread2, dev2 = start_reader(fake_hid, "Pen", state, dispatcher,
                                 second_enabled, shared_stop)

    first_enabled.clear()   # reader 1 exits alone (the pause shape)
    thread1.join(EVENT_WAIT_SECONDS)
    assert not thread1.is_alive(), "first reader survived the pause"
    assert len(key_ups(stub_send_input, vk_codes_dict["ctrl"])) == 1, (
        "first reader exit did not release Ctrl exactly once: %s" % stub_send_input)

    shared_stop.set()       # reader 2 exits afterwards
    thread2.join(EVENT_WAIT_SECONDS)
    assert not thread2.is_alive(), "second reader survived stop_event"

    assert len(key_ups(stub_send_input, vk_codes_dict["ctrl"])) == 1, (
        "second reader exit double-released the modifier: %s" % stub_send_input)
    assert dispatcher.held_keys == set()
    assert dev1.closed is True and dev2.closed is True, (
        "a reader exited without closing its device")


def test_concurrent_reader_exits_release_the_modifier_once(fake_hid,
                                                           stub_send_input,
                                                           monkeypatch):
    """Item 7 / A4, overlapping leg: two exits in one release window.

    Before A4, release_held_inputs walked the live held_keys set while
    release_key injected the key-up BEFORE it discarded, so two exits standing
    in that window both released the same modifier (Item 7). The barrier below
    does not rewrite the
    algorithm - it only widens the window the product already has: both
    threads stand inside send_key(release=True), i.e. both have snapshotted the
    held Ctrl and neither has discarded, before either injects. The two exits
    release each other, so no sleep is needed and the interleaving is not left
    to luck.

    Since A4 the registry is claimed under one lock per dispatcher, so only the
    caller that drained the held modifier injects its key-up and exactly one
    Ctrl-up lands. The seam window (SEAM_WAIT_SECONDS) is the release race's own
    budget and stays shorter than the join budget, so the lone arrival wakes,
    injects and finishes inside the joins. The seam arrival count is a harness
    sanity check, not the proof: post-A4 the loser never reaches the seam, so a
    serialized run reports the same counts - the once-only guarantee itself is
    what the ups == 1 assert pins.
    """
    state = TabletState()
    dispatcher = ActionDispatcher(state)
    dispatcher.press_key("ctrl")
    stub_send_input.clear()

    inside_release = threading.Barrier(2, timeout=SEAM_WAIT_SECONDS)
    release_calls = []
    original_send_key = ActionDispatcher.send_key

    def widened_send_key(self, vk_code, release=False, *args, **kwargs):
        # Scoped to this test's dispatcher: a class-wide seam would let any
        # other releasing dispatcher join the barrier and skew the count.
        if release and self is dispatcher:
            release_calls.append(vk_code)
            try:
                inside_release.wait()
            except threading.BrokenBarrierError:
                # Only one arrival means the release side already turned the
                # second exit into a no-op (the A4 shape). Inject anyway: the
                # counts below are the measurement.
                pass
        return original_send_key(self, vk_code, release, *args, **kwargs)

    monkeypatch.setattr(ActionDispatcher, "send_key", widened_send_key)

    enabled = threading.Event()
    enabled.set()
    stop = threading.Event()
    thread1, dev1 = start_reader(fake_hid, "Touch", state, dispatcher, enabled, stop)
    thread2, dev2 = start_reader(fake_hid, "Pen", state, dispatcher, enabled, stop)

    stop.set()      # both finallys run against each other
    join_readers(thread1, thread2)
    assert not thread1.is_alive() and not thread2.is_alive(), (
        "a reader outlived the join window: the count below is not a release "
        "measurement")

    ups = len(key_ups(stub_send_input, vk_codes_dict["ctrl"]))
    assert ups >= 1, (
        "no Ctrl-up landed at all - the release lane itself broke: %s"
        % stub_send_input)
    assert ups == 1, (
        f"two concurrent reader exits released the held modifier {ups} times - "
        "the held-input registry has to be claimed under one lock per "
        "dispatcher, so only the caller that drained a held input injects its "
        "key-up (Item 7 / A4).")
    assert len(release_calls) == 1, (
        f"the seam saw {len(release_calls)} release calls while {ups} Ctrl-up "
        "landed: a reader was starved past the seam window, so the single "
        "count is not a release measurement")
    # Green condition: exactly one Ctrl-up, one seam arrival, empty held set.
    assert dispatcher.held_keys == set()
    assert dev1.closed is True and dev2.closed is True, (
        "a reader exited without closing its device")


# ==============================================================================
# P4 - multi-device primary bind determinism (review Item 10)
# ==============================================================================

def test_primary_connection_binds_the_first_enumerated_device(fake_hid):
    """Item 10: with two devices connected the primary bind is deterministic.

    Documents current behavior and claims no contract beyond determinism: the
    primary is the first entry of active_connections, i.e. dict insertion
    order, i.e. hid.enumerate() order through select_active_device_pairs. No
    priority rule participates - not product id, not alias, not wireless. The
    pair with the HIGHER product id is enumerated first here, so a reader that
    read a product-id rule into this pin would fail; the pin only holds while
    insertion order wins.
    """
    fake_hid.enumerate = lambda: (wired_pair(789, "VID_056A&PID_0315")
                                  + wired_pair(788, "VID_056A&PID_030C"))
    fake_hid._queue.extend(PlainDevice() for _ in range(4))

    enabled = threading.Event()
    enabled.set()
    stop = threading.Event()
    manager = DeviceManagerThread(enabled, stop, poll_interval=0.1,
                                  base_config=DriverConfig())
    all_threads = []
    try:
        manager.manage_connections()
        conns = manager.list_connections()
        all_threads.extend(t for conn in conns for t in conn["threads"].values())
        assert len(conns) == 2, "both wired pairs must reach active_connections"

        primary = manager.get_primary_connection()
        assert primary is conns[0], "primary is not the first inserted entry"
        assert primary["product_id"] == 789, (
            "primary bind did not follow enumeration order: got product_id "
            f"{primary['product_id']}")
        assert primary["identity"] == touch_controller.device_identity_hash(
            b"HID#VID_056A&PID_0315&MI_00", "")

        state, action = manager.get_primary_state_and_action()
        assert state is primary["state"], (
            "get_primary_state_and_action disagreed with get_primary_connection")
        assert action is primary["action"], (
            "get_primary_state_and_action disagreed with get_primary_connection")
    finally:
        stop.set()
        join_readers(*all_threads)


def test_two_connections_keep_their_own_runtime_objects(fake_hid):
    """Item 10: the primary bind shares nothing between the two devices.

    Second lane of the same determinism question: each connection owns its
    fork, state and dispatcher, so the first-wins primary is a GUI bind
    choice, not a shared runtime. A primary that leaked one device's objects
    into the other would make the bind nondeterministic in practice.
    """
    fake_hid.enumerate = lambda: (wired_pair(789, "VID_056A&PID_0315")
                                  + wired_pair(788, "VID_056A&PID_030C"))
    fake_hid._queue.extend(PlainDevice() for _ in range(4))

    enabled = threading.Event()
    enabled.set()
    stop = threading.Event()
    manager = DeviceManagerThread(enabled, stop, poll_interval=0.1,
                                  base_config=DriverConfig())
    all_threads = []
    try:
        manager.manage_connections()
        conns = manager.list_connections()
        all_threads.extend(t for conn in conns for t in conn["threads"].values())
        assert len(conns) == 2

        assert conns[0]["config"] is not conns[1]["config"]
        assert conns[0]["state"] is not conns[1]["state"]
        assert conns[0]["action"] is not conns[1]["action"]
        assert conns[0]["identity"] != conns[1]["identity"]
    finally:
        stop.set()
        join_readers(*all_threads)
