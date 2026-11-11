"""U414 L1 (#406, approved design proposal §2): pen-area map persistence lane.

Pins the `pen_area_map_<identity-hash>` lane: loader admission (no unknown-
section warning), save/reload round-trip, per-display-key entry selection
(exact match only, stale configurations never selected), same-key replace,
entry-shape validation, and the display-key digest over sorted monitor
records. RED-before at 4fde164: ImportError on the new API names (stated in
the protocol LEG 1 entry).
"""

import hashlib
import json

import pytest

from touch_controller import (
    DriverConfig,
    PEN_AREA_MAP_PREFIX,
    current_pen_area_entry,
    display_key_for_records,
    select_pen_area_entry,
)

IDENTITY = "0123456789ab"
KEY_A = "a" * 40
KEY_B = "b" * 40
MONITORS_A = ["\\\\.\\DISPLAY1|0|0|3440x1440"]
RECT_A = [100, 20, 800, 500]


@pytest.fixture
def cfg(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    return DriverConfig()


def _lane(entries):
    return {"entries": entries}


def _entry(display_key, monitors, rect):
    return {"display_key": display_key, "monitors": list(monitors),
            "rect_px": list(rect)}


def test_lane_prefix_admitted_without_unknown_warning(cfg):
    data = {f"{PEN_AREA_MAP_PREFIX}{IDENTITY}": _lane([_entry(KEY_A, MONITORS_A, RECT_A)])}
    _, warnings, errors = cfg.validate_config_data(data, json.dumps(data))
    assert errors == []
    assert not any("unknown section" in w for w in warnings)
    validated, _, _ = cfg.validate_config_data(data, json.dumps(data))
    assert validated["pen_area_maps"][IDENTITY]["entries"] == [_entry(KEY_A, MONITORS_A, RECT_A)]


def test_set_entry_save_reload_round_trip(cfg):
    cfg.set_pen_area_entry(IDENTITY, KEY_A, MONITORS_A, RECT_A)
    cfg.save_to_file()
    reloaded = DriverConfig(cfg.config_path)
    assert select_pen_area_entry(reloaded.pen_area_maps[IDENTITY], KEY_A) == \
        _entry(KEY_A, MONITORS_A, RECT_A)


def test_stale_display_key_entry_is_not_selected(cfg):
    cfg.set_pen_area_entry(IDENTITY, KEY_A, MONITORS_A, RECT_A)
    assert select_pen_area_entry(cfg.pen_area_maps[IDENTITY], KEY_B) is None
    assert cfg.pen_area_maps[IDENTITY]["entries"]  # entry still stored


def test_same_key_replaces_current_entry_only(cfg):
    cfg.set_pen_area_entry(IDENTITY, KEY_A, MONITORS_A, RECT_A)
    cfg.set_pen_area_entry(IDENTITY, KEY_B, ["\\\\.\\DISPLAY1|0|0|1920x1080"], [0, 0, 100, 62])
    cfg.set_pen_area_entry(IDENTITY, KEY_A, MONITORS_A, [10, 20, 300, 187])
    entries = cfg.pen_area_maps[IDENTITY]["entries"]
    by_key = {e["display_key"]: e for e in entries}
    assert len(entries) == 2
    assert by_key[KEY_A]["rect_px"] == [10, 20, 300, 187]
    assert by_key[KEY_B]["rect_px"] == [0, 0, 100, 62]


def test_bad_entry_shape_is_named_and_valid_entries_survive(cfg):
    data = {
        f"{PEN_AREA_MAP_PREFIX}{IDENTITY}": _lane([
            _entry(KEY_A, MONITORS_A, RECT_A),
            {"display_key": "x", "monitors": "not-a-list", "rect_px": [1, 2]},
        ])
    }
    validated, _, errors = cfg.validate_config_data(data, json.dumps(data))
    assert any(f"{PEN_AREA_MAP_PREFIX}{IDENTITY}" in e for e in errors)
    # U414 STEP-1 P1: the surviving-entry half of the protocol pin -- a
    # regression that drops the whole lane on one bad entry must fail here.
    assert validated["pen_area_maps"][IDENTITY]["entries"] == [_entry(KEY_A, MONITORS_A, RECT_A)]


def test_set_pen_area_entry_rejects_bad_shape(cfg):
    # U414 STEP-1 P2-3 (RED-before: the write path validated nothing and
    # either raised TypeError or stored garbage until the next load).
    with pytest.raises(ValueError):
        cfg.set_pen_area_entry(IDENTITY, KEY_A, MONITORS_A, [1, 2])
    with pytest.raises(ValueError):
        cfg.set_pen_area_entry(IDENTITY, KEY_A, MONITORS_A, [0, 0, 0, 500])
    with pytest.raises(ValueError):
        cfg.set_pen_area_entry(IDENTITY, "", MONITORS_A, RECT_A)
    assert cfg.pen_area_maps == {}


def test_monitor_records_bad_rect_dropped_or_errored(cfg):
    data = {
        f"{PEN_AREA_MAP_PREFIX}{IDENTITY}": _lane([
            {"display_key": "d" * 40, "monitors": [], "rect_px": [0, 0, 0, 0]},
        ])
    }
    _, _, errors = cfg.validate_config_data(data, json.dumps(data))
    assert any("rect_px" in e for e in errors)  # rw/rh must be positive


def test_display_key_digest_is_sorted_join_full_sha1():
    records = ["\\\\.\\DISPLAY2|3440|0|1080x1920", "\\\\.\\DISPLAY1|0|0|3440x1440"]
    expected = hashlib.sha1("\n".join(sorted(records)).encode("utf-8")).hexdigest()
    assert display_key_for_records(records) == expected
    assert len(expected) == 40
    assert display_key_for_records(list(reversed(records))) == expected


def test_current_entry_falls_back_when_enumeration_fails(cfg, monkeypatch):
    cfg.set_pen_area_entry(IDENTITY, display_key_for_records(MONITORS_A),
                           MONITORS_A, RECT_A)
    monkeypatch.setattr("touch_controller.monitor_records", lambda: [])
    assert current_pen_area_entry(cfg, IDENTITY) is None


def test_current_entry_selected_for_matching_records(cfg, monkeypatch):
    key = display_key_for_records(MONITORS_A)
    cfg.set_pen_area_entry(IDENTITY, key, MONITORS_A, RECT_A)
    monkeypatch.setattr("touch_controller.monitor_records", lambda: list(MONITORS_A))
    assert current_pen_area_entry(cfg, IDENTITY) == _entry(key, MONITORS_A, RECT_A)
