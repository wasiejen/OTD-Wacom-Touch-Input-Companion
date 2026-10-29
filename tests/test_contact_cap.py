"""Over-cap contact handling tests (TODO #253).

Synthetic HID reports drive the state machine directly; no device, no GUI.
"""

import threading
import time

import pytest

import touch_controller
from touch_controller import (
    MAX_TOUCH_CONTACTS,
    ActionDispatcher,
    TabletState,
    clamped_contact_index,
    process_touch_report,
    run_interface,
)

from conftest import FakeDevice


def touch_down_block(slot, x, y):
    """One 8-byte sub-block: slot id, touch-down status, packed x/y."""
    return [slot, 0xC0, (x >> 4) & 0xFF, (y >> 4) & 0xFF, ((x & 0xF) << 4) | (y & 0xF), 0, 0, 0]


def release_block(slot):
    return [slot, 0x20, 0, 0, 0, 0, 0, 0]


def touch_report(blocks):
    return [0x02, len(blocks)] + [b for block in blocks for b in block]


def down_report(count):
    # Slot id 0 is a block-header byte value the parser treats as an empty
    # block, so synthetic contacts use slot ids starting at 1.
    return touch_report([touch_down_block(i, 100 + i, 200 + i) for i in range(1, count + 1)])


def test_clamped_index_caps_over_the_slot_limit():
    assert clamped_contact_index(MAX_TOUCH_CONTACTS + 1) == MAX_TOUCH_CONTACTS
    assert clamped_contact_index(MAX_TOUCH_CONTACTS) == MAX_TOUCH_CONTACTS
    assert clamped_contact_index(5) == 5


def test_clamped_index_stays_in_range_for_bad_counts():
    assert clamped_contact_index(0) == 0
    assert clamped_contact_index(-3) == 0


def test_start_time_table_is_sized_from_the_slot_count():
    assert len(TabletState().start_time) == MAX_TOUCH_CONTACTS + 1


def test_eleven_contact_report_processes_without_exception():
    state = TabletState()
    ad = ActionDispatcher(state)

    process_touch_report(down_report(11), state, ad)

    assert len(state.active_contacts) == 11
    assert state.peak_contact_count == 11
    assert state.last_num_active == 11


def test_twelve_contact_report_processes_without_exception():
    state = TabletState()
    ad = ActionDispatcher(state)

    process_touch_report(touch_report([touch_down_block(i, 50 + i, 60 + i) for i in range(1, 13)]),
                         state, ad)

    assert len(state.active_contacts) == 12
    assert state.peak_contact_count == 12


def test_state_still_processes_reports_after_an_over_cap_report():
    state = TabletState()
    ad = ActionDispatcher(state)

    process_touch_report(down_report(11), state, ad)
    process_touch_report(touch_report([release_block(i) for i in range(1, 12)]), state, ad)

    assert state.active_contacts == {}
    assert state.last_num_active == 0


def test_normal_contact_count_indexes_its_own_cell():
    state = TabletState()
    ad = ActionDispatcher(state)

    process_touch_report(down_report(2), state, ad)

    assert len(state.active_contacts) == 2
    assert state.start_time[2] is not None


def test_interface_thread_survives_an_over_cap_report(fake_hid, stub_send_input):
    dev = FakeDevice(reports=[down_report(11)])
    fake_hid._queue.append(dev)
    state = TabletState()
    ad = ActionDispatcher(state)

    enabled = threading.Event()
    enabled.set()
    stop = threading.Event()

    thread = threading.Thread(
        target=run_interface,
        args=("Touch", {"interface_number": 1, "path": b"fake-path"}, state, ad, enabled, stop),
        kwargs={"idle_poll": 0.02},
        daemon=True,
    )
    thread.start()

    deadline = time.monotonic() + 1.0
    while len(state.active_contacts) != 11 and time.monotonic() < deadline:
        time.sleep(0.002)

    assert len(state.active_contacts) == 11, "the over-cap report was not processed"
    assert thread.is_alive(), "the interface thread died on the over-cap report"
    assert state.peak_contact_count == 11

    enabled.clear()
    thread.join(timeout=0.5)
    assert not thread.is_alive()
    assert dev.closed is True
