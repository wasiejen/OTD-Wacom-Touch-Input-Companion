"""Device-free test scaffolding: fake the `hid` module and the input layer.

No real Wacom device, no network, no GUI window, and no live desktop input
event is touched by this suite: the injection guard installed below blocks
every Win32 input entry point for the whole session.
"""

import contextlib
import ctypes
import os
import sys
import time
import traceback
import types

import pytest

# Make the product modules importable from tests/ without packaging changes.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ==============================================================================
# LIVE-INJECTION GUARD (U354 / TODO #338)
#
# Installed here, at conftest import time, so it is in place before any test
# module imports the product code. Every Win32 input-injection entry point on
# ctypes.windll.user32 is replaced for the whole session; the guard never
# calls the real API, so no test can inject a live desktop event.
#
# A test that exercises the dispatcher input layer requests the
# stub_send_input fixture: that function-scoped fixture shadows the guard for
# its own test only. Injection that reaches the guard is a test-isolation
# leak: the call is recorded with the active test nodeid and the caller
# chain, the injecting call raises, and the session fails naming offenders.
# ==============================================================================

INJECT_APIS = ("SendInput", "mouse_event", "keybd_event", "SetCursorPos")

inject_offenders = []
selfcheck_hits = []
_inject_seen = set()
_in_selfcheck = False
_current_nodeid = "<import/collection time>"


def _caller_chain():
    """Caller frames outside conftest, oldest first, as 'file:line'."""
    frames = [f for f in traceback.extract_stack()
              if not f.filename.replace("\\", "/").endswith("/conftest.py")
              and "site-packages" not in f.filename]
    return " <- ".join("%s:%d" % (os.path.basename(f.filename), f.lineno)
                       for f in frames[-6:])


def _block_injection(api):
    chain = _caller_chain()
    key = (api, _current_nodeid, chain)
    if _in_selfcheck:
        # The guard's own probe test: block the call, record no offender.
        selfcheck_hits.append(api)
    elif key not in _inject_seen:
        _inject_seen.add(key)
        inject_offenders.append(key)
    raise RuntimeError(
        "LIVE INJECTION BLOCKED: %s called by %s (chain: %s). "
        "Test-isolation leak: request the stub_send_input fixture."
        % (api, _current_nodeid, chain)
    )


def _install_inject_guard():
    """Replace every Win32 input entry point with a blocking recorder."""
    user32 = ctypes.windll.user32
    for api in INJECT_APIS:
        setattr(user32, api, lambda *args, _api=api: _block_injection(_api))


_install_inject_guard()


def pytest_runtest_setup(item):
    global _current_nodeid
    _current_nodeid = item.nodeid


@contextlib.contextmanager
def inject_guard_selfcheck():
    """Probe the guard from a test: the calls raise, no offender is recorded."""
    global _in_selfcheck
    _in_selfcheck = True
    try:
        yield
    finally:
        _in_selfcheck = False


@pytest.fixture(scope="session", autouse=True)
def inject_guard():
    """Fail the run naming every test that tried to inject a live event."""
    yield
    if inject_offenders:
        raise AssertionError(
            "live input injection leaked during this session:\n"
            + "\n".join("  offender: %s node=%s chain: %s" % entry
                        for entry in inject_offenders)
        )


class FakeDevice:
    """Stand-in for hid.device() driven by a scripted report queue.

    Exposes read_timeout() like some HID bindings do: a queued report returns
    immediately, and an empty queue waits at most the requested timeout.
    """

    def __init__(self, reports=None):
        self.reports = list(reports or [])
        self.opened_path = None
        self.nonblocking = None
        self.closed = False
        self.read_calls = 0
        self.timeout_waits = 0
        self.slept_seconds = 0.0

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

    def read_timeout(self, max_size, timeout_ms):
        """Timed semantics: return on arrival, otherwise wait the timeout."""
        self.read_calls += 1
        if self.reports:
            return self.reports.pop(0)
        self.timeout_waits += 1
        wait_s = min(timeout_ms / 1000.0, 0.2)
        self.slept_seconds += wait_s
        time.sleep(wait_s)
        return []

    def close(self):
        self.closed = True


class PlainDevice(FakeDevice):
    """Device exposing only read(), matching the Windows hidapi 0.15 wheel."""

    read_timeout = None


class ExplodingDevice(FakeDevice):
    """Fake device whose reads raise OSError once the queue drains."""

    def read(self, max_size=64):
        self.read_calls += 1
        if self.reports:
            return self.reports.pop(0)
        raise OSError("device disconnected")

    def read_timeout(self, max_size, timeout_ms):
        self.read_calls += 1
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
    """Opt-in input stub: replaces the guard's SendInput for this test only.

    A test that drives the dispatcher input layer requests this fixture: its
    monkeypatch shadows the session injection guard, so the dispatcher's
    SendInput calls are recorded here as events. A test that injects without
    requesting it is an isolation leak the guard reports.
    """
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
