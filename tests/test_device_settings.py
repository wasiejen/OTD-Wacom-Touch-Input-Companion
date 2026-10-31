"""Per-device settings: alias rule, overlay, and isolation (U73).

Device-free: tmp files only, no real device, no GUI window.
"""

import json

import pytest

from touch_controller import (
    DriverConfig,
    MODEL_CLASS_ALIASES,
    WIRELESS_DEFAULT_ALIAS,
    resolve_device_alias,
)


def fresh_config(monkeypatch, tmp_path):
    """Build a DriverConfig with no user.cfg reachable from cwd."""
    monkeypatch.chdir(tmp_path)
    return DriverConfig()


def overlay_entry():
    return {
        "motion_and_thresholds": {"cursor_speed_low": 7.0, "max_tap_duration": 0.4},
        "feature_toggles": {"2f_tap": True},
        "action_mapping": {"1f_tap": "right_click"},
    }


# --- alias rule (U72 §4) ----------------------------------------------------

@pytest.mark.parametrize("product_id,expected", [
    (788, "wacom-intuos5-s"),
    (789, "wacom-intuos5-m"),
    (791, "wacom-intuos5-l"),
])
def test_wired_model_class_alias(product_id, expected):
    assert resolve_device_alias(product_id, False) == expected


def test_receiver_gets_placeholder_alias():
    assert resolve_device_alias(132, True) == WIRELESS_DEFAULT_ALIAS


def test_manual_binding_resolves_receiver_twin():
    assert resolve_device_alias(132, True, {"wacom-intuos5-m": [132]}) == "wacom-intuos5-m"


def test_wired_class_wins_over_binding():
    assert resolve_device_alias(789, False, {"other-alias": [789]}) == "wacom-intuos5-m"


def test_unknown_device_resolves_to_none():
    assert resolve_device_alias(999, False) is None
    assert resolve_device_alias(999, True, {"a": [132]}) == "wireless"


def test_alias_table_covers_declared_product_ids():
    """Every wired model id the manager enumerates maps to a class alias."""
    for product_id in (788, 789, 791):
        assert product_id in MODEL_CLASS_ALIASES


# --- per-device config fork -------------------------------------------------

def test_fork_applies_overlay_and_rebuilds_sensitivities(monkeypatch, tmp_path):
    cfg = fresh_config(monkeypatch, tmp_path)
    cfg.devices = {"wacom-intuos5-m": overlay_entry()}

    fork = cfg.fork_for_device("wacom-intuos5-m")

    assert fork.cursor_speed_low == 7.0
    assert fork.max_tap_duration == 0.4
    assert fork.feature_toggles["2f_tap"] is True
    assert fork.action_mapping["1f_tap"] == "right_click"
    assert fork.sensitivities["cursor_acceleration"][3] == 7.0


def test_fork_leaves_base_untouched_and_owns_its_dicts(monkeypatch, tmp_path):
    cfg = fresh_config(monkeypatch, tmp_path)
    cfg.devices = {"wacom-intuos5-m": overlay_entry()}

    fork = cfg.fork_for_device("wacom-intuos5-m")
    fork.feature_toggles["2f_tap"] = False
    fork.action_mapping["1f_tap"] = "undo"
    fork.cursor_speed_low = 1.0

    assert cfg.cursor_speed_low == 5.0
    assert cfg.feature_toggles["2f_tap"] is False
    assert cfg.action_mapping["1f_tap"] == "left_click"


def test_fork_of_unknown_alias_is_the_global_view(monkeypatch, tmp_path):
    cfg = fresh_config(monkeypatch, tmp_path)
    cfg.cursor_speed_low = 3.0
    cfg.devices = {"tablet-a": overlay_entry()}

    none_fork = cfg.fork_for_device(None)
    missing_fork = cfg.fork_for_device("absent-alias")

    for fork in (none_fork, missing_fork):
        assert fork.cursor_speed_low == 3.0
        assert fork.feature_toggles == cfg.feature_toggles
        assert fork.action_mapping == cfg.action_mapping


def test_invalid_overlay_entry_keeps_global(monkeypatch, tmp_path, capsys):
    cfg = fresh_config(monkeypatch, tmp_path)
    cfg.devices = {"wacom-intuos5-m": {
        "motion_and_thresholds": {"cursor_speed_low": "abc", "max_tap_duration": 0.4},
        "feature_toggles": {"2f_tap": True},
    }}

    fork = cfg.fork_for_device("wacom-intuos5-m")
    out = capsys.readouterr().out

    assert fork.cursor_speed_low == 5.0
    assert fork.max_tap_duration == DriverConfig().max_tap_duration
    assert fork.feature_toggles["2f_tap"] is False
    assert "Invalid devices entry" in out
    assert "cursor_speed_low" in out


# --- schema round-trip --------------------------------------------------------

def test_device_entry_round_trip_and_reload(monkeypatch, tmp_path):
    cfg = fresh_config(monkeypatch, tmp_path)
    cfg.set_device_entry("wacom-intuos5-m", overlay_entry())
    path = tmp_path / "user.cfg"

    cfg.save_to_file(str(path))
    fresh = DriverConfig()
    assert fresh.load_from_file(str(path)) is True
    assert fresh.devices["wacom-intuos5-m"] == overlay_entry()

    fork = fresh.fork_for_device("wacom-intuos5-m")
    assert fork.cursor_speed_low == 7.0
    assert fork.action_mapping["1f_tap"] == "right_click"


def test_set_device_entry_keeps_bindings(monkeypatch, tmp_path):
    cfg = fresh_config(monkeypatch, tmp_path)
    cfg.set_device_entry("wacom-intuos5-m", {}, bindings=[132])

    cfg.set_device_entry("wacom-intuos5-m", overlay_entry())

    assert cfg.devices["wacom-intuos5-m"]["bindings"] == [132]
    assert cfg.devices["wacom-intuos5-m"]["feature_toggles"] == {"2f_tap": True}


def test_unknown_device_reload_falls_back_to_global(monkeypatch, tmp_path):
    """Global sections apply first; only the known alias overlays them."""
    cfg = fresh_config(monkeypatch, tmp_path)
    cfg.cursor_speed_low = 3.0
    cfg.set_device_entry("wacom-intuos5-m", overlay_entry())
    path = tmp_path / "user.cfg"
    cfg.save_to_file(str(path))

    fresh = DriverConfig()
    fresh.load_from_file(str(path))
    unknown = fresh.fork_for_device(None)

    assert unknown.cursor_speed_low == 3.0
    assert json.loads(path.read_text())["devices"]["wacom-intuos5-m"]


# --- manual bindings ----------------------------------------------------------

def test_device_bindings_skips_invalid_entries(monkeypatch, tmp_path):
    cfg = fresh_config(monkeypatch, tmp_path)
    cfg.devices = {
        "bound-alias": {"bindings": [132]},
        "broken-alias": {"bindings": "132"},
        "plain-alias": {"feature_toggles": {}},
        "empty-alias": {"bindings": []},
    }

    assert cfg.device_bindings() == {"bound-alias": [132]}
