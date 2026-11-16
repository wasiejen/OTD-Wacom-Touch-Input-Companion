"""Single-instance guard pins (maintainer decision 2026-11-16, X-SINGLE,
WTD mirror of the shipped FST guard): one WTD instance at a time - a
second start logs one notice line and exits 1.

Pinned lanes: real in-process double acquire (kernel 'first' then
'already_running' - a named mutex is inert, no device, no window and no
prompt; the maintainer instruction names this pin and overrides the
device-free test line for it), the Global-scoped mutex name, the refusal
(byte-exact stream line via capsys + SystemExit 1), api_error fail-open
(NULL handle and raising seam both continue), the
WTD_ALLOW_MULTI_INSTANCE test seam, and the main() wiring order pins
(elevation relaunch exits 0 with the guard NEVER reached; refusal exits
before DriverConfig; an elevated continuation still reaches the guard).

Discriminator: every pin is red on the pre-guard tree (R1: the module
and the cli symbol did not exist - the wiring pins fail with
AttributeError on the HEAD copy, verified 2026-11-16).
"""

import ctypes
import os
import sys

import pytest

from wtd import cli
from wtd import single_instance as si

BS = chr(92)
NOTICE = "[SingleInstance] another WTD is already running - exiting"
FAKE_EXE = "P:/build/OTD_Wacom_Touch_Driver.exe"


def tripwire(*args, **kwargs):
    raise AssertionError("this seam must not be reached in this test")


class TestRealAcquire:
    """Real kernel32 acquisition with a unique test-scope name."""

    def _close(self, handle):
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)

    def test_double_acquire_in_process_refuses_second(self):
        name = "Global" + BS + "wtd_si_test_double_" + str(os.getpid())
        try:
            assert si.acquire_single_instance_guard(name) == "first"
            assert si._mutex_handle  # handle kept for the process life
            assert si.acquire_single_instance_guard(name) == "already_running"
        finally:
            self._close(si._mutex_handle)
            si._mutex_handle = None

    def test_mutex_name_is_global_scoped(self):
        assert si.MUTEX_NAME == "Global" + BS + "wtd_otd_wacom_touch_driver"


@pytest.fixture()
def seam(monkeypatch):
    """Patch the raw CreateMutexW seam; return {calls, state}."""
    rec = {"calls": []}
    state = {"handle": 1234, "err": 0, "raises": None}

    def fake(name):
        rec["calls"].append(name)
        if state["raises"] is not None:
            raise state["raises"]
        return state["handle"], state["err"]

    monkeypatch.setattr(si, "_kernel32_create_mutexw", fake)
    monkeypatch.delenv("WTD_ALLOW_MULTI_INSTANCE", raising=False)
    si._mutex_handle = None
    rec["state"] = state
    return rec


class TestRefusalPath:
    def test_first_acquire_keeps_handle_and_continues(self, seam):
        si.handle_single_instance_guard()
        assert seam["calls"] == [si.MUTEX_NAME]
        assert si._mutex_handle == 1234

    def test_second_instance_exits_1_with_one_notice(self, seam, capsys):
        seam["state"]["handle"] = 999  # ALREADY_EXISTS still hands a handle
        seam["state"]["err"] = si.ERROR_ALREADY_EXISTS
        with pytest.raises(SystemExit) as exc:
            si.handle_single_instance_guard()
        assert exc.value.code == 1  # documented startup-error code, calm
        out = capsys.readouterr().out
        assert out.count(NOTICE) == 1  # ONE line, byte-exact, stream sink
        assert "another WTD is already running" in out

    def test_null_handle_fails_open(self, seam, capsys):
        seam["state"]["handle"] = None  # API failure, not a refusal
        seam["state"]["err"] = 5
        si.handle_single_instance_guard()  # NO exit - fail open
        out = capsys.readouterr().out
        assert "CreateMutexW failed" in out
        assert "GetLastError=5" in out

    def test_raising_seam_fails_open(self, seam, capsys):
        seam["state"]["raises"] = OSError("no kernel32 here")
        si.handle_single_instance_guard()  # NO exit - fail open
        out = capsys.readouterr().out
        assert "mutex API failed" in out
        assert seam["calls"] == [si.MUTEX_NAME]

    def test_env_seam_skips_guard(self, seam, monkeypatch):
        monkeypatch.setenv("WTD_ALLOW_MULTI_INSTANCE", "1")
        si.handle_single_instance_guard()
        assert seam["calls"] == []  # test seam: no acquire attempt at all


class TestMainWiring:
    """main() order pins: the guard runs after the --elevated lane and
    before the config ladder, the CLI lanes, and any device or window
    init (all patched below the tripwire)."""

    def _patch_guard_spy(self, monkeypatch, code):
        calls = []

        def spy():
            calls.append(True)
            raise SystemExit(code)  # exit shape of the real handler

        monkeypatch.setattr(cli, "handle_single_instance_guard", spy)
        monkeypatch.setattr(cli, "DriverConfig", tripwire)
        return calls

    def _patch_frozen_not_elevated(self, monkeypatch, relaunch_calls,
                                   result=101):
        """Frozen exe, not elevated: the lane relaunches once and exits 0."""
        monkeypatch.setattr(cli, "_shell32_is_user_an_admin", lambda: False)
        monkeypatch.setattr(cli, "_is_dev_python", lambda: False)
        monkeypatch.setattr(sys, "executable", FAKE_EXE)

        def fake_relaunch(exe_path, parameters, work_dir):
            relaunch_calls.append((exe_path, parameters, work_dir))
            return result

        monkeypatch.setattr(cli, "_shell32_runas_relaunch", fake_relaunch)

    def test_elevation_relaunch_exits_before_guard(self, monkeypatch):
        relaunch_calls = []
        self._patch_frozen_not_elevated(monkeypatch, relaunch_calls)
        monkeypatch.setattr(sys, "argv", ["driver.py", "--elevated"])
        # The REAL lane runs; the guard must never be reached by the
        # launcher (race-free order: no acquire in front of the child).
        monkeypatch.setattr(si, "_kernel32_create_mutexw", tripwire)
        with pytest.raises(SystemExit) as exc:
            cli.main()
        assert exc.value.code == 0
        assert len(relaunch_calls) == 1  # exactly one runas relaunch

    def test_refusal_exits_before_config_read(self, monkeypatch):
        calls = self._patch_guard_spy(monkeypatch, 1)
        monkeypatch.setattr(sys, "argv", ["driver.py"])
        with pytest.raises(SystemExit) as exc:
            cli.main()
        assert exc.value.code == 1
        assert calls == [True]  # guard ran; DriverConfig tripwire held

    def test_guard_runs_without_flag(self, monkeypatch):
        calls = self._patch_guard_spy(monkeypatch, 42)
        monkeypatch.setattr(sys, "argv", ["driver.py"])
        with pytest.raises(SystemExit) as exc:
            cli.main()
        assert exc.value.code == 42
        assert calls == [True]

    def test_elevated_continuation_still_reaches_guard(self, monkeypatch,
                                                       capsys):
        # already elevated: the lane logs one line and continues - the
        # worker (elevated run) is the process that acquires.
        monkeypatch.setattr(cli, "_shell32_is_user_an_admin", lambda: True)
        monkeypatch.setattr(sys, "argv", ["driver.py", "--elevated"])
        calls = self._patch_guard_spy(monkeypatch, 42)
        with pytest.raises(SystemExit) as exc:
            cli.main()
        assert exc.value.code == 42
        assert calls == [True]
        # the lane's continuation line landed before the guard exit
        assert "[Elevation] running elevated" in capsys.readouterr().out
