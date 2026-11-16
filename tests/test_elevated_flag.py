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

import os
import subprocess
import sys

import pytest

from wtd import cli
from wtd.cli import (ELEVATE_DEV_MESSAGE, handle_elevated_flag, parse_start_args)

RUN_ELEVATED_LINE = "[Elevation] running elevated"
RELAUNCH_LINE = "[Elevation] relaunching elevated via runas"
FAILURE_LINE = ("[Elevation] runas relaunch returned 5 - no elevated "
                "instance started; launch the exe as administrator manually "
                "if none appeared")


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


def test_not_elevated_dev_run_prints_hint_and_continues(monkeypatch, capsys):
    # The REAL dev-mode detection runs here: this suite lives under
    # python.exe, so the lane must print the hint and never relaunch.
    assert cli._is_dev_python() is True
    monkeypatch.setattr(cli, "_shell32_is_user_an_admin", lambda: False)
    monkeypatch.setattr(cli, "_shell32_runas_relaunch", tripwire)
    assert handle_elevated_flag(["-elevated", "--wireless"]) is None
    assert ELEVATE_DEV_MESSAGE in capsys.readouterr().out


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
    assert FAILURE_LINE in capsys.readouterr().out


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
