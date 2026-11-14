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
happened yet. The one product-internal wait this module inherits is main()'s
own 1.0 s startup readout (touch_controller.py:3626), not a test sleep.

Pins that cannot pass with today's code call pytest.xfail with a reason
naming the unit that must flip them (A2, A3, A4). No lane pins a defect as
the desired outcome: the gate states the requirement once, and the xfail
branch only records that the current build does not meet it. The gate is
deliberately imperative instead of a strict marker: a blanket marker would
also swallow a broken churn harness or a release lane that stopped injecting,
which are different bugs the reader must see as failures.
"""

import sys
import threading
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
from vk_codes import vk_codes_dict

from conftest import PlainDevice, SignalDevice

JOIN_WINDOW_SECONDS = 1.0
EVENT_WAIT_SECONDS = 2.0
TICK = 0.02

BASE_ACTION = "volume_up"
APPLIED_ACTION = "volume_down"


class GateDevice(PlainDevice):
    """Plain device that parks its reader inside read() until the test lets go.

    The shutdown lane needs readers that are provably still running when the
    product's own shutdown path returns, so an arriving report would not do:
    read() blocks on gate with no bound and records the reader handle. The
    lane always releases the gate in its finally.
    """

    def __init__(self):
        super().__init__()
        self.gate = threading.Event()
        self.first_read = threading.Event()
        self.reader_threads = []

    def read(self, max_size=64):
        self.reader_threads.append(threading.current_thread())
        self.first_read.set()
        while not self.gate.is_set():
            self.gate.wait(0.5)
        return []

    def close(self):
        super().close()
        self.reader_threads = []


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
        kwargs={"idle_poll": TICK},
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


def test_shutdown_path_leaves_no_reader_alive_behind_it(fake_hid, monkeypatch,
                                                        tmp_path):
    """Item 1 / A2: main()'s own shutdown path has to wait for its readers.

    The two pins above show the release lands when somebody joins; this lane
    pins the joiner. The gate devices park both readers inside read(), so at
    the moment main() returns they are provably still running. Today main()'s
    finally (:3662-3666) sets stop_event, prints, and returns: nothing joins
    the readers, the interpreter is free to exit, and both daemon threads die
    wherever they stand - the stuck-Ctrl class Item 1 reports. A2's bounded
    join (or non-daemon readers) makes this lane green.

    This is the only lane that runs main(): it patches argv, the module config,
    and the gui import, so the stub GUI closes as soon as it is launched. The
    inherited 1.0 s cost is main()'s own startup readout (:3626).
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["touch_controller.py"])
    monkeypatch.setattr(touch_controller, "config", DriverConfig())
    fake_hid.enumerate = lambda: wired_pair(789, "VID_056A&PID_0315")
    gates = [GateDevice(), GateDevice()]
    fake_hid._queue.extend(gates)

    gui_stub = types.ModuleType("gui")

    def stub_launch_gui(state, gui_config, touch_enabled_event, stop_event,
                        device_info=None):
        return None

    gui_stub.launch_gui = stub_launch_gui
    monkeypatch.setitem(sys.modules, "gui", gui_stub)

    try:
        touch_controller.main()   # returns when the stub GUI closes

        for dev in gates:
            assert dev.first_read.wait(EVENT_WAIT_SECONDS), (
                "main() never drove a reader into its read loop")
        readers = [t for dev in gates for t in dev.reader_threads]
        assert readers, "main() spawned no reader thread"

        behind = live_threads(readers)
        if behind:
            pytest.xfail(
                "A2 (review Item 1): main()'s shutdown path left "
                f"{len(behind)} reader(s) alive - the finally sets stop_event "
                "and returns with no join (touch_controller.py:3662-3666), so "
                "the daemon threads' release lane is a race at process exit.")
        # Green condition: main() returned with zero live readers.
    finally:
        for dev in gates:
            dev.gate.set()
        join_readers(*[t for dev in gates for t in dev.reader_threads])


# ==============================================================================
# P2 - Apply-Live durability across pause -> reconnect (Item 2 / unit A3)
# ==============================================================================

def test_apply_live_survives_pause_reconnect(fake_hid, monkeypatch, tmp_path):
    """Item 2 / A3: a live-applied value must outlive its connection.

    "Apply Live" mutates the connection's own config fork in place (the U386
    config-fork refresh contract in the run_interface docstring, :2947-2951).
    Pause clears the enable event, both readers break out, and the next
    manage_connections pass drops the dead entry and respawns it with a fresh
    fork. The applied value has to be readable from that respawn's connection
    entry - today it is lost silently, which is the user-data-loss lane Item 2
    reports.

    Scope of this pin: the config fork the connection entry carries across the
    churn. The reader side of the same contract - that every spawn passes that
    fork as cfg - is owned by test_manager_passes_fork_to_every_reader_spawn
    (tests/test_apply_live_propagation.py), and the two pins together are what
    the reader actually reads.

    A3 route note for the A3 handover: the manager-side route (keep the
    per-identity fork across the churn) satisfies this pin as written. The
    other route the review names - the GUI apply call writing through to
    base_config - is only reachable through gui.apply_settings; if A3 ships
    that route alone, this fork-level pin stays red and A3 must re-point it at
    the GUI apply call rather than delete it.
    """
    monkeypatch.chdir(tmp_path)
    base = DriverConfig()
    base.action_mapping["btn1_press"] = BASE_ACTION
    fake_hid.enumerate = lambda: wired_pair(789, "VID_056A&PID_0315")
    fake_hid._queue.extend(PlainDevice() for _ in range(4))

    enabled = threading.Event()
    enabled.set()
    stop = threading.Event()
    manager = DeviceManagerThread(enabled, stop, poll_interval=0.1,
                                  base_config=base)
    all_threads = []
    try:
        manager.manage_connections()
        conn = manager.get_primary_connection()
        assert conn is not None, "the fake pair produced no connection"
        all_threads.extend(conn["threads"].values())
        fork = conn["config"]
        assert fork.action_mapping["btn1_press"] == BASE_ACTION
        assert fork is not base

        fork.action_mapping["btn1_press"] = APPLIED_ACTION  # == Apply Live

        enabled.clear()   # pause: the readers break out
        join_readers(*all_threads)
        enabled.set()     # resume

        manager.manage_connections()   # the churn pass: drop the entry, respawn
        respawned = manager.get_primary_connection()
        assert respawned is not None, "no connection after the respawn"
        all_threads = list(respawned["threads"].values())

        survived = respawned["config"].action_mapping["btn1_press"]
        if survived != APPLIED_ACTION:
            pytest.xfail(
                "A3 (review Item 2): Apply Live lost across pause/reconnect - "
                "the manager deletes the connection entry on pause "
                "(touch_controller.py:3192-3204) and the respawn forks a fresh "
                "config from base_config (:3235), so the reader reads "
                f"'{survived}' again although the user applied "
                f"'{APPLIED_ACTION}'.")
        # Green condition: the respawned connection carries APPLIED_ACTION.
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
    one connection do (:3240-3241). With the exits serialized, the second
    release_held_inputs pass finds held_keys empty and must inject no second
    Ctrl-up. This leg is green today: the idempotence holds without a lock as
    long as only one reader exits at a time.
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

    release_held_inputs walks a snapshot of held_keys (:361-363) and
    release_key injects the key-up BEFORE it discards from the set (:341-343);
    no lock guards that set (Item 7). The barrier below does not rewrite the
    algorithm - it only widens the window the product already has: both
    threads stand inside send_key(release=True), i.e. both have snapshotted the
    held Ctrl and neither has discarded, before either injects. The two exits
    release each other, so no sleep is needed and the interleaving is not left
    to luck.

    Today both readers see the held modifier, so two Ctrl-ups land: the
    requirement (one) fails, and A4 (one lock over the held-input sets) is the
    unit that must close it.
    """
    state = TabletState()
    dispatcher = ActionDispatcher(state)
    dispatcher.press_key("ctrl")
    stub_send_input.clear()

    inside_release = threading.Barrier(2, timeout=EVENT_WAIT_SECONDS)
    original_send_key = ActionDispatcher.send_key

    def widened_send_key(self, vk_code, release=False, *args, **kwargs):
        # Scoped to this test's dispatcher: a class-wide seam would let any
        # other releasing dispatcher join the barrier and skew the count.
        if release and self is dispatcher:
            try:
                inside_release.wait()
            except threading.BrokenBarrierError:
                # Only one arrival means A4 already turned the second exit
                # into a no-op. Inject anyway: the count below is the
                # measurement, and the one release must still land.
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
    if ups > 1:
        pytest.xfail(
            "A4 (review Item 7): concurrent reader exits released the held "
            f"modifier {ups} times - held_keys has no owner and no lock, so "
            "both finallys (:3041-3045) snapshotted the Ctrl as still held.")
    # Green condition: exactly one Ctrl-up, and the held set is empty.
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
