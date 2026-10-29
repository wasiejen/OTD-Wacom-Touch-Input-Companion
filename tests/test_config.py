"""Config save/load round-trip tests (TODO #247).

Pure JSON handling: tmp_path files only, no device, no GUI.
"""

import json

from touch_controller import DriverConfig


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
