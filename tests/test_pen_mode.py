"""U424 L1 (#424 ruling 3): single stored pen_mode + migration + live apply.

One stored `pen_mode` in {relative, stretched, rect_1to1} replaces the
effective state of the old boolean toggles (`pen_absolute_mode`,
`pen_stretch_full_desktop`). Migration (documented in the protocol
Decision ledger) lands old configs on the mode matching their PRIOR
effective behavior; the stored mode wins over legacy toggle writes (no
dual control); switching applies LIVE (the running reader re-reads the
config per report, the #386 Apply-Live shape). The stored 1:1 rect is
untouched by mode switches (ruling 3: kept, instantly re-usable).

RED-before 9914c83: ImportError on PEN_MODES / resolved_pen_mode.
"""

import pytest

from touch_controller import (
    PEN_MODES,
    ActionDispatcher,
    DriverConfig,
    TabletState,
    resolved_pen_mode,
    update_pen_cursor,
)

ABS_FLAGS = 0x0001 | 0x8000 | 0x4000


@pytest.fixture
def cfg(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    return DriverConfig()


def write_cfg(path, motion):
    import json
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"motion_and_thresholds": motion}, f)
    return str(path)


def test_modes_are_the_three_named_states():
    assert PEN_MODES == ("relative", "stretched", "rect_1to1")


def test_migration_table_matches_prior_effective_behavior(cfg):
    # absolute off -> relative, whatever the stretch toggle says
    cfg.feature_toggles["pen_absolute_mode"] = False
    cfg.feature_toggles["pen_stretch_full_desktop"] = True
    assert resolved_pen_mode(cfg) == "relative"
    # absolute on + stretch -> stretched
    cfg.feature_toggles["pen_absolute_mode"] = True
    assert resolved_pen_mode(cfg) == "stretched"
    # absolute on + no stretch -> rect_1to1 (isotropic contain is its
    # documented no-rect fallback - the old default behavior)
    cfg.feature_toggles["pen_stretch_full_desktop"] = False
    assert resolved_pen_mode(cfg) == "rect_1to1"


def test_stored_mode_wins_over_legacy_toggles(cfg):
    cfg.pen_mode = "relative"
    cfg.feature_toggles["pen_absolute_mode"] = True
    cfg.feature_toggles["pen_stretch_full_desktop"] = True
    assert resolved_pen_mode(cfg) == "relative"


def test_set_pen_mode_validates(cfg):
    with pytest.raises(ValueError):
        cfg.set_pen_mode("absolute")
    with pytest.raises(ValueError):
        cfg.set_pen_mode(None)
    cfg.set_pen_mode("stretched")
    assert cfg.pen_mode == "stretched"


def test_load_reads_pen_mode_and_bad_value_rejects(cfg, tmp_path, capsys):
    path = write_cfg(tmp_path / "mode.cfg", {"pen_mode": "rect_1to1"})
    assert cfg.load_from_file(path) is True
    assert cfg.pen_mode == "rect_1to1"
    capsys.readouterr()
    bad = write_cfg(tmp_path / "bad.cfg", {"pen_mode": "full_fill"})
    assert cfg.load_from_file(bad) is False
    assert "pen_mode" in capsys.readouterr().out


def test_save_round_trip_and_unset_rederives(cfg, tmp_path):
    path = str(tmp_path / "rt.cfg")
    cfg.set_pen_mode("stretched")
    cfg.save_to_file(path)
    fresh = DriverConfig()
    assert fresh.load_from_file(path) is True
    assert fresh.pen_mode == "stretched"
    # Never-set configs round-trip as null and re-derive from toggles.
    fresh.pen_mode = None
    fresh.save_to_file(path)
    again = DriverConfig()
    assert again.load_from_file(path) is True
    assert again.pen_mode is None
    assert resolved_pen_mode(again) == "rect_1to1"  # default toggles


def test_mode_switch_applies_live_no_restart(cfg, stub_send_input):
    # #386 shape: the running reader picks the new lane on the NEXT report.
    state = TabletState()
    dispatcher = ActionDispatcher(state, cfg)
    specs = __import__("touch_controller").PEN_SPECS_BY_PRODUCT[791]
    cfg.feature_toggles["pen_absolute_mode"] = True  # legacy says absolute
    cfg.pen_mode = None
    update_pen_cursor(state, dispatcher, cfg, specs, 100, 100,
                      screen_metrics=(0, 0, 3440, 1440))
    assert stub_send_input[-1][1] == ABS_FLAGS
    cfg.set_pen_mode("relative")
    stub_send_input.clear()
    update_pen_cursor(state, dispatcher, cfg, specs, 200, 100,
                      screen_metrics=(0, 0, 3440, 1440))
    assert stub_send_input[-1][1] == 0x0001  # MOVE-only after the switch


def test_mode_switch_does_not_touch_the_stored_rect(cfg, monkeypatch):
    # Ruling 3: leaving rect_1to1 keeps the rect (inactive, instantly
    # re-usable). Identity/display state irrelevant here - lane data only.
    from touch_controller import display_key_for_records
    records = ["\\\\.\\DISPLAY1|0|0|3440x1440"]
    cfg.set_pen_area_entry("0123456789ab", display_key_for_records(records),
                           records, [0.1, 0.1, 0.5, 0.5])
    cfg.set_pen_mode("relative")
    assert cfg.pen_area_maps["0123456789ab"]["entries"][0]["rect_frac"] == \
        [0.1, 0.1, 0.5, 0.5]
