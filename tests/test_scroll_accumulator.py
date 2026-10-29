"""Scroll fractional-accumulator tests (TODO #252).

SendInput is stubbed; no device and no real input injection.
"""

import pytest

from touch_controller import ActionDispatcher, TabletState


@pytest.fixture
def dispatcher(stub_send_input):
    state = TabletState()
    return ActionDispatcher(state), stub_send_input


def wheel_events(events):
    return [e for e in events if e[0] == "mouse" and e[1] == ActionDispatcher.MOUSEEVENTF_WHEEL]


def hwheel_events(events):
    return [e for e in events if e[0] == "mouse" and e[1] == ActionDispatcher.MOUSEEVENTF_HWHEEL]


def test_sub_notch_deltas_accumulate_into_wheel_events(dispatcher):
    ad, events = dispatcher

    for _ in range(10):
        ad.send_mouse_scroll(0, 0.4)

    emitted = wheel_events(events)
    assert len(emitted) >= 1, "0.4 px deltas over 10 packets emitted no wheel event"
    assert sum(e[2] for e in emitted) == 4


def test_sub_notch_accumulator_keeps_only_the_remainder(dispatcher):
    ad, events = dispatcher

    ad.send_mouse_scroll(0, 0.4)
    ad.send_mouse_scroll(0, 0.4)
    ad.send_mouse_scroll(0, 0.4)

    assert len(wheel_events(events)) == 1
    assert ad.state.scroll_acc_y == pytest.approx(0.2, abs=1e-9)


def test_whole_notch_delta_emits_exactly_its_units(dispatcher):
    ad, events = dispatcher

    ad.send_mouse_scroll(0, 120)

    assert wheel_events(events) == [
        ("mouse", ActionDispatcher.MOUSEEVENTF_WHEEL, 120, 0, 0)
    ]
    assert ad.state.scroll_acc_y == 0.0


def test_integral_deltas_do_not_double_emit(dispatcher):
    ad, events = dispatcher

    ad.send_mouse_scroll(0, 120)
    ad.send_mouse_scroll(0, 120)

    emitted = wheel_events(events)
    assert len(emitted) == 2
    assert sum(e[2] for e in emitted) == 240


def as_signed(mouse_data):
    """mouseData is a DWORD; a negative wheel delta arrives as its 32-bit pattern."""
    return mouse_data - 2 ** 32 if mouse_data >= 2 ** 31 else mouse_data


def test_negative_sub_notch_deltas_accumulate(dispatcher):
    ad, events = dispatcher

    for _ in range(10):
        ad.send_mouse_scroll(0, -0.4)

    emitted = wheel_events(events)
    assert len(emitted) >= 1
    assert all(as_signed(e[2]) < 0 for e in emitted)
    assert sum(as_signed(e[2]) for e in emitted) <= -4


def test_horizontal_accumulates_independently(dispatcher):
    ad, events = dispatcher

    for _ in range(5):
        ad.send_mouse_scroll(0.6, 0)

    emitted = hwheel_events(events)
    assert len(emitted) >= 2
    assert sum(e[2] for e in emitted) >= 2
    assert wheel_events(events) == []
    assert ad.state.scroll_acc_y == 0.0
    assert ad.state.scroll_acc_x < 1.0
