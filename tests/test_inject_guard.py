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
import pathlib
import subprocess
import sys
import threading

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


# ==============================================================================
# U369 hardening pins (TODO #354): the four edges the U354 reviewer disclosed.
# One pin per edge. Every probe uses SendInput with cInputs=0: even where the
# probe would reach the real API, count 0 injects no desktop event, so both
# the pre-change (RED) and post-change (GREEN) runs stay live-injection-free.
# ==============================================================================


def test_fresh_dll_instances_resolve_to_the_guard():
    """Edge 1: fresh CDLL/WinDLL/oledll user32 instances hit the guard too.

    Pre-change (RED direction): a fresh instance resolves the real SendInput
    and the probe succeeds unguarded -- pytest.raises reports DID NOT RAISE.
    Post-change: every fresh-instance SendInput raises, counted as a
    selfcheck hit, and no offender is recorded.
    """
    saved_hits = list(conftest.selfcheck_hits)
    saved_offenders = list(conftest.inject_offenders)
    try:
        with conftest.inject_guard_selfcheck():
            for lib in (ctypes.CDLL("user32"), ctypes.WinDLL("user32"),
                        ctypes.oledll.user32):
                with pytest.raises(RuntimeError, match="LIVE INJECTION BLOCKED"):
                    lib.SendInput(0, None, 0)
            # Blast-radius control: unrelated APIs still resolve on instances.
            assert ctypes.CDLL("user32").GetForegroundWindow is not None
        assert conftest.selfcheck_hits[len(saved_hits):] == ["SendInput"] * 3
        assert conftest.inject_offenders == saved_offenders
    finally:
        conftest.selfcheck_hits[:] = saved_hits
        conftest.inject_offenders[:] = saved_offenders


def test_caller_chain_shows_third_party_frames():
    """Edge 2: the chain keeps third-party frames and filters pytest ones.

    Pre-change (RED direction): the site-packages filter drops the fake
    third-party frame, so its basename is absent from the block message.
    Post-change: the frame is shown and the filter rule itself pins that
    pytest machinery frames (_pytest, pytest, pluggy) stay filtered.
    """
    source = "def call(api):\n    api(0, None, 0)\n"
    code = compile(source, "/fake/site-packages/thirdlib/_display.py", "exec")
    namespace = {}
    exec(code, namespace)

    with conftest.inject_guard_selfcheck():
        with pytest.raises(RuntimeError) as excinfo:
            namespace["call"](ctypes.windll.user32.SendInput)

    message = str(excinfo.value)
    assert "_display.py" in message
    assert conftest._is_pytest_frame("/x/site-packages/_pytest/runner.py")
    assert conftest._is_pytest_frame("/x/site-packages/pytest/__init__.py")
    assert conftest._is_pytest_frame("/x/site-packages/pluggy/_hooks.py")
    assert not conftest._is_pytest_frame("/x/site-packages/pystray/_base.py")


def test_off_thread_injection_during_selfcheck_records_offender():
    """Edge 3: only the selfcheck window's own thread counts as selfcheck.

    Pre-change (RED direction): the flag is process-global, so the daemon
    thread's call lands in selfcheck_hits and no offender is recorded.
    Post-change: the call still raises, but it is recorded as an offender.
    """
    saved_hits = list(conftest.selfcheck_hits)
    saved_offenders = list(conftest.inject_offenders)
    box = {}

    def worker():
        try:
            ctypes.windll.user32.SendInput(0, None, 0)
        except RuntimeError as exc:
            box["message"] = str(exc)

    with conftest.inject_guard_selfcheck():
        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        thread.join(5.0)

    new_offenders = conftest.inject_offenders[len(saved_offenders):]
    try:
        assert "LIVE INJECTION BLOCKED" in box.get("message", "")
        assert [entry[0] for entry in new_offenders] == ["SendInput"]
        assert conftest.selfcheck_hits[len(saved_hits):] == []
    finally:
        conftest.inject_offenders[:] = saved_offenders
        conftest.selfcheck_hits[:] = saved_hits


def test_post_teardown_injection_reported_at_session_end():
    """Edge 4: atexit-time injection gets teardown attribution and a report.

    Runs a tiny probe session in a subprocess whose test registers an atexit
    injection. Pre-change (RED direction): the atexit call raises unattributed
    (nodeid = last test) and never surfaces in any session-end report -- the
    report token and the "<teardown time>" attribution are both absent.
    Post-change: conftest resets the active nodeid on teardown and its
    atexit handler reports the post-session offender and exits nonzero.
    """
    probe = pathlib.Path(__file__).resolve().parent / "u369_post_session_probe_tmp.py"
    probe.write_text(
        "import atexit\n"
        "import ctypes\n"
        "\n"
        "def test_register_late_injection():\n"
        "    def _late():\n"
        "        ctypes.windll.user32.SendInput(0, None, 0)\n"
        "    atexit.register(_late)\n",
        encoding="utf-8",
    )
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", str(probe), "-q", "-p", "no:cacheprovider"],
            capture_output=True,
            text=True,
            cwd=str(probe.parent.parent),
            timeout=180,
        )
        combined = proc.stdout + proc.stderr
    finally:
        probe.unlink(missing_ok=True)

    assert proc.returncode != 0
    assert "POST-SESSION LIVE INJECTION REPORT" in combined
    assert "<teardown time>" in combined
    assert not probe.exists()
