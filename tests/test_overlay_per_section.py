"""WTD-14 (decision 14, maintainer 2026-11-17): per-section overlay
acceptance + best-effort line-number feedback.

One stale/bad action key in a device overlay no longer poisons the whole
entry: valid sections APPLY, the offending section stays out atomically
and is named. All sections bad reproduces the old whole-reject line.
Line references ('line N') name the offending key's line in the SAVED
file when the entry was loaded from that file (the same best-effort,
file-relative class the load path always had, now scoped to the lane's
own block); an entry that exists only in memory degrades to 'item N'
(this class is NOT cheaply trackable - fork cannot know the future file
line of an unsaved in-memory value, and inventing one would lie).
"""

import json
import re

import re  # noqa: E402  (regex-boundary line-ref asserts)

from wtd.config import (DEVICE_SETTINGS_PREFIX, DriverConfig,
                        device_identity_hash)

WACOM_PATH = b"ACM&W87&0&MI_00"
OTHER_PATH = b"ACM&W87&0&MI_10"

BAD_ACTION = "definitely_not_an_action"


def fresh_config(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    return DriverConfig()


def good_motion():
    return {"cursor_speed_low": 7.0, "max_tap_duration": 0.4}


def entry_with_bad_action():
    # The original filing's shape: good motion+toggle sections, one stale
    # action value in action_mapping.
    return {
        "motion_and_thresholds": good_motion(),
        "feature_toggles": {"2f_tap": True},
        "action_mapping": {"1f_tap": BAD_ACTION},
    }


# -- the acceptance criterion from the filing --------------------------------

def test_one_bad_action_key_leaves_motion_and_toggle_sections_applied(
        monkeypatch, tmp_path, capsys):
    cfg = fresh_config(monkeypatch, tmp_path)
    base = DriverConfig()
    cfg.devices = {"tablet-a": entry_with_bad_action()}

    fork = cfg.fork_for_device("tablet-a")
    out = capsys.readouterr().out

    # Valid sections applied:
    assert fork.cursor_speed_low == 7.0
    assert fork.max_tap_duration == 0.4
    assert fork.feature_toggles["2f_tap"] is True
    # Bad section stayed out atomically - the mapping keeps base values:
    assert fork.action_mapping == base.action_mapping
    # Named, actionable error: section + key + what is wrong:
    assert "Invalid devices entry 'tablet-a'" in out
    # (_describe names the value type, hence "str '...'")
    assert f"'action_mapping.1f_tap' maps to unknown action str '{BAD_ACTION}'" in out
    assert "'action_mapping' not applied; 2 valid section(s) applied" in out
    assert "Overlay for 'tablet-a' not applied" not in out


def test_bad_section_never_partially_applies(monkeypatch, tmp_path, capsys):
    # Within-section atomicity: max_tap_duration is VALID but sits in the
    # same motion section as a bad value - the whole section stays out.
    cfg = fresh_config(monkeypatch, tmp_path)
    cfg.devices = {"tablet-a": {
        "motion_and_thresholds": {"max_tap_duration": 0.4,
                                  "cursor_speed_low": "abc"},
    }}

    fork = cfg.fork_for_device("tablet-a")
    out = capsys.readouterr().out

    base = DriverConfig()
    assert fork.max_tap_duration == base.max_tap_duration
    assert fork.cursor_speed_low == base.cursor_speed_low
    assert "'motion_and_thresholds' not applied; 0 valid section" not in out
    # Only one section exists and it is bad -> the all-bad whole-reject line:
    assert "Overlay for 'tablet-a' not applied; previous settings stay." in out


def test_all_sections_bad_behaves_like_the_old_whole_reject(
        monkeypatch, tmp_path, capsys):
    cfg = fresh_config(monkeypatch, tmp_path)
    base = DriverConfig()
    cfg.devices = {"tablet-a": {
        "motion_and_thresholds": {"cursor_speed_low": "abc"},
        "feature_toggles": {"2f_tap": "yes-please"},
        "action_mapping": {"1f_tap": BAD_ACTION},
    }}

    fork = cfg.fork_for_device("tablet-a")
    out = capsys.readouterr().out

    assert fork.cursor_speed_low == base.cursor_speed_low
    assert fork.feature_toggles == base.feature_toggles
    assert fork.action_mapping == base.action_mapping
    # Byte-identical to the pre-WTD-14 summary line (doctrine pin):
    assert "[Config Error] Overlay for 'tablet-a' not applied; " \
           "previous settings stay." in out
    # Every bad section was named with its own error:
    assert "'motion_and_thresholds.cursor_speed_low'" in out
    assert "'feature_toggles.2f_tap'" in out
    assert f"'action_mapping.1f_tap' maps to unknown action" in out


# -- no cross-section / cross-lane contamination -----------------------------

def test_bad_identity_mapping_section_never_touches_the_alias_sections(
        monkeypatch, tmp_path, capsys):
    cfg = fresh_config(monkeypatch, tmp_path)
    base = DriverConfig()
    identity = device_identity_hash(WACOM_PATH)
    cfg.devices = {"tablet-a": {"action_mapping": {"1f_tap": "middle_click"}}}
    cfg.device_settings = {identity: {"action_mapping":
                                      {"1f_tap": BAD_ACTION}}}

    fork = cfg.fork_for_device("tablet-a", identity)
    out = capsys.readouterr().out

    # The good alias mapping applied FIRST, then the bad identity section
    # stayed out - the identity value must NOT have leaked, and the alias
    # value must NOT have been reverted:
    assert fork.action_mapping["1f_tap"] == "middle_click"
    assert fork.action_mapping is not base.action_mapping
    assert f"Invalid identity entry '{identity}'" in out
    assert "'action_mapping' not applied; 0 valid section(s) applied" not in out
    assert f"Overlay for '{identity}' not applied; previous settings stay." in out


# -- line-number feedback: trackable vs not (decision 14 "if easily possible")

def test_line_reference_names_the_bad_keys_line_in_the_saved_file(
        monkeypatch, tmp_path, capsys):
    identity = device_identity_hash(WACOM_PATH)
    cfg = DriverConfig()
    cfg.config_path = str(tmp_path / "user.cfg")
    cfg.device_settings = {identity: entry_with_bad_action()}
    text = json.dumps(
        {f"{DEVICE_SETTINGS_PREFIX}{identity}": entry_with_bad_action()},
        indent=2)
    with open(cfg.config_path, "w") as f:
        f.write(text)

    fork = cfg.fork_for_device(None, identity)
    out = capsys.readouterr().out

    lines = text.splitlines()
    want = [i for i, ln in enumerate(lines, start=1)
            if '"1f_tap"' in ln]
    assert len(want) == 1  # fixture guarantees uniqueness of the needle
    assert re.search(rf"line {want[0]}\b", out), out
    # The good sections still applied despite the rejected one:
    assert fork.cursor_speed_low == 7.0
    assert fork.feature_toggles["2f_tap"] is True


def test_lane_scoping_points_at_the_RIGHT_devices_line(monkeypatch, tmp_path,
                                                       capsys):
    # Two identity lanes carry the SAME bad key text ("1f_tap"): an
    # unscoped whole-file search would name the FIRST device's line for
    # the SECOND device's error (the contamination class). Lane scoping
    # must name lane B's own line.
    id_a = device_identity_hash(WACOM_PATH)
    id_b = device_identity_hash(OTHER_PATH)
    entry_a = {"action_mapping": {"1f_tap": BAD_ACTION}}
    entry_b = {"action_mapping": {"1f_tap": BAD_ACTION,
                                  "2f_tap": BAD_ACTION}}
    text = json.dumps({
        f"{DEVICE_SETTINGS_PREFIX}{id_a}": entry_a,
        f"{DEVICE_SETTINGS_PREFIX}{id_b}": entry_b,
    }, indent=2)
    cfg = DriverConfig()
    cfg.config_path = str(tmp_path / "user.cfg")
    with open(cfg.config_path, "w") as f:
        f.write(text)
    cfg.device_settings = {id_a: entry_a, id_b: entry_b}

    cfg.fork_for_device(None, id_b)
    out = capsys.readouterr().out

    lines = text.splitlines()
    taps = [i for i, ln in enumerate(lines, start=1) if '"1f_tap"' in ln]
    assert len(taps) == 2
    line_a_1f, line_b_1f = taps
    line_b_2f = next(i for i, ln in enumerate(lines, start=1)
                     if '"2f_tap"' in ln)
    assert re.search(rf"line {line_b_1f}\b", out), (
        "lane B's 1f_tap error must name lane B's own line")
    assert not re.search(rf"line {line_a_1f}\b", out), (
        "lane A's line leaked into lane B's error (unscoped match)")
    assert re.search(rf"line {line_b_2f}\b", out)


def test_in_memory_only_entry_degrades_to_item_reference(monkeypatch,
                                                         tmp_path, capsys):
    # The NOT-cheaply-trackable class: the entry was never in the file
    # (Apply Live in-memory write-through before any save) - there is no
    # honest line to name, so the reference degrades to the validator's
    # existing 'item N' form and the pin records that limitation.
    identity = device_identity_hash(WACOM_PATH)
    cfg = fresh_config(monkeypatch, tmp_path)  # no user.cfg written
    cfg.device_settings = {identity: {"action_mapping":
                                      {"1f_tap": BAD_ACTION}}}

    cfg.fork_for_device(None, identity)
    out = capsys.readouterr().out

    assert "line " not in out.split("maps to unknown action")[1][:40]
    assert "(item 1)" in out
    assert f"Overlay for '{identity}' not applied; previous settings stay." in out
