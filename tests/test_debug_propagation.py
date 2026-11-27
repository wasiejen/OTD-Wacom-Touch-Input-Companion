"""--debug flips the runtime-shared DEBUG that every gate module reads.

C1 reviewer P1-1: after the split, gesture/sender/reader gates read
wtd.config.DEBUG at call time; this pins the cli lane to flip THAT shared
global (a cli-local rebinding would leave the other lanes silent).
"""

import sys
import time

from wtd import cli
from wtd import config as wtd_config


class _BoomManager:
    def __init__(self, *args, **kwargs):
        raise RuntimeError("stop after the debug rebind")


def test_main_debug_flag_flips_the_shared_global(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(wtd_config, "DEBUG", False)
    monkeypatch.setattr(sys, "argv", ["touch_controller.py", "--debug"])
    monkeypatch.setattr(time, "sleep", lambda s: None)
    monkeypatch.setattr(cli, "DeviceManagerThread", _BoomManager)
    monkeypatch.chdir(tmp_path)
    capsys.readouterr()
    try:
        cli.main()
    except Exception:
        pass  # stubbed-lane failure after the rebind is irrelevant here
    out = capsys.readouterr().out
    assert "DEBUG active" in out
    assert wtd_config.DEBUG is True
