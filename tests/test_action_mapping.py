"""Gesture action mapping preservation tests (TODO #255 part 1).

The real SettingsWindow.apply_settings body runs against a duck-typed widget
surface; no Tk window, no messagebox, no device.
"""

import types

import pytest

import gui
from touch_controller import DriverConfig

OPTIONS = ["left_click", "right_click", "middle_click", "task_view"]


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
    """Duck-typed SettingsWindow surface: entries, vars, combos, saved actions."""

    def __init__(self, config, combos, saved_actions=None):
        self.config = config
        self.entries = {}
        self.cursor_accel_var = FakeVar(True)
        self.scroll_accel_var = FakeVar(True)
        self.feature_vars = {}
        self.action_combos = {key: FakeEntry(value) for key, value in combos.items()}
        self.saved_actions = dict(saved_actions or {})
        self.apply_settings = types.MethodType(gui.SettingsWindow.apply_settings, self)


@pytest.fixture
def window_surface(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(gui, "messagebox", FakeMessageBox())
    monkeypatch.setattr(gui, "ToastNotification", lambda *args, **kwargs: None)
    return DriverConfig()


def test_valid_action_shows_plain():
    assert gui.action_display_value("left_click", OPTIONS) == "left_click"


def test_invalid_action_shows_marked():
    display = gui.action_display_value("my_custom_gesture", OPTIONS)
    assert display.startswith(gui.INVALID_ACTION_PREFIX)
    assert "my_custom_gesture" in display


def test_untouched_invalid_selection_is_preserved():
    display = gui.action_display_value("my_custom_gesture", OPTIONS)
    assert gui.resolve_action_mapping(display, "my_custom_gesture", OPTIONS) == "my_custom_gesture"


def test_chosen_valid_option_is_stored():
    assert gui.resolve_action_mapping("right_click", "my_custom_gesture", OPTIONS) == "right_click"


def test_valid_saved_selection_round_trips():
    assert gui.resolve_action_mapping("left_click", "left_click", OPTIONS) == "left_click"


def test_invalid_action_survives_with_no_options():
    display = gui.action_display_value("legacy_action", [])
    assert display.startswith(gui.INVALID_ACTION_PREFIX)
    assert gui.resolve_action_mapping(display, "legacy_action", []) == "legacy_action"


def test_open_and_apply_keeps_invalid_mapping(window_surface):
    config = window_surface
    config.action_mapping["1f_tap"] = "legacy_action"
    config.AVAILABLE_ACTIONS = OPTIONS

    win = FakeWindow(
        config,
        {"1f_tap": gui.action_display_value("legacy_action", OPTIONS)},
        {"1f_tap": "legacy_action"},
    )

    applied = win.apply_settings()

    assert applied is True
    assert config.action_mapping["1f_tap"] == "legacy_action"


def test_open_and_apply_updates_a_changed_mapping(window_surface):
    config = window_surface
    config.action_mapping["1f_tap"] = "legacy_action"
    config.AVAILABLE_ACTIONS = OPTIONS

    win = FakeWindow(
        config,
        {"1f_tap": gui.action_display_value("legacy_action", OPTIONS)},
        {"1f_tap": "legacy_action"},
    )

    win.action_combos["1f_tap"].value = "right_click"
    applied = win.apply_settings()

    assert applied is True
    assert config.action_mapping["1f_tap"] == "right_click"


def test_valid_mapping_round_trips_through_apply(window_surface):
    config = window_surface
    config.action_mapping["2f_tap"] = "right_click"
    config.AVAILABLE_ACTIONS = OPTIONS

    win = FakeWindow(config, {"2f_tap": "right_click"}, {"2f_tap": "right_click"})

    assert win.apply_settings() is True
    assert config.action_mapping["2f_tap"] == "right_click"
