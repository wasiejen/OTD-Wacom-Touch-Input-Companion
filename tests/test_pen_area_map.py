"""U414 L1 (#406) + U416 L3 (#406 defect round 1): pen-area map persistence.

Pins the `pen_area_map_<identity-hash>` lane: loader admission (no unknown-
section warning), save/reload round-trip, per-display-key entry selection
(exact match only, stale configurations never selected), same-key replace,
entry-shape validation, and the display-key digest over sorted monitor
records.

U416: the stored rect is now FRACTIONAL (rect_frac = rx/ry/rw/rh as
fractions of the union bounds of the entry's own monitors) so metrics-less
hosts can edit and consume it. PENAREA (2026-11-16): those fractions are no
longer bounded to 0..1 - an off-screen rect part stores negative values or
values past 1 (the editor enforces "at least one corner ... inside the
display area"). Legacy rect_px entries are MIGRATED AT LOAD using the
entry's own monitor-record union (documented choice: no heuristic unit
guessing - the records give the exact bounds the px datum came from).
RED-before 03c0a40: rect_frac fails the old rect_px validator.
"""

import hashlib
import json

import pytest

from wtd.config import (DriverConfig, PEN_AREA_MAP_PREFIX)
from wtd.mapping import (current_pen_area_entry, display_key_for_records, select_pen_area_entry)
from wtd import mapping

IDENTITY = "0123456789ab"
KEY_A = "a" * 40
KEY_B = "b" * 40
MONITORS_A = ["\\\\.\\DISPLAY1|0|0|3440x1440"]
MONITORS_B = ["\\\\.\\DISPLAY1|0|0|1920x1080"]
RECT_FRAC_A = [0.1, 0.05, 0.5, 0.25]


@pytest.fixture
def cfg(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    return DriverConfig()


def _lane(entries):
    return {"entries": entries}


def _entry(display_key, monitors, rect_frac):
    return {"display_key": display_key, "monitors": list(monitors),
            "rect_frac": list(rect_frac)}


def test_lane_prefix_admitted_without_unknown_warning(cfg):
    data = {f"{PEN_AREA_MAP_PREFIX}{IDENTITY}": _lane([_entry(KEY_A, MONITORS_A, RECT_FRAC_A)])}
    _, warnings, errors = cfg.validate_config_data(data, json.dumps(data))
    assert errors == []
    assert not any("unknown section" in w for w in warnings)
    validated, _, _ = cfg.validate_config_data(data, json.dumps(data))
    assert validated["pen_area_maps"][IDENTITY]["entries"] == [_entry(KEY_A, MONITORS_A, RECT_FRAC_A)]


def test_set_entry_save_reload_round_trip(cfg):
    cfg.set_pen_area_entry(IDENTITY, KEY_A, MONITORS_A, RECT_FRAC_A)
    cfg.save_to_file()
    reloaded = DriverConfig(cfg.config_path)
    assert select_pen_area_entry(reloaded.pen_area_maps[IDENTITY], KEY_A) == \
        _entry(KEY_A, MONITORS_A, RECT_FRAC_A)


def test_stale_display_key_entry_is_not_selected(cfg):
    cfg.set_pen_area_entry(IDENTITY, KEY_A, MONITORS_A, RECT_FRAC_A)
    assert select_pen_area_entry(cfg.pen_area_maps[IDENTITY], KEY_B) is None
    assert cfg.pen_area_maps[IDENTITY]["entries"]  # entry still stored


def test_same_key_replaces_current_entry_only(cfg):
    cfg.set_pen_area_entry(IDENTITY, KEY_A, MONITORS_A, RECT_FRAC_A)
    cfg.set_pen_area_entry(IDENTITY, KEY_B, MONITORS_B, [0.0, 0.0, 0.5, 0.5])
    cfg.set_pen_area_entry(IDENTITY, KEY_A, MONITORS_A, [0.25, 0.1, 0.25, 0.125])
    entries = cfg.pen_area_maps[IDENTITY]["entries"]
    by_key = {e["display_key"]: e for e in entries}
    assert len(entries) == 2
    assert by_key[KEY_A]["rect_frac"] == [0.25, 0.1, 0.25, 0.125]
    assert by_key[KEY_B]["rect_frac"] == [0.0, 0.0, 0.5, 0.5]


def test_bad_entry_shape_is_named_and_valid_entries_survive(cfg):
    data = {
        f"{PEN_AREA_MAP_PREFIX}{IDENTITY}": _lane([
            _entry(KEY_A, MONITORS_A, RECT_FRAC_A),
            {"display_key": "x", "monitors": "not-a-list", "rect_frac": [1, 2]},
        ])
    }
    validated, _, errors = cfg.validate_config_data(data, json.dumps(data))
    assert any(f"{PEN_AREA_MAP_PREFIX}{IDENTITY}" in e for e in errors)
    # U414 STEP-1 P1: the surviving-entry half of the protocol pin -- a
    # regression that drops the whole lane on one bad entry must fail here.
    assert validated["pen_area_maps"][IDENTITY]["entries"] == [_entry(KEY_A, MONITORS_A, RECT_FRAC_A)]


def test_set_pen_area_entry_rejects_bad_shape(cfg):
    # U414 STEP-1 P2-3 (write path validates). PENAREA (2026-11-16) retires
    # the 0..1 containment rejects (rx+rw > 1, rw > 1): the rect may extend
    # outside the display union. The schema keeps the integrity rejects:
    # shape, positive size, finite values, non-empty key.
    with pytest.raises(ValueError):
        cfg.set_pen_area_entry(IDENTITY, KEY_A, MONITORS_A, [0.1, 0.2])
    with pytest.raises(ValueError):
        cfg.set_pen_area_entry(IDENTITY, KEY_A, MONITORS_A, [0.0, 0.0, 0.0, 0.5])
    with pytest.raises(ValueError):
        cfg.set_pen_area_entry(IDENTITY, KEY_A, MONITORS_A, [0.6, 0.0, -0.5, 0.5])  # rw < 0
    with pytest.raises(ValueError):
        cfg.set_pen_area_entry(IDENTITY, KEY_A, MONITORS_A, [0.6, 0.0, 0.5, 0.0])  # rh = 0
    with pytest.raises(ValueError):
        cfg.set_pen_area_entry(IDENTITY, KEY_A, MONITORS_A,
                               [float("nan"), 0.0, 0.5, 0.5])
    with pytest.raises(ValueError):
        cfg.set_pen_area_entry(IDENTITY, KEY_A, MONITORS_A,
                               [0.0, 0.0, float("inf"), 0.5])
    with pytest.raises(ValueError):
        cfg.set_pen_area_entry(IDENTITY, "", MONITORS_A, RECT_FRAC_A)
    assert cfg.pen_area_maps == {}


def test_offscreen_rect_fraction_round_trips(cfg):
    # PENAREA (2026-11-16, maintainer rule quoted in the editor pins): the
    # pen-area rect may extend partly outside the display area, so the
    # stored fractions may be negative or exceed 1. Save + reload keeps them
    # exactly; the loader admits the entry without an error or warning.
    off = [-0.25, -0.1, 1.5, 0.8]
    cfg.set_pen_area_entry(IDENTITY, KEY_A, MONITORS_A, off)
    cfg.save_to_file()
    reloaded = DriverConfig(cfg.config_path)
    assert select_pen_area_entry(reloaded.pen_area_maps[IDENTITY], KEY_A) == \
        _entry(KEY_A, MONITORS_A, off)
    data = {f"{PEN_AREA_MAP_PREFIX}{IDENTITY}": _lane([_entry(KEY_A, MONITORS_A, off)])}
    validated, warnings, errors = cfg.validate_config_data(data, json.dumps(data))
    assert errors == []
    assert not any("rect_frac" in w for w in warnings)
    assert validated["pen_area_maps"][IDENTITY]["entries"][0]["rect_frac"] == off


def test_rect_out_of_fraction_range_is_named(cfg):
    data = {
        f"{PEN_AREA_MAP_PREFIX}{IDENTITY}": _lane([
            {"display_key": "d" * 40, "monitors": MONITORS_A, "rect_frac": [0, 0, 0, 0]},
        ])
    }
    _, _, errors = cfg.validate_config_data(data, json.dumps(data))
    assert any("rect_frac" in e for e in errors)  # rw/rh must be positive


def test_legacy_rect_px_entry_migrates_via_its_own_monitor_union(cfg):
    # U416 documented migration choice: legacy px entries convert at LOAD
    # with the union bounds of the entry's OWN monitors (the px datum's
    # source), then the next save rewrites the entry as rect_frac. No unit
    # guessing from current-display metrics (which read junk on the hosts
    # this lane must survive).
    data = {
        f"{PEN_AREA_MAP_PREFIX}{IDENTITY}": _lane([
            {"display_key": KEY_A, "monitors": ["\\\\.\\DISPLAY1|0|0|3440x1440"],
             "rect_px": [344, 144, 1720, 720]},
        ])
    }
    validated, _, errors = cfg.validate_config_data(data, json.dumps(data))
    assert errors == []
    entry = validated["pen_area_maps"][IDENTITY]["entries"][0]
    assert "rect_px" not in entry
    assert entry["rect_frac"] == pytest.approx([0.1, 0.1, 0.5, 0.5])


def test_legacy_rect_px_entry_without_parseable_monitors_is_rejected(cfg):
    data = {
        f"{PEN_AREA_MAP_PREFIX}{IDENTITY}": _lane([
            {"display_key": KEY_A, "monitors": [], "rect_px": [0, 0, 100, 100]},
        ])
    }
    _, _, errors = cfg.validate_config_data(data, json.dumps(data))
    assert any("rect_px" in e for e in errors)


def test_display_key_digest_is_sorted_join_full_sha1():
    records = ["\\\\.\\DISPLAY2|3440|0|1080x1920", "\\\\.\\DISPLAY1|0|0|3440x1440"]
    expected = hashlib.sha1("\n".join(sorted(records)).encode("utf-8")).hexdigest()
    assert display_key_for_records(records) == expected
    assert len(expected) == 40
    assert display_key_for_records(list(reversed(records))) == expected


def test_current_entry_falls_back_when_enumeration_fails(cfg, monkeypatch):
    cfg.set_pen_area_entry(IDENTITY, display_key_for_records(MONITORS_A),
                           MONITORS_A, RECT_FRAC_A)
    monkeypatch.setattr("wtd.mapping.monitor_records", lambda: [])
    assert current_pen_area_entry(cfg, IDENTITY) is None


def test_current_entry_selected_for_matching_records(cfg, monkeypatch):
    key = display_key_for_records(MONITORS_A)
    cfg.set_pen_area_entry(IDENTITY, key, MONITORS_A, RECT_FRAC_A)
    monkeypatch.setattr("wtd.mapping.monitor_records", lambda: list(MONITORS_A))
    assert current_pen_area_entry(cfg, IDENTITY) == _entry(key, MONITORS_A, RECT_FRAC_A)


def test_display_key_for_empty_records_is_stable(cfg):
    # U416 fallback-host shape: with NO enumeration the editor still names
    # its entry with a stable key (sha1 over the empty join), so a rect
    # saved on one metrics-less session reloads on the next.
    key = display_key_for_records([])
    assert key == hashlib.sha1(b"").hexdigest()
    assert len(key) == 40
