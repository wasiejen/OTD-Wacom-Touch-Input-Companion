"""Device-free test scaffolding: fake the `hid` module and the input layer.

No real Wacom device, no network, no mapped GUI window, and no live desktop
input event is touched by this suite: the injection guard installed below
blocks every Win32 input entry point for the whole session. The U383 pins in
tests/test_gui_device_bar.py construct Tk widgets under a withdrawn root with
no event pump -- every window stays unmapped (pinned in build_window). Do not
create and destroy a second Tk root in tests while that module's root is
alive: on Windows a fresh first root after destroy() can fail to relocate
init.tcl (measured U383).
"""

import atexit
import contextlib
import ctypes
import os
import sys
import threading
import time
import traceback
import types

import pytest

# R1 P1-2: keep test emissions out of the maintainer's live hunting
# file. Set before any wtd import in this process: the wtd_file sink
# attaches at first import with this path (the product never sets it).
os.environ.setdefault(
    "WTD_LOG_FILE",
    os.path.join(os.environ.get("TEMP", os.sep + "tmp"), "wtd-test-hunt.log"),
)

from wtd import devices

# ==============================================================================
# TEST-CWD GUARD (estate TODO #425) - runs at conftest IMPORT time, i.e.
# during COLLECTION, so a poisoned CWD fails the run BEFORE any test executes.
#
# WHY import-time and not an autouse session fixture (the TODO #425 wording
# named a fixture): an autouse session fixture runs in SETUP, after
# collection, so it can only error every test - the #425 acceptance requires
# COLLECTION to fail with a named message. A module-level raise in this file
# is what pytest reports as a collection error.
#
# WHY the guard exists: wtd.config.CONFIG_FILE_PATH is the bare
# relative name "user.cfg", and several test modules instantiate
# DriverConfig() without chdir - any user.cfg left in the pytest CWD by a
# debug run silently overrides the mapping defaults those tests assume
# (deterministic touch/pen thread failures, measured in the U424 debug run).
# The live app config must never sit in the test CWD; the file is NEVER
# deleted or moved by this guard - it only refuses to run.
# ==============================================================================
def _guard_test_cwd_free_of_config():
    stray = os.path.join(os.getcwd(), "user.cfg")
    if os.path.exists(stray):
        raise RuntimeError(
            "TEST-CWD GUARD: stray user.cfg found in the pytest CWD: "
            f"{stray}\n"
            'DriverConfig() resolves CONFIG_FILE_PATH="user.cfg" against '
            "the CWD, so this file would silently override the mapping "
            "defaults every DriverConfig()-without-chdir test assumes "
            "(deterministic touch/pen thread failures - estate TODO #425). "
            "Either a debug run left it here, or the app itself was run "
            "from this directory and saved its live config here. MOVE it "
            "out of the run CWD - do NOT delete it if it is your live app "
            "config - then re-run the suite. This guard never touches the "
            "file itself.")


_guard_test_cwd_free_of_config()


@pytest.fixture(scope="session", autouse=True)
def _isolate_appdata_config_lane(tmp_path_factory):
    """Point the %APPDATA% config lane (unit B1 / Item 5) at a throwaway folder.

    The approved search order falls back to %APPDATA%/wtd/user.cfg, and several
    lanes run main() or a save path inside a tmp folder; without this fixture a
    test could read - or on a first save create - the developer's real per-user
    config. Pins that must own the lane pass appdata_path to
    resolve_config_paths explicitly instead of relying on this default.
    """
    root = tmp_path_factory.mktemp("appdata-isolation")
    previous = os.environ.get("APPDATA")
    os.environ["APPDATA"] = str(root)
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("APPDATA", None)
        else:
            os.environ["APPDATA"] = previous

# Make the product modules importable from tests/ without packaging changes.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ==============================================================================
# LIVE-INJECTION GUARD (U354 / TODO #338; U369 hardening / TODO #354)
#
# Installed here, at conftest import time, so it is in place before any test
# module imports the product code. Every Win32 input-injection entry point is
# replaced for the whole session; the guard never calls the real API, so no
# test can inject a live desktop event. Coverage (U369):
# - the loader-cached ctypes.windll.user32 instance carries direct patches;
# - ctypes.CDLL.__getattr__ is wrapped, so no other ctypes DLL instance (a
#   fresh CDLL/WinDLL/OleDLL) can resolve an inject API name at all. The
#   wrap fires only for names in INJECT_APIS; every other attribute lookup
#   (GetForegroundWindow and friends) delegates to ctypes unchanged. DLL
#   loading itself is not intercepted.
#
# A test that exercises the dispatcher input layer requests the
# stub_send_input fixture: that function-scoped fixture shadows the guard for
# its own test only. Injection that reaches the guard is a test-isolation
# leak: the call is recorded with the active test nodeid and the caller
# chain, the injecting call raises, and the session fails naming offenders.
# U369 sharpens attribution: the chain keeps third-party frames (only pytest
# machinery is filtered), the selfcheck window is owned by one thread, the
# active nodeid resets on teardown, and calls recorded after the session
# fixture check (atexit time) are reported at interpreter exit.
# ==============================================================================

INJECT_APIS = ("SendInput", "mouse_event", "keybd_event", "SetCursorPos")

inject_offenders = []
selfcheck_hits = []
_inject_seen = set()
_selfcheck_thread = None
_current_nodeid = "<import/collection time>"
_offenders_reported = 0


def _is_pytest_frame(filename):
    """True when the frame belongs to pytest machinery, not to product code."""
    parts = filename.replace("\\", "/").split("/")
    return any(part in ("_pytest", "pytest", "pluggy") for part in parts)


def _caller_chain():
    """Caller frames outside conftest/pytest machinery, oldest first, as 'file:line'.

    U369: the old site-packages filter truncated third-party frames (e.g.
    pystray); only pytest machinery (_pytest, pytest, pluggy) is filtered now.
    """
    frames = [f for f in traceback.extract_stack()
              if not f.filename.replace("\\", "/").endswith("/conftest.py")
              and not _is_pytest_frame(f.filename)]
    return " <- ".join("%s:%d" % (os.path.basename(f.filename), f.lineno)
                       for f in frames[-6:])


def _block_injection(api):
    chain = _caller_chain()
    key = (api, _current_nodeid, chain)
    if _selfcheck_thread is not None and threading.get_ident() == _selfcheck_thread:
        # The guard's own probe test, and only from the thread that opened
        # the window (U369): block the call, record no offender. A call from
        # any other thread racing the window is an offender.
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
    # U369: the instance patch covers only the loader-cached windll.user32.
    # Every ctypes DLL instance resolves function names through
    # CDLL.__getattr__ (WinDLL/OleDLL/PyDLL do not override it), so wrapping
    # that one lookup blocks inject API resolution on fresh instances too.
    original_getattr = ctypes.CDLL.__getattr__

    def _guarded_getattr(self, name):
        if name in INJECT_APIS:
            _block_injection(name)
        return original_getattr(self, name)

    ctypes.CDLL.__getattr__ = _guarded_getattr


_install_inject_guard()


def pytest_runtest_setup(item):
    global _current_nodeid
    _current_nodeid = item.nodeid


@pytest.hookimpl(wrapper=True)
def pytest_runtest_teardown(item, nextitem):
    # U369: after each test's teardown phase the active nodeid resets, so
    # teardown/atexit-time injections are attributed to the teardown phase
    # instead of silently blamed on the last test that ran. The single-yield
    # wrapper form runs the reset for clean and failing teardown alike.
    global _current_nodeid
    try:
        yield
    finally:
        _current_nodeid = "<teardown time>"


@contextlib.contextmanager
def inject_guard_selfcheck():
    """Probe the guard from a test: the calls raise, no offender is recorded.

    U369: the window is owned by the thread that opened it; a call from any
    other thread (e.g. a daemon racing the probe) is recorded as an offender.
    """
    global _selfcheck_thread
    _selfcheck_thread = threading.get_ident()
    try:
        yield
    finally:
        _selfcheck_thread = None


@atexit.register
def _report_post_session_offenders():
    """U369: calls recorded after the session fixture check must surface.

    Registered at conftest import, the earliest atexit handler of the run, so
    atexit's LIFO order runs it last: any later-registered atexit injection
    has already raised and been recorded by then. Late offenders are printed
    to stderr and the interpreter exits nonzero -- a post-teardown leak can
    never pass silently. Injections recorded before a still-earlier handler
    remain out of scope (documented limit).
    """
    late = inject_offenders[_offenders_reported:]
    if not late:
        return
    sys.stderr.write(
        "POST-SESSION LIVE INJECTION REPORT: %d call(s) recorded after the "
        "session inject-guard check:\n" % len(late)
        + "".join("  offender: %s node=%s chain: %s\n" % entry for entry in late)
    )
    sys.stderr.flush()
    os._exit(1)


@pytest.fixture(scope="session", autouse=True)
def inject_guard():
    """Fail the run naming every test that tried to inject a live event."""
    global _offenders_reported
    yield
    _offenders_reported = len(inject_offenders)
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


class SignalDevice(PlainDevice):
    """Plain device that signals its own lifecycle with events, not sleeps.

    first_read sets on every read (a waiter only needs the first one),
    closed_event sets when the device is closed, in a finally so a raising
    close cannot strand a waiter. Those are the two points the lifetime pins
    in tests/test_lifecycle_pins.py synchronize on, so a slow machine waits
    longer but never reads state that has not happened yet.
    """

    def __init__(self):
        super().__init__()
        self.first_read = threading.Event()
        self.closed_event = threading.Event()

    def read(self, max_size=64):
        self.first_read.set()
        return super().read(max_size)

    def close(self):
        try:
            super().close()
        finally:
            self.closed_event.set()


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
    """Install a fake hid module before the devices lane resolves hid."""
    module = make_hid_module()
    monkeypatch.setitem(sys.modules, "hid", module)
    monkeypatch.setattr(devices, "hid", module)
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
