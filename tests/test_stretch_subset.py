"""U424 L2 (#424 ruling 1): stretched mode fills ONLY the selected screens.

`pen_stretch_screens` = list of monitor-record strings naming the stretch
target subset (None/empty = ALL screens = pre-U424 full-fill behavior,
byte-unchanged control). Unselected screens become unreachable: the pen
box stretches across the UNION of the selected monitors, which lies
strictly inside the virtual desktop, so normalized output never crosses
into the unselected area. Junk metrics keep the degraded full-fill
(stretch_unmeasured) - subset math needs the metrics datum and does not
invent one.

RED-before b5f0840: pen_stretch_screens does not exist / the stretch
lane ignores it (subset pins land full-desktop corners).
"""

import json

import pytest

from wtd.config import DriverConfig
from wtd.gestures import TabletState
from wtd.mapping import update_pen_cursor
from wtd.sender import ActionDispatcher
from wtd import config
from wtd import mapping
from wtd import protocol

ABS_FLAGS = 0x0001 | 0x8000 | 0x4000
VIRT = protocol.PEN_VIRTUAL_RESOLUTION
SPEC_791 = protocol.PEN_SPECS_BY_PRODUCT[791]
SCREEN = (0, 0, 3440, 1440)
DISP1 = "\\\\.\\DISPLAY1|0|0|1920x1080"
DISP2 = "\\\\.\\DISPLAY2|1920|0|1520x1440"


@pytest.fixture
def cfg(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    return DriverConfig()


@pytest.fixture
def state():
    return TabletState()


def _map(state, config, x, y, events, screens=None):
    config.set_pen_mode("stretched")
    if screens is not None:
        config.pen_stretch_screens = screens
    dispatcher = ActionDispatcher(state, config)
    update_pen_cursor(state, dispatcher, config, SPEC_791, x, y,
                      screen_metrics=SCREEN)
    # WTD-BATCH: flush the parked absolute move (see test_pen_absolute_isotropy).
    dispatcher.flush_pending_move()
    assert events and events[-1][0] == "mouse" and events[-1][1] == ABS_FLAGS
    return events[-1][3], events[-1][4]


def _px(nx, screen_w):
    return round(nx * screen_w / VIRT)


def test_no_subset_control_is_the_untouched_full_fill(state, cfg, stub_send_input):
    # Control: pen_stretch_screens unset == pre-U424 byte-identical
    # full-desktop stretch (near corner 0, far corner VIRT).
    assert cfg.pen_stretch_screens is None
    nx0, ny0 = _map(state, cfg, 0, 0, stub_send_input)
    assert (nx0, ny0) == (0, 0)
    stub_send_input.clear()
    nx1, ny1 = _map(state, cfg, SPEC_791["max_x"], SPEC_791["max_y"],
                    stub_send_input)
    assert (nx1, ny1) == (VIRT, VIRT)


def test_subset_fills_only_the_selected_screen_union(state, cfg, stub_send_input):
    nx1, ny1 = _map(state, cfg, SPEC_791["max_x"], SPEC_791["max_y"],
                    stub_send_input, screens=[DISP1])
    # Far corner lands on DISPLAY1's far edge, not the desktop's.
    assert _px(nx1, 3440) == 1920 and _px(ny1, 1440) == 1080


def test_unselected_screen_is_unreachable(state, cfg, stub_send_input):
    # Sweeping the whole box, the pen never crosses into DISPLAY2's
    # territory (x > 1920 on the 3440-wide union).
    max_x = 0
    for frac in range(5):
        stub_send_input.clear()
        nx, _ = _map(state, cfg, SPEC_791["max_x"] * frac // 4, 0,
                     stub_send_input, screens=[DISP1])
        max_x = max(max_x, _px(nx, 3440))
    assert max_x <= 1921


def test_two_selected_screens_fill_their_union(state, cfg, stub_send_input):
    nx1, ny1 = _map(state, cfg, SPEC_791["max_x"], SPEC_791["max_y"],
                    stub_send_input, screens=[DISP1, DISP2])
    # Union of both = the full 3440x1440 virtual desktop here.
    assert (nx1, ny1) == (VIRT, VIRT)


def test_junk_metrics_keep_the_degraded_full_fill(state, cfg, stub_send_input):
    cfg.pen_stretch_screens = [DISP1]
    dispatcher = ActionDispatcher(state, cfg)
    cfg.set_pen_mode("stretched")
    update_pen_cursor(state, dispatcher, cfg, SPEC_791, 0, 0, screen_metrics=None)
    dispatcher.flush_pending_move()
    assert stub_send_input[-1][3:] == (0, 0)
    update_pen_cursor(state, dispatcher, cfg, SPEC_791, SPEC_791["max_x"],
                      SPEC_791["max_y"], screen_metrics=None)
    dispatcher.flush_pending_move()
    assert stub_send_input[-1][3:] == (VIRT, VIRT)


def test_diag_names_the_subset_mode(state, cfg, stub_send_input, capsys, monkeypatch):
    monkeypatch.setenv(mapping.PEN_MAP_DIAG_ENV, "1")
    monkeypatch.setattr(mapping, "monitor_count", lambda: 2)
    state.pen_product_id = 791
    capsys.readouterr()
    _map(state, cfg, 0, 0, stub_send_input, screens=[DISP1])
    out = capsys.readouterr().out
    assert out.startswith("pen_abs_map mode=stretch_subset")


def test_pen_stretch_screens_validates_and_round_trips(cfg, tmp_path, capsys):
    path = str(tmp_path / "sub.cfg")
    cfg.pen_stretch_screens = [DISP1, DISP2]
    cfg.save_to_file(path)
    fresh = DriverConfig()
    assert fresh.load_from_file(path) is True
    assert fresh.pen_stretch_screens == [DISP1, DISP2]
    bad = {"motion_and_thresholds": {"pen_stretch_screens": ["a", 3]}}
    with open(tmp_path / "bad.cfg", "w", encoding="utf-8") as f:
        json.dump(bad, f)
    capsys.readouterr()
    assert fresh.load_from_file(str(tmp_path / "bad.cfg")) is False
    assert "pen_stretch_screens" in capsys.readouterr().out
