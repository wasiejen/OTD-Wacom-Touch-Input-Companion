"""Device-free test scaffolding: fake the `hid` module and the input layer.

No real Wacom device, no network, and no GUI window is touched by this suite.
"""

import os
import sys
import types

import pytest

# Make the product modules importable from tests/ without packaging changes.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class FakeDevice:
    """Stand-in for hid.device() driven by a scripted report queue."""

    def __init__(self, reports=None):
        self.reports = list(reports or [])
        self.opened_path = None
        self.nonblocking = None
        self.closed = False
        self.read_calls = 0

    def open_path(self, path):
        self.opened_path = path

    def set_nonblocking(self, value):
        self.nonblocking = value

    def read(self, max_size=64):
        """Non-blocking semantics: empty list when the queue is drained."""
        self.read_calls += 1
        if self.reports:
            return self.reports.pop(0)
        return []

    def close(self):
        self.closed = True


class ExplodingDevice(FakeDevice):
    """Fake device whose read() raises OSError after the queue drains."""

    def read(self, max_size=64):
        if self.reports:
            return self.reports.pop(0)
        raise OSError("device disconnected")


def make_hid_module(devices=None, device_queue=None):
    """Build a fake `hid` module exposing enumerate() and device()."""
    module = types.ModuleType("hid")
    queue = list(device_queue or [])

    def enumerate():
        return list(devices or [])

    def device():
        if queue:
            return queue.pop(0)
        return FakeDevice()

    module.enumerate = enumerate
    module.device = device
    module._queue = queue
    return module


@pytest.fixture
def fake_hid(monkeypatch):
    """Install a fake hid module before touch_controller resolves hid."""
    module = make_hid_module()
    monkeypatch.setitem(sys.modules, "hid", module)
    import touch_controller
    monkeypatch.setattr(touch_controller, "hid", module)
    return module


@pytest.fixture
def stub_send_input(monkeypatch):
    """Replace the Win32 SendInput call the dispatcher uses; record events."""
    import ctypes

    events = []

    def fake_send_input(count, data, size):
        ii = data[0] if data is not None else None
        if ii is not None:
            if ii.type == 0:
                events.append(("mouse", ii.mi.dwFlags, ii.mi.mouseData, ii.mi.dx, ii.mi.dy))
            elif ii.type == 1:
                events.append(("key", ii.ki.wVk, ii.ki.dwFlags))
        return count

    monkeypatch.setattr(ctypes.windll.user32, "SendInput", fake_send_input)
    return events
