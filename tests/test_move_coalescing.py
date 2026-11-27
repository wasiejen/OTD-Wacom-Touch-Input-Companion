"""Move coalescing (WTD-BATCH): latest-wins pending slot, ordered flush.

Pins the per-dispatcher absolute-move collapse: a drain burst emits at most
one ABSOLUTE move at the newest position (latest wins); every stateful
emission (barrel/tablet button, wheel notch, pressure edge, key) flushes the
pending move FIRST so a move never leapfrogs a stateful event; the OOR pen
path lands the final position before the release pair; the reader-loop time
gate caps positional emissions per flush interval (fake monotonic clock,
WM-52: no sleeps). Negative controls: the relative and wheel lanes are
byte-identical to their pre-change streams and never see the slot.

Synthetic 10-byte 0x02 pen reports per the documented Intuos-V1 layout, the
SQUARE synthetic desktop (U376 convention), and the stub_send_input ordered
injection log (conftest). The session inject guard stays the backstop.
"""

import sys
import threading

import pytest

from conftest import PlainDevice, make_hid_module

from wtd.config import DriverConfig
from wtd.gestures import TabletState
from wtd.mapping import process_pen_report
from wtd.sender import ActionDispatcher
from wtd import devices

SPECS = {"max_x": 44704, "max_y": 27940, "width_mm": 223.52, "height_mm": 139.7}
SQUARE_1440 = (0, 0, 1440, 1440)
ABS_FLAGS = 0x0001 | 0x8000 | 0x4000
MOVE_FLAG = 0x0001
LEFTDOWN = 0x0002
LEFTUP = 0x0004
WHEEL_FLAG = 0x0800
OOR_REPORT = [0x02, 0x80, 0, 0, 0, 0, 0, 0, 0, 0]


def build_pen_report(status, x=0, y=0, pressure=0, hover=0):
    """Encode fields back with the documented Intuos-V1 expressions."""
    return [0x02, status | (pressure & 1),
            (x >> 9) & 0xFF, (x >> 1) & 0xFF,
            (y >> 9) & 0xFF, (y >> 1) & 0xFF,
            (pressure >> 3) & 0xFF, ((pressure >> 1) & 0x03) << 6,
            0x00, (hover & 0xFC) | ((x & 1) << 1) | (y & 1)]


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


def process(report, state, dispatcher, config):
    process_pen_report(report, state, dispatcher, config, SPECS,
                       screen_metrics=SQUARE_1440)


def abs_events(events):
    """The absolute-move entries of the injection log, in order."""
    return [e for e in events if e[0] == "mouse" and e[1] == ABS_FLAGS]


# -- DoD pin 1: burst inside one cap window collapses to the newest point ---

def test_burst_collapses_to_latest_position(state, dispatcher, config,
                                            stub_send_input):
    # Three coordinate reports with no stateful edge: nothing emits during
    # the burst; the slot holds the newest position; one flush emits it once.
    process(build_pen_report(0x20, x=0, y=0), state, dispatcher, config)
    process(build_pen_report(0x20, x=22352, y=13970), state, dispatcher, config)
    process(build_pen_report(0x20, x=44704, y=27940), state, dispatcher, config)
    assert stub_send_input == []
    assert dispatcher.pending_move is not None
    dispatcher.flush_pending_move()
    assert abs_events(stub_send_input) == [("mouse", ABS_FLAGS, 0, 65535, 53247)]
    assert dispatcher.pending_move is None


# -- DoD pin 1b / supersede: a second queued move erases the first ----------

def test_second_move_supersedes_first(dispatcher, stub_send_input):
    dispatcher.queue_pen_absolute_move(10, 20)
    dispatcher.queue_pen_absolute_move(30, 40)
    assert stub_send_input == []
    dispatcher.flush_pending_move()
    assert stub_send_input == [("mouse", ABS_FLAGS, 0, 30, 40)]


# -- DoD pin 2 (leapfrog class): a barrel-edge report flushes first ---------

def test_barrel_edge_report_flushes_pending_move_first(state, dispatcher,
                                                       config, stub_send_input):
    config.action_mapping["pen_button_1"] = "left_click"
    process(build_pen_report(0x20, x=0, y=0), state, dispatcher, config)
    process(build_pen_report(0x20, x=22352, y=13970), state, dispatcher, config)
    process(build_pen_report(0x20, x=44704, y=27940), state, dispatcher, config)
    stub_send_input.clear()
    # The edge arrives in a coordinate report: the pending move (the newest
    # one so far) goes out BEFORE the button down; this report's own move is
    # then parked again behind the edge.
    process(build_pen_report(0x22, x=22352, y=13970), state, dispatcher, config)
    assert stub_send_input[0] == ("mouse", ABS_FLAGS, 0, 65535, 53247)
    assert stub_send_input[1] == ("mouse", LEFTDOWN, 0, 0, 0)
    assert len(abs_events(stub_send_input)) == 1
    assert dispatcher.pending_move is not None
    dispatcher.flush_pending_move()
    assert stub_send_input[-1] == ("mouse", ABS_FLAGS, 0, 32767, 32767)


# -- DoD pin 2 twins: stateful after pending move -> order preserved --------

def test_tablet_button_after_pending_move_order(dispatcher, stub_send_input):
    dispatcher.queue_pen_absolute_move(44704, 27940)
    dispatcher.press_tablet_button(1, "left_click")
    assert stub_send_input == [("mouse", ABS_FLAGS, 0, 44704, 27940),
                               ("mouse", LEFTDOWN, 0, 0, 0)]


def test_wheel_notch_after_pending_move_order(dispatcher, stub_send_input):
    dispatcher.queue_pen_absolute_move(7, 9)
    dispatcher.send_mouse_scroll(0, 1.0)
    assert stub_send_input == [("mouse", ABS_FLAGS, 0, 7, 9),
                               ("mouse", WHEEL_FLAG, 1, 0, 0)]


def test_key_after_pending_move_order(dispatcher, stub_send_input):
    dispatcher.queue_pen_absolute_move(7, 9)
    dispatcher.send_key(0x11)
    assert stub_send_input[0] == ("mouse", ABS_FLAGS, 0, 7, 9)
    assert stub_send_input[1][0] == "key"


def test_pressure_cross_orders_move_before_edge(state, dispatcher, config,
                                                stub_send_input):
    # Report 2 carries the move AND crosses the pressure threshold: the move
    # is queued, then the edge flushes it - wire order [move, LEFTDOWN].
    process(build_pen_report(0x20, x=0, y=0), state, dispatcher, config)
    process(build_pen_report(0x20, x=44704, y=27940, pressure=500),
            state, dispatcher, config)
    assert len(abs_events(stub_send_input)) == 1
    assert stub_send_input == [("mouse", ABS_FLAGS, 0, 65535, 53247),
                               ("mouse", LEFTDOWN, 0, 0, 0)]


# -- DoD pin 3: OOR lands the position before the release pair ---------------

def test_oor_flushes_move_before_release_pair(state, dispatcher, config,
                                              stub_send_input):
    process(build_pen_report(0x20, x=0, y=0, pressure=500),
            state, dispatcher, config)
    process(build_pen_report(0x20, x=44704, y=27940, pressure=500),
            state, dispatcher, config)
    stub_send_input.clear()
    process(OOR_REPORT, state, dispatcher, config)
    # The last parked move lands first, then the held-button release; and
    # NOTHING is emitted after the release.
    assert stub_send_input == [("mouse", ABS_FLAGS, 0, 65535, 53247),
                               ("mouse", LEFTUP, 0, 0, 0)]


def test_oor_with_nothing_held_still_lands_position(state, dispatcher, config,
                                                     stub_send_input):
    # OOR on a pen that holds nothing: the parked move still lands (the
    # release pair is absent) and nothing else follows.
    process(build_pen_report(0x20, x=44704, y=27940), state, dispatcher, config)
    stub_send_input.clear()
    process(OOR_REPORT, state, dispatcher, config)
    assert stub_send_input == [("mouse", ABS_FLAGS, 0, 65535, 53247)]
    assert state.pen_position is None
    assert dispatcher.pending_move is None


# -- DoD pin 4: cap math on the fake monotonic clock, no stranded tail -------

def test_time_gate_caps_burst_and_lands_final(dispatcher, config,
                                              stub_send_input):
    # 16x-rate burst over a 4.0 s window against an 8 Hz cap (interval
    # 0.125 s, exact dyadic steps): the documented rule gives at most
    # ceil(cap * W) = 32 emissions, the first only after one whole interval,
    # and the final position is present (no stranded tail).
    config.pen_move_flush_hz = 8.0
    for k in range(65):
        dispatcher.queue_pen_absolute_move(k, 0)
        dispatcher.time_gated_flush(k * 0.0625)
        if k == 1:
            # A pending move younger than one flush interval emits nothing.
            assert stub_send_input == []
    assert len(abs_events(stub_send_input)) == 32
    assert abs_events(stub_send_input)[-1] == ("mouse", ABS_FLAGS, 0, 64, 0)
    dispatcher.time_gated_flush(65 * 0.0625)
    assert len(abs_events(stub_send_input)) == 32


# -- DoD pin 5: idle reader emits nothing; no spin-flush ---------------------

def test_idle_gate_emits_nothing(dispatcher, config, stub_send_input):
    # No pending move: gate ticks never arm, never emit (loop spin leg).
    for t in (0.0, 0.5, 100.0):
        dispatcher.time_gated_flush(t)
    assert stub_send_input == []
    # A pending move older than the gate emits nothing DURING the window;
    # the emission requires the whole interval (flush is time-based, never
    # per empty read).
    config.pen_move_flush_hz = 8.0
    dispatcher.queue_pen_absolute_move(1, 1)
    dispatcher.time_gated_flush(0.0)
    for t in (0.05, 0.1, 0.124):
        dispatcher.time_gated_flush(t)
    assert stub_send_input == []
    dispatcher.time_gated_flush(0.125)
    assert stub_send_input == [("mouse", ABS_FLAGS, 0, 1, 1)]


class DrainedPlainDevice(PlainDevice):
    """Non-blocking-binding device: signals once its scripted queue drains."""

    def __init__(self, reports):
        super().__init__(reports)
        self.drained = threading.Event()

    def read(self, max_size=64):
        report = super().read(max_size)
        if not self.reports:
            self.drained.set()
        return report


def test_reader_loop_burst_collapses_no_spin_flush(state, config,
                                                    stub_send_input,
                                                    monkeypatch):
    # Integration through the reader loop with the PLAIN (non-blocking)
    # binding and a 1 Hz cap (interval 1 s >> drain time): the whole burst
    # stays collapsed - zero absolute emissions during drain, and the
    # thread-exit drain of the slot drops the parked position instead of
    # flushing it (release must never be delayed; no flush off the reader
    # thread beyond its own loop).
    config.pen_move_flush_hz = 1.0
    reports = [build_pen_report(0x20, x=100, y=100),
               build_pen_report(0x20, x=200, y=200),
               build_pen_report(0x20, x=300, y=300)]
    dev = DrainedPlainDevice(reports)
    hid_module = make_hid_module(device_queue=[dev])
    monkeypatch.setitem(sys.modules, "hid", hid_module)
    monkeypatch.setattr(devices, "hid", hid_module)

    dispatcher = ActionDispatcher(state, config)
    touch_enabled = threading.Event()
    touch_enabled.set()
    stop_event = threading.Event()
    thread = threading.Thread(
        target=devices.run_interface,
        args=("Pen", {"path": b"x", "interface_number": 0, "product_id": 789},
              state, dispatcher, stop_event, config))
    thread.start()
    assert dev.drained.wait(timeout=5.0)
    stop_event.set()
    thread.join(timeout=5.0)
    assert not thread.is_alive()
    assert abs_events(stub_send_input) == []
    assert stub_send_input == []
    assert dispatcher.pending_move is None


# -- DoD pin 6 negative control: relative lane byte-identical ----------------

def test_relative_lane_stream_byte_identical_slot_untouched(state, config,
                                                            stub_send_input):
    config.pen_mode = "relative"
    # pen_speed 8.0; the mm delta is int-truncated from IEEE floats, so the
    # emitted pairs follow the accumulator math exactly: (0,0) origin sets
    # no delta; (+100,0) -> (4,0); (+200,+100) -> (8,3) with the 0.999...px
    # y-remainder carried; (+0,+1) lands the carried remainder as (0,1) -
    # nothing lost, which is the point of the accumulator.
    reports = [build_pen_report(0x20, x=0, y=0),
               build_pen_report(0x20, x=100, y=0),
               build_pen_report(0x20, x=300, y=100),
               build_pen_report(0x20, x=300, y=101)]
    baseline = []
    for report in reports:
        process(report, state, ActionDispatcher(state, config), config)
        baseline.append(list(stub_send_input))
        stub_send_input.clear()

    state2 = TabletState()
    dispatcher2 = ActionDispatcher(state2, config)
    dispatcher2.pending_move = (7, 7)  # a parked absolute move must be inert
    with_slot = []
    for report in reports:
        process(report, state2, dispatcher2, config)
        with_slot.append(list(stub_send_input))
        stub_send_input.clear()

    assert with_slot == baseline
    assert baseline[1] == [("mouse", MOVE_FLAG, 0, 4, 0)]
    assert baseline[2] == [("mouse", MOVE_FLAG, 0, 8, 3)]
    assert baseline[3] == [("mouse", MOVE_FLAG, 0, 0, 1)]
    assert dispatcher2.pending_move == (7, 7)


# -- DoD pin 6 negative control: wheel lane byte-identical -------------------

def test_wheel_lane_sum_unchanged_by_slot(dispatcher, stub_send_input):
    baseline = []
    for _ in range(12):
        dispatcher.send_mouse_scroll(0, 0.5)
    baseline = list(stub_send_input)
    # 12 sub-notch 0.5 deltas sum to exactly 6 whole notches, none lost.
    assert baseline == [("mouse", WHEEL_FLAG, 1, 0, 0)] * 6

    dispatcher.state.scroll_acc_x = 0.0
    dispatcher.state.scroll_acc_y = 0.0
    dispatcher.pending_move = (9, 9)
    stub_send_input.clear()
    for _ in range(12):
        dispatcher.send_mouse_scroll(0, 0.5)
    # The slot flushes before the FIRST notch only; the notch stream itself
    # is byte-identical to the baseline.
    assert stub_send_input[0] == ("mouse", ABS_FLAGS, 0, 9, 9)
    assert stub_send_input[1:] == baseline


# -- Config knob: default, accept, reject ------------------------------------

def test_move_flush_hz_knob(config):
    assert config.pen_move_flush_hz == 250.0
    validated, warnings, errors = config.validate_config_data(
        {"motion_and_thresholds": {"pen_move_flush_hz": 300}})
    assert errors == []
    assert validated["motion"]["pen_move_flush_hz"] == 300.0
    for junk in (0, -5, "fast"):
        _, _, errors = config.validate_config_data(
            {"motion_and_thresholds": {"pen_move_flush_hz": junk}})
        assert any("pen_move_flush_hz" in e for e in errors), junk
