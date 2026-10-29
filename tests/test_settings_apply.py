"""Settings apply/save all-or-nothing tests (TODO #250).

The real SettingsWindow.apply_settings/save_to_file bodies run against a
duck-typed widget surface; no Tk window, no messagebox, no device.
"""

import json
import types

import pytest

import gui
from touch_controller import DriverConfig


class FakeEntry:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value


class FakeVar:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value


class FakeMessageBox:
    def __init__(self):
        self.errors = []

    def showerror(self, title, message):
        self.errors.append((title, message))


class FakeWindow:
    """Duck-typed SettingsWindow widget surface used by apply/save."""

    def __init__(self, config, entries, accel=(True, True), features=None, actions=None):
        self.config = config
        self.entries = {key: FakeEntry(value) for key, value in entries.items()}
        self.cursor_accel_var = FakeVar(accel[0])
        self.scroll_accel_var = FakeVar(accel[1])
        self.feature_vars = {key: FakeVar(value) for key, value in (features or {}).items()}
        self.action_combos = {key: FakeEntry(value) for key, value in (actions or {}).items()}
        # Bind the real SettingsWindow.apply_settings body onto this surface.
        self.apply_settings = types.MethodType(gui.SettingsWindow.apply_settings, self)


@pytest.fixture
def window_surface(monkeypatch, tmp_path):
    """Driver config in tmp cwd plus stubbed messagebox/toast."""
    monkeypatch.chdir(tmp_path)
    box = FakeMessageBox()
    monkeypatch.setattr(gui, "messagebox", box)
    monkeypatch.setattr(gui, "ToastNotification", lambda *args, **kwargs: None)
    config = DriverConfig()
    return config, box


def make_window(config, entries, **kwargs):
    return FakeWindow(config, entries, **kwargs)


def test_parse_numeric_values_returns_floats():
    parsed = gui.parse_numeric_values({"a": "1.5", "b": "7"})
    assert parsed == {"a": 1.5, "b": 7.0}


def test_parse_numeric_values_names_offending_key():
    with pytest.raises(ValueError) as exc:
        gui.parse_numeric_values({"cursor_speed_low": "abc"})
    assert "cursor_speed_low" in str(exc.value)


def test_apply_all_or_nothing_leaves_config_untouched(window_surface):
    config, box = window_surface
    win = make_window(config, {
        "cursor_speed_low": "9.0",
        "cursor_speed_high": "abc",
        "scroll_speed_low": "14.0",
    })

    applied = types.MethodType(gui.SettingsWindow.apply_settings, win)()

    assert applied is False
    assert config.cursor_speed_low == 5.0
    assert config.cursor_speed_high == 80.0
    assert config.scroll_speed_low == 12.0
    assert config.sensitivities["cursor_acceleration"][3] == 5.0


def test_apply_reports_invalid_input_to_user(window_surface):
    config, box = window_surface
    win = make_window(config, {"max_tap_duration": "not-a-number"})

    applied = types.MethodType(gui.SettingsWindow.apply_settings, win)()

    assert applied is False
    assert box.errors, "no error surfaced to the user"
    title, message = box.errors[0]
    assert title == "Invalid Input"
    assert "max_tap_duration" in message


def test_apply_valid_updates_config_and_sensitivities(window_surface):
    config, box = window_surface
    win = make_window(config, {
        "cursor_speed_low": "6.5",
        "cursor_speed_high": "95.0",
        "scroll_speed_low": "10.0",
        "scroll_speed_high": "70.0",
    })

    applied = types.MethodType(gui.SettingsWindow.apply_settings, win)()

    assert applied is True
    assert box.errors == []
    assert config.cursor_speed_low == 6.5
    assert config.cursor_speed_high == 95.0
    assert config.scroll_speed_low == 10.0
    assert config.scroll_speed_high == 70.0
    assert config.sensitivities["cursor_acceleration"][3] == 6.5
    assert config.sensitivities["scroll_acceleration"][4] == 70.0


def test_apply_updates_toggles_and_mappings(window_surface):
    config, box = window_surface
    win = make_window(
        config,
        {},
        accel=(False, True),
        features={"2f_tap": True, "5f_alt_tab": False},
        actions={"1f_tap": "right_click"},
    )

    applied = types.MethodType(gui.SettingsWindow.apply_settings, win)()

    assert applied is True
    assert config.feature_toggles["cursor_acceleration"] is False
    assert config.feature_toggles["scroll_acceleration"] is True
    assert config.feature_toggles["2f_tap"] is True
    assert config.feature_toggles["5f_alt_tab"] is False
    assert config.action_mapping["1f_tap"] == "right_click"


def test_save_aborts_when_input_invalid(window_surface, tmp_path):
    config, box = window_surface
    win = make_window(config, {"cursor_speed_low": "7.0", "cursor_speed_high": "bad"})

    saved = types.MethodType(gui.SettingsWindow.save_to_file, win)()

    assert saved is False
    assert not (tmp_path / "user.cfg").exists()
    assert config.cursor_speed_low == 5.0
    assert box.errors


def test_save_writes_file_when_valid(window_surface, tmp_path):
    config, box = window_surface
    win = make_window(config, {"cursor_speed_low": "7.0", "cursor_speed_high": "90.0"})

    saved = types.MethodType(gui.SettingsWindow.save_to_file, win)()

    assert saved is True
    path = tmp_path / "user.cfg"
    assert path.exists()
    data = json.loads(path.read_text())
    assert data["motion_and_thresholds"]["cursor_speed_low"] == 7.0
    assert data["motion_and_thresholds"]["cursor_speed_high"] == 90.0
