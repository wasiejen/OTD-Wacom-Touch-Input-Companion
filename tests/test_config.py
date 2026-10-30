"""Config save/load round-trip tests (TODO #247).

Pure JSON handling: tmp_path files only, no device, no GUI.
"""

import json

from touch_controller import DriverConfig, resolve_config_paths


def fresh_config(monkeypatch, tmp_path):
    """Build a DriverConfig with no user.cfg reachable from cwd."""
    monkeypatch.chdir(tmp_path)
    return DriverConfig()


def test_cursor_speed_keys_round_trip(monkeypatch, tmp_path):
    cfg = fresh_config(monkeypatch, tmp_path)
    cfg.cursor_speed_low = 7.5
    cfg.cursor_speed_high = 90.0
    cfg.scroll_speed_low = 11.0
    cfg.scroll_speed_high = 55.0

    path = tmp_path / "user.cfg"
    cfg.save_to_file(str(path))

    fresh = DriverConfig()
    fresh.load_from_file(str(path))

    assert fresh.cursor_speed_low == 7.5
    assert fresh.cursor_speed_high == 90.0
    assert fresh.scroll_speed_low == 11.0
    assert fresh.scroll_speed_high == 55.0


def test_saved_key_names_match_attribute_names(monkeypatch, tmp_path):
    cfg = fresh_config(monkeypatch, tmp_path)
    path = tmp_path / "user.cfg"
    cfg.save_to_file(str(path))

    data = json.loads(path.read_text())
    motion = data["motion_and_thresholds"]

    assert "cursor_speed_low" in motion
    assert "cursor_speed_high" in motion
    assert "scroll_speed_low" in motion
    assert "scroll_speed_high" in motion
    assert "speed_low" not in motion
    assert "speed_high" not in motion


def test_scroll_acceleration_block_round_trip(monkeypatch, tmp_path):
    cfg = fresh_config(monkeypatch, tmp_path)
    cfg.scroll_deadzone = 1.5
    cfg.scroll_min_sens = 2.0
    cfg.scroll_max_sens = 40.0
    cfg.scroll_speed_low = 9.0
    cfg.scroll_speed_high = 70.0

    path = tmp_path / "user.cfg"
    cfg.save_to_file(str(path))

    fresh = DriverConfig()
    fresh.load_from_file(str(path))

    assert fresh.scroll_deadzone == 1.5
    assert fresh.scroll_min_sens == 2.0
    assert fresh.scroll_max_sens == 40.0
    assert fresh.scroll_speed_low == 9.0
    assert fresh.scroll_speed_high == 70.0


def test_thresholds_round_trip(monkeypatch, tmp_path):
    cfg = fresh_config(monkeypatch, tmp_path)
    cfg.max_tap_duration = 0.4
    cfg.tap_max_movement = 12
    cfg.swipe_threshold_x = 80
    cfg.axis_dominance_ratio = 1.7
    cfg.press_hold_duration = 0.35

    path = tmp_path / "user.cfg"
    cfg.save_to_file(str(path))

    fresh = DriverConfig()
    fresh.load_from_file(str(path))

    assert fresh.max_tap_duration == 0.4
    assert fresh.tap_max_movement == 12
    assert fresh.swipe_threshold_x == 80
    assert fresh.axis_dominance_ratio == 1.7
    assert fresh.press_hold_duration == 0.35


def test_feature_toggles_and_action_mapping_round_trip(monkeypatch, tmp_path):
    cfg = fresh_config(monkeypatch, tmp_path)
    cfg.feature_toggles["2f_tap"] = True
    cfg.feature_toggles["5f_alt_tab"] = False
    cfg.action_mapping["1f_tap"] = "right_click"

    path = tmp_path / "user.cfg"
    cfg.save_to_file(str(path))

    fresh = DriverConfig()
    fresh.load_from_file(str(path))

    assert fresh.feature_toggles["2f_tap"] is True
    assert fresh.feature_toggles["5f_alt_tab"] is False
    assert fresh.action_mapping["1f_tap"] == "right_click"
    assert fresh.action_mapping["2f_tap"] == cfg.action_mapping["2f_tap"]


def test_missing_file_keeps_defaults(monkeypatch, tmp_path):
    cfg = fresh_config(monkeypatch, tmp_path)
    missing = tmp_path / "absent.cfg"

    cfg.load_from_file(str(missing))

    assert cfg.cursor_speed_low == 5.0
    assert cfg.cursor_speed_high == 80.0
    assert cfg.scroll_speed_low == 12.0
    assert cfg.scroll_speed_high == 60.0


def test_sensitivities_rebuilt_from_loaded_values(monkeypatch, tmp_path):
    cfg = fresh_config(monkeypatch, tmp_path)
    cfg.cursor_speed_low = 6.0
    cfg.cursor_speed_high = 99.0
    cfg.scroll_speed_low = 8.0
    cfg.scroll_speed_high = 66.0

    path = tmp_path / "user.cfg"
    cfg.save_to_file(str(path))

    fresh = DriverConfig()
    fresh.load_from_file(str(path))

    assert fresh.sensitivities["cursor_acceleration"][3] == 6.0
    assert fresh.sensitivities["cursor_acceleration"][4] == 99.0
    assert fresh.sensitivities["scroll_acceleration"][3] == 8.0
    assert fresh.sensitivities["scroll_acceleration"][4] == 66.0


def test_sync_sensitivities_mirrors_fields(monkeypatch, tmp_path):
    cfg = fresh_config(monkeypatch, tmp_path)
    cfg.cursor_deadzone = 2.0
    cfg.scroll_sensitivity_y = -1.0
    cfg.sync_sensitivities()

    assert cfg.sensitivities["cursor_acceleration"][0] == 2.0
    assert cfg.sensitivities["scroll_acceleration"][6] == -1.0


def test_unknown_keys_do_not_create_attributes(monkeypatch, tmp_path):
    cfg = fresh_config(monkeypatch, tmp_path)
    path = tmp_path / "user.cfg"
    path.write_text(json.dumps({
        "motion_and_thresholds": {"not_a_setting": 1.0},
        "feature_toggles": {"99f_tap": True},
        "action_mapping": {"99f_tap": "left_click"},
    }))

    cfg.load_from_file(str(path))

    assert not hasattr(cfg, "not_a_setting")
    assert "99f_tap" not in cfg.feature_toggles
    assert "99f_tap" not in cfg.action_mapping


def test_config_option_omitted_keeps_default_lanes(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    load_path, save_path, notes, other = resolve_config_paths(["-DEBUG"])

    assert load_path == "user.cfg"
    assert save_path == "user.cfg"
    assert notes == []
    assert other == ["-DEBUG"]
    assert DriverConfig().config_path == "user.cfg"


def test_cli_config_path_round_trip(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    custom = tmp_path / "custom.cfg"

    cfg = DriverConfig()
    cfg.cursor_speed_low = 6.25
    cfg.feature_toggles["2f_tap"] = True
    cfg.action_mapping["1f_tap"] = "right_click"

    load_path, save_path, notes, other = resolve_config_paths(
        ["--config", str(custom), "-wireless"])
    cfg.config_path = load_path
    cfg.save_path = save_path

    assert load_path == str(custom)
    assert save_path == str(custom)
    assert notes == []
    assert other == ["-wireless"]

    cfg.save_to_file()
    fresh = DriverConfig(load_path)
    assert fresh.cursor_speed_low == 6.25
    assert fresh.feature_toggles["2f_tap"] is True
    assert fresh.action_mapping["1f_tap"] == "right_click"


def test_save_as_follows_its_own_path(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    loaded = tmp_path / "loaded.cfg"
    saved = tmp_path / "saved.cfg"
    loaded.write_text(json.dumps({"motion_and_thresholds": {"max_tap_duration": 0.4}}))

    load_path, save_path, _, _ = resolve_config_paths(
        ["--config", str(loaded), "--save-as", str(saved)])
    cfg = DriverConfig(load_path)
    cfg.save_path = save_path

    assert cfg.max_tap_duration == 0.4
    assert not saved.exists()

    cfg.save_to_file()
    assert saved.exists()
    assert not json.loads(saved.read_text())["devices"]


def test_config_option_accepts_equals_form_and_flags_missing_value():
    load_path, save_path, notes, other = resolve_config_paths(["--config=X.cfg", "-wireless"])
    assert load_path == "X.cfg"
    assert save_path == "X.cfg"
    assert other == ["-wireless"]

    load_path, save_path, notes, _ = resolve_config_paths(["--config"])
    assert load_path == "user.cfg"
    assert save_path == "user.cfg"
    assert len(notes) == 1 and "config" in notes[0]


def test_invalid_value_applies_nothing(monkeypatch, tmp_path, capsys):
    cfg = fresh_config(monkeypatch, tmp_path)
    path = tmp_path / "user.cfg"
    path.write_text(json.dumps({
        "motion_and_thresholds": {"cursor_speed_low": 6.0, "cursor_speed_high": "abc"},
        "feature_toggles": {"2f_tap": True},
        "action_mapping": {"1f_tap": "right_click"},
    }, indent=4))

    loaded = cfg.load_from_file(str(path))
    out = capsys.readouterr().out

    assert loaded is False
    assert cfg.cursor_speed_low == 5.0
    assert cfg.cursor_speed_high == 80.0
    assert cfg.feature_toggles["2f_tap"] is False
    assert cfg.action_mapping["1f_tap"] == "left_click"
    assert cfg.sensitivities["cursor_acceleration"][3] == 5.0
    assert "cursor_speed_high" in out
    assert "line 4" in out
    assert "Nothing applied" in out


def test_unknown_action_name_applies_nothing(monkeypatch, tmp_path, capsys):
    cfg = fresh_config(monkeypatch, tmp_path)
    path = tmp_path / "user.cfg"
    path.write_text(json.dumps({
        "action_mapping": {"1f_tap": "not_a_known_action"},
        "feature_toggles": {"2f_tap": True},
    }, indent=4))

    loaded = cfg.load_from_file(str(path))
    out = capsys.readouterr().out

    assert loaded is False
    assert cfg.action_mapping["1f_tap"] == "left_click"
    assert cfg.feature_toggles["2f_tap"] is False
    assert "not_a_known_action" in out


def test_unknown_keys_warn_and_still_load(monkeypatch, tmp_path, capsys):
    cfg = fresh_config(monkeypatch, tmp_path)
    path = tmp_path / "user.cfg"
    path.write_text(json.dumps({
        "bogus_section": {},
        "motion_and_thresholds": {"not_a_setting": 1.0, "max_tap_duration": 0.45},
    }, indent=4))

    loaded = cfg.load_from_file(str(path))
    out = capsys.readouterr().out

    assert loaded is True
    assert cfg.max_tap_duration == 0.45
    assert "[Config Warning]" in out
    assert "bogus_section" in out
    assert "not_a_setting" in out
    assert not hasattr(cfg, "not_a_setting")


def test_invalid_devices_slot_applies_nothing(monkeypatch, tmp_path, capsys):
    cfg = fresh_config(monkeypatch, tmp_path)
    path = tmp_path / "user.cfg"
    path.write_text(json.dumps({"devices": [], "feature_toggles": {"2f_tap": True}}, indent=4))

    loaded = cfg.load_from_file(str(path))

    assert loaded is False
    assert cfg.feature_toggles["2f_tap"] is False
    assert "devices" in capsys.readouterr().out


def test_devices_slot_round_trip(monkeypatch, tmp_path):
    cfg = fresh_config(monkeypatch, tmp_path)
    cfg.devices = {"tablet-a": {"scroll_speed_low": 20.0}}
    path = tmp_path / "user.cfg"

    cfg.save_to_file(str(path))
    data = json.loads(path.read_text())
    assert data["devices"] == {"tablet-a": {"scroll_speed_low": 20.0}}

    fresh = DriverConfig()
    fresh.load_from_file(str(path))
    assert fresh.devices == {"tablet-a": {"scroll_speed_low": 20.0}}
    assert fresh.scroll_speed_low == 12.0
