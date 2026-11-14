"""Config search-order pins (estate WTD plan Phase B unit B1, review Item 5).

The approved order: an explicit --config first, then a user.cfg in the current
folder ONLY when one exists (so an existing portable install is never broken),
then the per-user lane %APPDATA%/wtd/user.cfg. Resolution happens once, in
main(), through resolve_config_paths - the single lane these pins exercise.

Device-free: no HID, no GUI, no live injection. The ladder is driven through the
filesystem probes resolve_config_paths exposes, so no lane reads or writes a real
profile (tests/conftest.py also isolates %APPDATA% for the whole session). The
one subprocess pin is about import time, which only a fresh interpreter can show.

Discriminator labels per docstring, so a reader knows which lanes proved red on
the pre-B1 tree and which lock a contract that already held (estate convention:
a regression guard is stated as such, never counted as proof of a fix).
"""

import json
import os
import subprocess
import sys

import pytest

from touch_controller import (
    APPDATA_DIR_NAME,
    CONFIG_FILE_PATH,
    DriverConfig,
    appdata_config_path,
    resolve_config_paths,
)

APPDATA = "P:/profile/wtd/user.cfg"


def resolve(args, present=(), mtimes=None, appdata=APPDATA):
    """Run the single lane over a stubbed filesystem.

    Stamps default to one shared value, so a pin that says nothing about
    modification times gets the identical-stamp case (no migration notice) and
    only the notice pin sets differing values.
    """
    seen = set(present)
    stamps = mtimes or {}
    return resolve_config_paths(args,
                                path_exists=lambda path: path in seen,
                                path_mtime=lambda path: stamps.get(path, 0.0),
                                appdata_path=appdata)


def test_explicit_config_wins_over_both_default_lanes():
    """Order step 1. Regression guard: this already held before B1.

    A named file beats both a cwd config and the per-user file, and --save-as
    omitted follows it. No migration notice: the user said which file to read.
    """
    load, save, notes, other = resolve(["--config", "chosen.cfg"],
                                       present=("user.cfg", APPDATA))
    assert load == "chosen.cfg"
    assert save == "chosen.cfg"
    assert notes == []
    assert other == []


def test_existing_cwd_config_wins_over_the_per_user_lane():
    """Order step 2. Regression guard for the never-break-installs rule.

    With both files live, the cwd file is the one loaded - that precedence is
    what keeps an existing portable install reading its own file after B1 added
    the fallback. The pair with test_fresh_install_falls_back_to_the_per_user_lane
    is what the B1 change actually moved.
    """
    load, save, notes, _ = resolve([], present=("user.cfg", APPDATA))
    assert load == CONFIG_FILE_PATH
    assert save == CONFIG_FILE_PATH
    assert load != APPDATA


def test_fresh_install_falls_back_to_the_per_user_lane():
    """Order step 3. DISCRIMINATOR: pre-B1 this returned the bare 'user.cfg'.

    Nothing in the current folder: the per-user lane is chosen for both load and
    save, and there is nothing to migrate, so no notice.
    """
    load, save, notes, _ = resolve([], present=())
    assert load == APPDATA
    assert save == APPDATA
    assert notes == []


def test_appdata_lane_derives_from_the_profile_dir(monkeypatch, tmp_path):
    """DISCRIMINATOR: the lane did not exist before B1 (its red evidence is the
    pre-B1 import failure - the symbol is new, so this pin cannot even run).

    %APPDATA% drives it, and a missing %APPDATA% (a non-Windows host) still gets
    a per-user path from the home directory instead of a crash.
    """
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert appdata_config_path() == os.path.join(str(tmp_path), APPDATA_DIR_NAME,
                                                 CONFIG_FILE_PATH)
    derived = appdata_config_path(env={})
    assert derived.endswith(os.path.join(APPDATA_DIR_NAME, CONFIG_FILE_PATH))
    assert derived.startswith(os.path.expanduser("~"))


def test_first_save_creates_the_missing_profile_folder(tmp_path):
    """DISCRIMINATOR: pre-B1 a save into a folder that does not exist failed.

    A fresh install picks the per-user lane, whose folder arrives with nothing in
    it, so the first save has to create it rather than raise.
    """
    target = tmp_path / "wtd" / CONFIG_FILE_PATH
    cfg = DriverConfig()
    cfg.save_path = str(target)
    cfg.save_to_file()
    assert target.exists()
    assert json.loads(target.read_text())["motion_and_thresholds"]


def test_importing_the_product_reads_no_config_file(tmp_path):
    """No file I/O at import (Item 5 / B1). DISCRIMINATOR.

    Importing the product module used to construct DriverConfig() at module
    scope, which printed the loader's verdict - "[Config] No 'user.cfg' found..."
    with no file, "[Config Error] Failed loading..." with an unreadable one - in
    every process that merely imported the module, including tests and CLI lanes.
    A subprocess is the only honest probe: the pytest process already imported it.
    """
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    code = (
        "import contextlib, io\n"
        "buf = io.StringIO()\n"
        "with contextlib.redirect_stdout(buf):\n"
        "    import touch_controller\n"
        "print('IMPORT_OUT<' + buf.getvalue() + '>')\n"
        "print('LANE', touch_controller.config.config_path)\n"
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = root + os.pathsep + env.get("PYTHONPATH", "")

    for label, content in (("no config file", None), ("unreadable config file", "{not json")):
        workdir = tmp_path / label.replace(" ", "_")
        workdir.mkdir()
        if content is not None:
            (workdir / CONFIG_FILE_PATH).write_text(content)
        proc = subprocess.run([sys.executable, "-c", code], cwd=str(workdir), env=env,
                              capture_output=True, text=True, timeout=120)
        assert proc.returncode == 0, proc.stderr
        out = proc.stdout
        assert "IMPORT_OUT<>" in out, f"{label}: import printed config noise: {out}"
        assert f"LANE {CONFIG_FILE_PATH}" in out, (
            f"{label}: the import-time global is not the defaults object: {out}")


def test_migration_notice_only_for_two_live_files_that_differ():
    """DISCRIMINATOR: the notice did not exist before B1.

    Two live copies with different modification times is the case the user can
    not see from the outside, so the start says it once: the current folder wins
    and nothing was moved or copied. Identical stamps, or only one live file,
    have nothing to report.
    """
    present = ("user.cfg", APPDATA)

    _, _, notes, _ = resolve([], present=present,
                             mtimes={"user.cfg": 100.0, APPDATA: 200.0})
    assert len(notes) == 1, notes
    text = notes[0]
    assert text.startswith("[Config Migration]")
    assert APPDATA in text
    assert "current folder wins" in text
    assert "nothing was moved or copied" in text

    same, _, quiet_same, _ = resolve([], present=present,
                                     mtimes={"user.cfg": 100.0, APPDATA: 100.0})
    assert quiet_same == []
    assert same == CONFIG_FILE_PATH

    alone, _, quiet_alone, _ = resolve([], present=("user.cfg",),
                                       mtimes={"user.cfg": 100.0})
    assert quiet_alone == []
    assert alone == CONFIG_FILE_PATH
