"""TRAY unit (TODO #448): tray stop = touch OUTPUT only + touch_toggle action.

The maintainer ruling (2026-11-16): the tray stop and the new assignable
touch_toggle action gate the TOUCH EMISSION lanes only - pen coordinate/
mapping output and tablet button/dial emissions stay live in every touch
state. One source of truth: the shared threading.Event (cli creates it;
tray, DeviceManagerThread, and every connection's ActionDispatcher hold
THE SAME object - never forked).

Pins (delegation): touch emission dead while stopped (gesture, cursor,
scroll, touch-part hold); pen + button emission lanes live (positive);
the mapped toggle flips the touch state from a button-event lane; the
tray icon state follows toggle-driven changes; blast-radius regression
pins (release edges never gate - no stuck inputs; the action itself is
never gated).
"""

import threading
import time

import pytest

import gui
from wtd.config import DriverConfig
from wtd.gestures import TabletState, process_touch_report
from wtd.sender import ActionDispatcher

from conftest import PlainDevice  # noqa: F401  (kept for fixture parity)


def touch_down_block(slot, x, y):
    return [slot, 0xC0, (x >> 4) & 0xFF, (y >> 4) & 0xFF,
            ((x & 0xF) << 4) | (y & 0xF), 0, 0, 0]


def touch_move_block(slot, x, y):
    # STATUS_ACTIVE_CONTACTS tracking update (0x80): keeps the contact,
    # moves it - a second 0xC0 would be a fresh DOWN (resets the drag).
    return [slot, 0x80, (x >> 4) & 0xFF, (y >> 4) & 0xFF,
            ((x & 0xF) << 4) | (y & 0xF), 0, 0, 0]


def touch_report(blocks):
    return [0x02, len(blocks)] + [b for block in blocks for b in block]


def make_dispatcher(touch_state=None):
    cfg = DriverConfig()
    cfg.feature_toggles["1f_tap"] = True
    cfg.action_mapping["1f_tap"] = "left_click"
    state = TabletState()
    return ActionDispatcher(state, cfg, touch_enabled_event=touch_state)


# -- touch emission lanes are dead while the shared flag is cleared ------

def test_touch_stop_silences_the_gesture_lane(stub_send_input):
    touch = threading.Event()
    touch.set()
    d = make_dispatcher(touch)
    d.dispatch_gesture_event("1f_tap")
    on_events = len(stub_send_input)
    assert on_events > 0, "precondition: the tap lane emits when live"

    touch.clear()
    stub_send_input.clear()
    d.dispatch_gesture_event("1f_tap")
    assert stub_send_input == [], "touch gesture output must be dead when stopped"

    touch.set()
    d.dispatch_gesture_event("1f_tap")
    assert len(stub_send_input) == on_events, "the lane must resume with the flag"


def test_touch_stop_silences_cursor_and_scroll_lanes(stub_send_input):
    touch = threading.Event()
    touch.set()
    cfg_d = make_dispatcher(touch)
    reports = [touch_report([touch_down_block(3, 100, 100)]),
               touch_report([touch_move_block(3, 300, 100)])]
    for report in reports:
        process_touch_report(report, cfg_d.state, cfg_d, cfg_d.config)
    moves = [e for e in stub_send_input if e[0] == "mouse"]
    assert moves, "precondition: the touch cursor lane emits when live"

    touch.clear()
    stub_send_input.clear()
    cfg_d.state.cursor_acc_x = 0.0
    cfg_d.state.cursor_acc_y = 0.0
    for report in reports:
        process_touch_report(report, cfg_d.state, cfg_d, cfg_d.config)
    assert stub_send_input == [], "touch cursor must be dead when stopped"

    cfg_d.send_mouse_scroll(0, 3)
    assert stub_send_input == [], "touch scroll must be dead when stopped"


def test_touch_stop_silences_the_touch_part_hold_press(stub_send_input):
    from wtd.config import TOUCH_MODIFIER_KEYS
    touch = threading.Event()
    touch.set()
    d = make_dispatcher(touch)
    d.press_touch_modifier(4, TOUCH_MODIFIER_KEYS[0])
    presses = [e for e in stub_send_input if e[0] == "key"]
    assert presses, "precondition: the touch-part press emits when live"

    touch.clear()
    stub_send_input.clear()
    d.press_touch_modifier(5, TOUCH_MODIFIER_KEYS[0])
    assert stub_send_input == [], "a new touch-part hold must not start when stopped"


# -- blast radius: the lanes that must STAY LIVE while touch is stopped ---

def test_stopped_touch_keeps_tablet_button_and_mapped_action_emission_live(
        stub_send_input):
    touch = threading.Event()  # CLEARED: touch stopped
    d = make_dispatcher(touch)
    d.press_tablet_button(2, "middle_click")  # a mapped button press edge
    flags = [e[1] for e in stub_send_input if e[0] == "mouse"]
    assert ActionDispatcher.MOUSEEVENTF_MIDDLEDOWN in flags, (
        "tablet button emission died with the touch stop - #448 violation")
    stub_send_input.clear()
    d.execute_mapped_action("undo")
    assert stub_send_input, "mapped actions died with the touch stop"


def test_stopped_touch_keeps_the_pen_cursor_lane_live(stub_send_input):
    # send_real_mouse_move is deliberately NOT gated inside the dispatcher:
    # the PEN relative lane shares that emitter (mapping.update_pen_cursor),
    # so the touch-cursor gate lives at the gestures call site instead
    # (pinned by the cursor leg above) and this method stays live for pen.
    touch = threading.Event()
    touch.set()
    d = make_dispatcher(touch)
    d.send_real_mouse_move(7, 9)
    assert stub_send_input, "precondition: the emitter sends when live"
    touch.clear()
    stub_send_input.clear()
    d.send_real_mouse_move(7, 9)
    assert [e for e in stub_send_input if e[0] == "mouse"], (
        "pen relative emission must survive a touch stop - #448 violation")


def test_release_edges_never_gate_so_holds_outliving_a_stop_get_released(
        stub_send_input):
    # No-flag-sharing regression pin (the stuck-input class): a touch-part
    # modifier held while live, stopped mid-hold, released while stopped -
    # the KEY UP must still land.
    from wtd.config import TOUCH_MODIFIER_KEYS
    touch = threading.Event()
    touch.set()
    d = make_dispatcher(touch)
    d.press_touch_modifier(6, TOUCH_MODIFIER_KEYS[1])
    stub_send_input.clear()

    touch.clear()
    d.release_touch_modifier(6)
    ups = [e for e in stub_send_input
           if e[0] == "key" and e[2] == ActionDispatcher.KEYEVENTF_KEYUP]
    assert ups, "release must never gate: a held modifier cannot be stranded"


# -- the assignable touch_toggle action ------------------------------------

def test_touch_toggle_is_a_configured_assignable_action():
    cfg = DriverConfig()
    assert "touch_toggle" in cfg.AVAILABLE_ACTIONS
    d = ActionDispatcher(TabletState(), cfg)
    assert "touch_toggle" in d.ACTION_DISPATCH


def test_touch_toggle_flips_the_shared_state_from_a_mapped_button_press(
        stub_send_input):
    touch = threading.Event()
    touch.set()
    d = make_dispatcher(touch)

    d.press_tablet_button(3, "touch_toggle")   # a button press edge taps it
    assert touch.is_set() is False, "the mapped toggle did not stop touch"
    d.release_tablet_button(3)                 # release edge: harmless no-op
    assert touch.is_set() is False

    d.press_tablet_button(3, "touch_toggle")
    assert touch.is_set() is True, "the mapped toggle did not resume touch"

    # And the control lane itself is never gated: works while stopped too.
    touch.clear()
    d.execute_mapped_action("touch_toggle")
    assert touch.is_set() is True


def test_touch_toggle_without_a_touch_state_is_a_logged_noop(stub_send_input):
    cfg = DriverConfig()
    d = ActionDispatcher(TabletState(), cfg)  # no touch state in this run
    d.execute_mapped_action("touch_toggle")   # must not raise, must emit
    assert stub_send_input == []


# -- the direct-injection bypass lanes (reviewer P1-1/P1-2) ---------------

def _seed_contacts(st, positions):
    # ACTIVE (0x80) tracking blocks for already-tracked contacts: state
    # fields are seeded directly so the gesture lane fires deterministically.
    st.active_contacts.clear()
    for i, (x, y) in enumerate(positions, start=1):
        st.active_contacts[i] = (x, y)
    return touch_report([touch_move_block(i, x, y)
                         for i, (x, y) in enumerate(positions, start=1)])


def _seed_session(st):
    st.last_num_active = None      # forces the mf-origin re-arm branch
    st.mf_start_centroid = None
    st.mf_start_spread = 0.0
    st.session_start_time = time.time()
    st.active_gesture = None


def test_stopped_touch_kills_the_5f_alt_tab_direct_injections(stub_send_input):
    # Reviewer P1-1: the 5f_alt_tab family injects keyboard output via
    # execute_mapped_action - NO choke method involved - so the gate
    # rides the calls; live fires, stopped fires nothing.
    def run(touch_state):
        stub_send_input.clear()
        d = make_dispatcher(touch_state)
        st = d.state
        _seed_session(st)
        report = _seed_contacts(st, [(100, 100), (200, 100), (300, 100),
                                     (400, 100), (500, 100)])
        process_touch_report(report, st, d, d.config)
        return list(stub_send_input)

    live = threading.Event()
    live.set()
    events = run(live)
    assert events, "precondition: 5f fires ctrl_alt_tab_initiate when live"

    stopped = threading.Event()  # cleared
    assert run(stopped) == [], "5f_alt_tab injected while touch was stopped"


def test_stopped_touch_kills_the_pinch_ctrl_hold_press(stub_send_input):
    # Reviewer P1-2: 2f_pinch activation presses Ctrl directly (default
    # ON); the press gates, the release lane never does.
    def run(touch_state):
        stub_send_input.clear()
        d = make_dispatcher(touch_state)
        st = d.state
        st.session_start_time = time.time()
        st.active_gesture = None
        # Keep last_num_active == contact count and a REAL mf origin: the
        # re-arm branch would otherwise reset mf_start_spread to the live
        # spread (delta 0, no pinch) - seed the pinch delta directly.
        st.last_num_active = 2
        st.mf_start_centroid = (300, 300)
        st.mf_start_spread = 0.0
        st.last_2finger_centroid = None
        st.start_time[2] = st.session_start_time  # hold-duration bookkeeping
        report = _seed_contacts(st, [(100, 100), (500, 500)])
        process_touch_report(report, st, d, d.config)
        assert st.active_gesture == "2f_pinch", "the pinch did not activate"
        return [e for e in stub_send_input if e[0] == "key"]

    live = threading.Event()
    live.set()
    assert run(live), "precondition: pinch presses Ctrl when live"

    stopped = threading.Event()  # cleared
    assert run(stopped) == [], "pinch pressed Ctrl while touch was stopped"


# -- the tray icon reflects toggle-driven changes (single source) ----------

class _RecordingIcon:
    def __init__(self):
        self.icon = None
        self.title = None


class _FakeRoot:
    def __init__(self):
        self.scheduled = []

    def after(self, ms, func):
        self.scheduled.append((ms, func))


def test_tray_icon_state_follows_a_toggle_driven_change():
    touch = threading.Event()
    touch.set()
    root = _FakeRoot()
    app = gui.DriverTrayApp(root=root, state_ref=None, config_ref=None,
                            enabled_event_ref=touch,
                            stop_event_ref=threading.Event())
    app.icon = _RecordingIcon()

    # The constructor armed the watcher; drive its ticks by hand.
    assert root.scheduled, "the constructor armed the icon watch"
    ms, tick = root.scheduled.pop()
    assert ms == gui.DriverTrayApp.ICON_WATCH_MS
    tick()                               # no change: no refresh, re-armed
    assert root.scheduled and app.icon.icon is None

    ms, tick = root.scheduled.pop()
    touch.clear()  # the touch_toggle action flipped it (reader-thread I/O)
    tick()
    assert "Touch Disabled" in app.icon.title
    assert "pen and tablet buttons stay live" in app.icon.title

    ms, tick = root.scheduled.pop()
    touch.set()
    tick()
    assert "Touch Enabled" in app.icon.title
    assert root.scheduled, "the tick keeps re-arming the Tk timer"


def test_tray_flag_stops_touch_output_end_to_end_with_the_shared_event(
        stub_send_input):
    # The delegation's headline: ONE flag - the tray toggling it gates the
    # dispatcher's touch lane; the same dispatcher's touch_toggle flips
    # the same flag the tray reads.
    touch = threading.Event()
    touch.set()
    app = gui.DriverTrayApp(root=None, state_ref=None, config_ref=None,
                            enabled_event_ref=touch,
                            stop_event_ref=threading.Event())
    d = make_dispatcher(touch)

    app.toggle_touch()                 # tray STOP (icon path)
    d.dispatch_gesture_event("1f_tap")
    assert stub_send_input == []
    assert app.is_touch_enabled() is False

    d.execute_mapped_action("touch_toggle")  # button-mapped resume
    assert app.is_touch_enabled() is True
    d.dispatch_gesture_event("1f_tap")
    assert stub_send_input, "touch output did not resume"
