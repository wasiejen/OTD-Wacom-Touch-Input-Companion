"""Connection-class selection for the prefer_wireless ruling (U64).

Pure selection plus one fake-hid enumeration check; no real device.
"""

import threading

from touch_controller import DeviceManagerThread, select_active_device_pairs


def make_pair(dev_id, wireless):
    return {
        "id": dev_id,
        "product_id": 791 if wireless else 789,
        "product_string": "Wacom Wireless Receiver" if wireless else "Wacom Intuos Pro",
        "wireless": wireless,
        "pen_device": {},
        "touch_device": {},
    }


def test_wireless_wins_when_flag_set():
    pairs = [make_pair("wired_0", False), make_pair("wireless_0", True)]

    active, skipped = select_active_device_pairs(pairs, True)

    assert [pair["id"] for pair in active] == ["wireless_0"]
    assert [pair["id"] for pair in skipped] == ["wired_0"]


def test_wired_wins_when_flag_unset():
    pairs = [make_pair("wireless_0", True), make_pair("wired_0", False)]

    active, skipped = select_active_device_pairs(pairs, False)

    assert [pair["id"] for pair in active] == ["wired_0"]
    assert [pair["id"] for pair in skipped] == ["wireless_0"]


def test_single_class_setup_keeps_every_pair():
    pairs = [make_pair("a_0", False), make_pair("b_1", False)]

    active, skipped = select_active_device_pairs(pairs, True)

    assert [pair["id"] for pair in active] == ["a_0", "b_1"]
    assert skipped == []


def test_enumeration_marks_wireless_pairs(fake_hid):
    """The wireless flag comes from the product string, per paired device."""
    vendor = DeviceManagerThread.VENDOR_ID
    fake_hid.enumerate = lambda: [
        {"vendor_id": vendor, "product_id": 791,
         "product_string": "Wacom Wireless Receiver", "interface_number": 1,
         "path": b"ACM&WO&11&MI_00"},
        {"vendor_id": vendor, "product_id": 791,
         "product_string": "Wacom Wireless Receiver", "interface_number": 2,
         "path": b"ACM&WO&11&MI_01"},
        {"vendor_id": vendor, "product_id": 789,
         "product_string": "Wacom Intuos Pro", "interface_number": 0,
         "path": b"ACM&WI&22&MI_00"},
        {"vendor_id": vendor, "product_id": 789,
         "product_string": "Wacom Intuos Pro", "interface_number": 1,
         "path": b"ACM&WI&22&MI_01"},
    ]
    manager = DeviceManagerThread(threading.Event(), threading.Event())

    pairs = manager.enumerate_wacom_devices()

    assert sorted(pair["wireless"] for pair in pairs) == [False, True]
    active, skipped = select_active_device_pairs(pairs, True)
    assert all(pair["wireless"] for pair in active)
    assert all(not pair["wireless"] for pair in skipped)
