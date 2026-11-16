"""--elevated startup lane pins (maintainer decision 2026-11-16, WTD mirror
of the shipped FST --elevate).

Device-free and prompt-free: every win32 entry point of the lane is one of
the module-level ctypes seams in wtd/cli.py, patched here. No real UAC
prompt, no relaunch, and no live shell32 call happens in this file.

Discriminator labels: every pin is red on the pre-lane tree (R1: the lane
surface is new - the parser rejected --elevated/-elevated as an unknown
flag and the lane symbols did not exist yet; verified before the
implementation landed).
"""

import ctypes
import logging
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from wtd import cli
from wtd.cli import (ELEVATE_DEV_MESSAGE, handle_elevated_flag, parse_start_args)

RUN_ELEVATED_LINE = "[Elevation] running elevated"
RELAUNCH_LINE = "[Elevation] relaunching elevated via runas"
# The polish unit (2026-11-16) names the SE_ERR_* code inside the shipped
# sentence, so the log says WHICH failure happened; the numeric phrase stays
# because the shipped lane pins it. Byte literal (FST mirror).
FAILURE_LINE = ("[Elevation] runas relaunch returned 5 "
                "(SE_ERR_ACCESSDENIED) - no elevated instance started; "
                "launch the exe as administrator manually if none appeared")


def tripwire(*args):
    raise AssertionError("this seam must not be reached in this test")


def patch_frozen_not_elevated(monkeypatch, calls, result=101):
    """Pin the lane to a frozen, not-yet-elevated run recording each relaunch.

    The relaunch seam records (exe, parameters, work_dir) per call and
    returns ``result`` (101 = a healthy HINSTANCE, 5 = a declined UAC prompt).
    """
    monkeypatch.setattr(cli, "_shell32_is_user_an_admin", lambda: False)
    monkeypatch.setattr(cli, "_is_dev_python", lambda: False)
    monkeypatch.setattr(sys, "executable", "P:/build/OTD_Wacom_Touch_Driver.exe")

    def fake_relaunch(exe_path, parameters, work_dir):
        calls.append((exe_path, parameters, work_dir))
        return result

    monkeypatch.setattr(cli, "_shell32_runas_relaunch", fake_relaunch)


# ---------------------------------------------------------------------------
# parser surface: both spellings parse (the WTD convention: --double is the
# canonical form, -single folds in through START_FLAG_FOLD).
# ---------------------------------------------------------------------------

def test_parser_accepts_both_spellings_and_defaults_off():
    assert parse_start_args([])[0].elevated is False
    assert parse_start_args(["--elevated"])[0].elevated is True
    assert parse_start_args(["-elevated"])[0].elevated is True
    assert parse_start_args(["-ELEVATED"])[0].elevated is True


def test_parser_keeps_the_bool_value_rule_for_the_new_flag():
    # Same pre-B2 reading as '--debug=x': the flag is on, the value is
    # ignored, and the note names the spelling the user typed.
    namespace, notes = parse_start_args(["--elevated=yes"])
    assert namespace.elevated is True
    assert notes == ["[Start Parameter] --elevated takes no value - 'yes' ignored"]


# ---------------------------------------------------------------------------
# lane behavior per elevation state.
# ---------------------------------------------------------------------------

def test_no_flag_lane_touches_no_win32_seam(monkeypatch):
    monkeypatch.setattr(cli, "_shell32_is_user_an_admin", tripwire)
    monkeypatch.setattr(cli, "_shell32_runas_relaunch", tripwire)
    assert handle_elevated_flag(["--debug"]) is None


def test_already_elevated_logs_one_line_and_continues(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_shell32_is_user_an_admin", lambda: True)
    monkeypatch.setattr(cli, "_shell32_runas_relaunch", tripwire)
    assert handle_elevated_flag(["--elevated"]) is None
    assert capsys.readouterr().out.count(RUN_ELEVATED_LINE) == 1


def test_not_elevated_dev_run_prints_hint_and_continues(monkeypatch, capsys,
                                                        caplog):
    """The REAL dev-mode detection runs here: this suite lives under
    python.exe, so the lane must surface the hint and never relaunch.

    Polish unit P2 (2026-11-16, FST mirror): the hint routes through the
    shared 'wtd' logger at INFO. The _log.py contract keeps an INFO line on
    the console stream sink exactly where print wrote it (so the capsys
    assertion below IS the console-visibility pin) and mirrors the same line
    to wtd.log (that mirroring leg is pinned by tests/test_log_file_sink.py;
    conftest redirects the file away from the maintainer's live log)."""
    assert cli._is_dev_python() is True
    monkeypatch.setattr(cli, "_shell32_is_user_an_admin", lambda: False)
    monkeypatch.setattr(cli, "_shell32_runas_relaunch", tripwire)
    with caplog.at_level(logging.INFO, logger="wtd"):
        assert handle_elevated_flag(["-elevated", "--wireless"]) is None
    assert ELEVATE_DEV_MESSAGE in capsys.readouterr().out
    hints = [record for record in caplog.records
             if record.getMessage() == ELEVATE_DEV_MESSAGE]
    assert len(hints) == 1
    # INFO is the level the wtd_stream sink passes (logger level INFO): the
    # route that keeps the line console-visible AND lands it in wtd.log.
    assert hints[0].levelno == logging.INFO
    assert hints[0].name == "wtd"


def test_not_elevated_frozen_relaunches_once_with_stripped_args(monkeypatch,
                                                                 capsys):
    calls = []
    patch_frozen_not_elevated(monkeypatch, calls)
    argv = ["--config", "C:/path with space/user.cfg", "--elevated", "--debug"]

    with pytest.raises(SystemExit) as excinfo:
        handle_elevated_flag(argv)

    assert excinfo.value.code == 0
    assert len(calls) == 1
    exe, parameters, work_dir = calls[0]
    assert exe == "P:/build/OTD_Wacom_Touch_Driver.exe"
    # CRT-safe quoting (subprocess.list2cmdline), the flag stripped, the
    # other args in their original order.
    assert parameters == subprocess.list2cmdline(
        ["--config", "C:/path with space/user.cfg", "--debug"])
    assert work_dir == os.getcwd()
    assert RELAUNCH_LINE in capsys.readouterr().out


def test_relaunch_args_never_carry_either_spelling_loop_guard(monkeypatch):
    # Loop guard: EVERY accepted spelling -- both dashes, any case, the
    # inline-value form -- is out of the child's command line, so the
    # relaunched process can never relaunch again.
    calls = []
    patch_frozen_not_elevated(monkeypatch, calls)
    argv = ["--elevated=on", "-Elevated", "--wireless", "-elevated"]

    with pytest.raises(SystemExit):
        handle_elevated_flag(argv)

    assert len(calls) == 1
    parameters = calls[0][1]
    assert "--elevated" not in parameters.lower()
    assert "-elevated" not in parameters.lower()
    assert parameters == subprocess.list2cmdline(["--wireless"])


def test_relaunch_failure_warns_and_still_exits_zero(monkeypatch, capsys):
    # SE_ERR_* (5 = the declined UAC prompt): exit stays 0 per spec, but
    # the failure gets a named warning line.
    calls = []
    patch_frozen_not_elevated(monkeypatch, calls, result=5)

    with pytest.raises(SystemExit) as excinfo:
        handle_elevated_flag(["--elevated"])

    assert excinfo.value.code == 0
    assert len(calls) == 1
    out = capsys.readouterr().out
    assert FAILURE_LINE in out
    # failure-mapping pin (polish 2026-11-16): the diagnostic names the
    # SE_ERR_* code, and the run exits 0 (no crash, the product is unharmed)
    assert "(SE_ERR_ACCESSDENIED)" in out


# ---------------------------------------------------------------------------
# the raw ctypes seam (cross-product polish 2026-11-16, FST mirror): the
# lane pins patch the seam away, so these drive it against a fake windll -
# no real shell32 call happens here.
# ---------------------------------------------------------------------------

def test_runas_seam_sets_void_pointer_restype_before_the_call(monkeypatch):
    """ShellExecuteW returns an HINSTANCE-cast INT_PTR (shellapi.h: "can be
    cast only to an INT_PTR and compared to either 32 or the error codes"),
    so ctypes' default c_int truncates the high dword on x64 and a healthy
    relaunch could read <= 32 = a false 'no elevated instance started'
    warning. The seam pins restype = c_void_p BEFORE the call and maps a
    NULL (None) return to 0 = the failure band.

    Discriminator: the shipped seam never touches restype, so the fake
    records 'ctypes-default' there = RED before the fix."""
    recorded = {}

    class FakeShellExecute:
        restype = "ctypes-default"

        def __call__(self, *args):
            recorded["restype_at_call"] = self.restype
            recorded["args"] = args
            return None  # NULL HINSTANCE -> the seam maps it to 0

    fake_shell32 = SimpleNamespace(ShellExecuteW=FakeShellExecute())
    monkeypatch.setattr(ctypes, "windll",
                        SimpleNamespace(shell32=fake_shell32), raising=False)

    assert cli._shell32_runas_relaunch("P:/build/x.exe", "--wireless",
                                       "P:/wd") == 0
    assert recorded["restype_at_call"] is ctypes.c_void_p
    assert recorded["args"] == (None, "runas", "P:/build/x.exe", "--wireless",
                                "P:/wd", 1)


def test_se_err_name_labels_the_sdk_table_and_falls_back_honestly():
    """The failure diagnostic reads the SDK table (SE_ERR_* from shellapi.h,
    ERROR_BAD_FORMAT from winerror.h); a code outside it keeps its number
    instead of inventing a symbol."""
    assert cli._se_err_name(5) == "SE_ERR_ACCESSDENIED"
    assert cli._se_err_name(2) == "SE_ERR_FNF"
    assert cli._se_err_name(11) == "ERROR_BAD_FORMAT"
    assert cli._se_err_name(32) == "SE_ERR_DLLNOTFOUND"
    assert cli._se_err_name(0) == "code 0"
    assert cli._se_err_name(17) == "code 17"


def test_admin_check_failure_is_named_and_treated_as_not_elevated(monkeypatch,
                                                                   capsys):
    def boom():
        raise OSError("win32 unavailable")

    monkeypatch.setattr(cli, "_shell32_is_user_an_admin", boom)
    monkeypatch.setattr(cli, "_shell32_runas_relaunch", tripwire)
    # Dev mode (real python.exe) continues after the honest "not elevated"
    # reading: no relaunch, the hint prints, and the failure is named.
    assert handle_elevated_flag(["--elevated"]) is None
    out = capsys.readouterr().out
    assert "IsUserAnAdmin failed" in out
    assert ELEVATE_DEV_MESSAGE in out


# ---------------------------------------------------------------------------
# main() integration: the lane runs right after the strict parse and before
# any config read, device, or window init.
# ---------------------------------------------------------------------------

def test_main_relane_exits_before_config_and_device_init(monkeypatch, tmp_path,
                                                         capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["prog", "-elevated", "--list"])
    calls = []
    patch_frozen_not_elevated(monkeypatch, calls)

    def config_tripwire(config):
        raise AssertionError("the relaunch must exit before any config lane")

    monkeypatch.setattr(cli, "cli_list_devices", config_tripwire)

    with pytest.raises(SystemExit) as excinfo:
        cli.main()

    assert excinfo.value.code == 0
    assert len(calls) == 1
    out = capsys.readouterr().out
    assert RELAUNCH_LINE in out
