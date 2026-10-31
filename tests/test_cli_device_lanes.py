"""CLI device lanes (U88 Part C): --list, --restore-with, --save-as <alias>.

Device-free: tmp files only, no real device, no GUI window, no driver start.
"""

import json

from touch_controller import (
    DEVICE_SETTINGS_PREFIX,
    DriverConfig,
    cli_list_devices,
    cli_restore_with,
    cli_save_as,
    looks_like_path,
    resolve_cli_lanes,
    resolve_config_paths,
)


def test_list_prints_alias_and_identity_key(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    cfg = DriverConfig()
    cfg.set_device_alias("abcd1234ef00", "Kitchen Intuos")
    cfg.devices["wacom-intuos5-m"] = {"bindings": [789]}

    records = cli_list_devices(cfg)
    out = capsys.readouterr().out

    assert records == [("Kitchen Intuos", "abcd1234ef00"), ("wacom-intuos5-m", None)]
    assert "Stored devices (2)" in out
    assert "Kitchen Intuos\tabcd1234ef00" in out
    assert "wacom-intuos5-m\t-" in out


def test_list_on_empty_config_reports_zero(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    records = cli_list_devices(DriverConfig())

    assert records == []
    assert "Stored devices (0)" in capsys.readouterr().out


def test_restore_with_alias_applies_then_resaves_current(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "user.cfg"
    path.write_text(json.dumps({
        "motion_and_thresholds": {"cursor_speed_low": 5.0},
        "devices": {"kitchen": {
            "motion_and_thresholds": {"cursor_speed_low": 12.0}}},
    }, indent=4))
    cfg = DriverConfig(str(path))
    cfg.save_path = str(path)
    assert cfg.cursor_speed_low == 5.0

    assert cli_restore_with(cfg, "kitchen") is True
    assert cfg.cursor_speed_low == 12.0

    data = json.loads(path.read_text())
    entry = data["devices"]["kitchen"]
    # Option B: the stored entry mirrors the live values after the restore.
    assert entry["motion_and_thresholds"]["cursor_speed_low"] == 12.0
    assert entry["motion_and_thresholds"]["pen_speed"] == cfg.pen_speed
    assert entry["feature_toggles"] == cfg.feature_toggles
    assert entry["action_mapping"] == cfg.action_mapping
    assert "bindings" not in entry
    # The live value is now also the saved global value.
    assert data["motion_and_thresholds"]["cursor_speed_low"] == 12.0


def test_restore_with_identity_key_wins_over_alias(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    identity = "abcd1234ef00"
    cfg = DriverConfig()
    cfg.set_device_alias(identity, "Kitchen Intuos")
    cfg.devices["Kitchen Intuos"] = {
        "motion_and_thresholds": {"cursor_speed_low": 7.0}}
    cfg.set_device_identity_settings(identity, {
        "motion_and_thresholds": {"cursor_speed_low": 11.0}})
    path = tmp_path / "user.cfg"
    cfg.save_to_file(str(path))

    fresh = DriverConfig()
    fresh.save_path = str(path)
    assert fresh.load_from_file(str(path)) is True
    assert cli_restore_with(fresh, identity) is True
    assert fresh.cursor_speed_low == 11.0

    data = json.loads(path.read_text())
    key = f"{DEVICE_SETTINGS_PREFIX}{identity}"
    assert data[key]["motion_and_thresholds"]["cursor_speed_low"] == 11.0


def test_restore_with_named_alias_uses_its_identity_lane(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    identity = "abcd1234ef00"
    cfg = DriverConfig()
    cfg.set_device_alias(identity, "Kitchen Intuos")
    cfg.set_device_identity_settings(identity, {
        "feature_toggles": {"2f_tap": True}})
    path = tmp_path / "user.cfg"
    cfg.save_to_file(str(path))

    fresh = DriverConfig()
    fresh.save_path = str(path)
    assert fresh.load_from_file(str(path)) is True
    assert cli_restore_with(fresh, "Kitchen Intuos") is True
    assert fresh.feature_toggles["2f_tap"] is True

    data = json.loads(path.read_text())
    assert f"{DEVICE_SETTINGS_PREFIX}{identity}" in data


def test_restore_with_invalid_entry_keeps_settings(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    cfg = DriverConfig()
    cfg.devices["kitchen"] = {"motion_and_thresholds": {"cursor_speed_low": "abc"}}
    path = tmp_path / "user.cfg"
    cfg.save_path = str(path)

    assert cli_restore_with(cfg, "kitchen") is False
    assert cfg.cursor_speed_low == 5.0
    assert not path.exists()
    assert "Invalid alias entry" in capsys.readouterr().out


def test_restore_with_unknown_name_applies_nothing(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    cfg = DriverConfig()
    path = tmp_path / "user.cfg"
    cfg.save_path = str(path)

    assert cli_restore_with(cfg, "ghost") is False
    assert "No saved device 'ghost'" in capsys.readouterr().out
    assert not path.exists()


def test_save_as_writes_current_settings_under_new_alias(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    cfg = DriverConfig()
    cfg.cursor_speed_low = 7.0
    cfg.action_mapping["1f_tap"] = "right_click"
    cfg.devices["existing"] = {"bindings": [789]}
    path = tmp_path / "user.cfg"
    cfg.save_path = str(path)

    assert cli_save_as(cfg, "kitchen") is True

    data = json.loads(path.read_text())
    entry = data["devices"]["kitchen"]
    assert entry["motion_and_thresholds"]["cursor_speed_low"] == 7.0
    assert entry["action_mapping"]["1f_tap"] == "right_click"
    assert "bindings" not in entry
    assert data["devices"]["existing"] == {"bindings": [789]}


def test_save_as_overwrites_same_alias_only(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    cfg = DriverConfig()
    cfg.devices["kitchen"] = {"motion_and_thresholds": {"cursor_speed_low": 3.0}}
    cfg.devices["other"] = {"bindings": [791]}
    path = tmp_path / "user.cfg"
    cfg.save_path = str(path)

    assert cli_save_as(cfg, "kitchen") is True

    data = json.loads(path.read_text())
    assert data["devices"]["kitchen"]["motion_and_thresholds"]["cursor_speed_low"] == 5.0
    assert data["devices"]["other"] == {"bindings": [791]}


def test_cli_lanes_pass_path_arguments_through(monkeypatch, tmp_path):
    list_flag, restore, alias, notes, path_args = resolve_cli_lanes(
        ["--list", "--restore-with", "Kitchen", "--config", "base.cfg"])

    assert list_flag is True
    assert restore == "Kitchen"
    assert alias is None
    assert notes == []
    assert path_args == ["--config", "base.cfg"]

    load, save, _, other = resolve_config_paths(path_args)
    assert load == "base.cfg"
    assert save == "base.cfg"
    assert other == []


def test_save_as_path_form_keeps_the_save_path_lane():
    _, _, alias, _, path_args = resolve_cli_lanes(["--save-as", "out.cfg", "-wireless"])

    assert alias is None
    load, save, _, other = resolve_config_paths(path_args)
    assert load == "user.cfg"
    assert save == "out.cfg"
    assert other == ["-wireless"]


def test_save_as_bare_name_is_the_alias_lane():
    _, _, alias, _, path_args = resolve_cli_lanes(["--save-as=kitchen", "-wireless"])

    assert alias == "kitchen"
    assert path_args == ["-wireless"]


def test_restore_with_missing_value_is_noted():
    _, _, _, notes, path_args = resolve_cli_lanes(["--restore-with"])

    assert len(notes) == 1 and "restore-with" in notes[0]
    assert path_args == []


def test_list_flag_leaves_other_flags_for_the_flag_parser():
    list_flag, _, _, _, path_args = resolve_cli_lanes(["--list", "-wireless", "-DEBUG"])

    assert list_flag is True
    assert path_args == ["-wireless", "-DEBUG"]


def test_path_hint_accepts_separators_and_extensions():
    assert looks_like_path("sub/other.cfg") is True
    assert looks_like_path("C:/cfg/base.json") is True
    assert looks_like_path("saved.cfg") is True
    assert looks_like_path("kitchen") is False
    assert looks_like_path("Kitchen Intuos") is False
