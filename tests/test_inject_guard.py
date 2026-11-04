"""Live-injection guard self-checks (U354 / TODO #338).

The guard is installed at conftest import time and replaces every Win32 input
entry point on ctypes.windll.user32 for the whole session. These checks pin
that the replacement is in place and blocks, so the suite can never open the
Windows task-switcher on a live desktop again.

Every call below is intercepted: no real event is injected. The probe calls
run inside the conftest.inject_guard_selfcheck() context, which lets the
guard raise without recording the probe itself as an offender.
"""

import ctypes

import pytest

import conftest


def test_every_inject_api_is_blocked():
    """Each patched entry point raises instead of reaching Win32."""
    before = len(conftest.selfcheck_hits)
    user32 = ctypes.windll.user32

    with conftest.inject_guard_selfcheck():
        for api in conftest.INJECT_APIS:
            with pytest.raises(RuntimeError, match="LIVE INJECTION BLOCKED"):
                getattr(user32, api)()

    assert conftest.selfcheck_hits[before:] == list(conftest.INJECT_APIS)


def test_blocked_call_names_the_api_and_the_active_test():
    """The block message names the offender: api, nodeid, and how to opt in."""
    with conftest.inject_guard_selfcheck():
        with pytest.raises(RuntimeError) as excinfo:
            ctypes.windll.user32.SendInput(1, None, 0)

    message = str(excinfo.value)
    assert "SendInput" in message
    assert "test_blocked_call_names_the_api_and_the_active_test" in message
    assert "stub_send_input" in message


def test_stub_send_input_shadows_the_guard(stub_send_input):
    """The opt-in stub absorbs the call: no raise, no event, no offender."""
    ctypes.windll.user32.SendInput(1, None, 0)

    assert stub_send_input == []
    assert conftest.inject_offenders == []
