"""Hunting-contract pins: the two byte-stable diagnostic lines verbatim.

The maintainer's log-hunting contract is that the metrics readout line and
the pen_abs_map mapping line keep emitting byte-identical text. The logging
migration (C1) must not change a single character of either line, so both
are pinned here with the exact full text on minimal deterministic fixtures
(alongside the older per-behavior pins in test_metrics_diag.py and
test_pen_absolute_isotropy.py).
"""

import pytest

from wtd import mapping
from wtd.config import DriverConfig
from wtd.gestures import TabletState
from wtd.protocol import PEN_SPECS_DEFAULT
from wtd.mapping import (
    METRICS_DIAG_ENV,
    _log_pen_mapping_diagnostic,
    log_metrics_diagnostic,
    metrics_readout_line,
)

RECORDS_DUO = ["\\\\.\\DISPLAY1|0|0|3440x1440", "\\\\.\\DISPLAY2|3440|0|1440x2560"]

METRICS_LINE = (
    "wtd_metrics_diag SM_XVIRTUALSCREEN=100 SM_YVIRTUALSCREEN=200 "
    "SM_CXVIRTUALSCREEN=1920 SM_CYVIRTUALSCREEN=1080 SM_CMONITORS=2 "
    "call=user32.GetSystemMetrics monitors=2 union=0,0,4880x2560\n"
)

PEN_MAP_LINE = (
    "pen_abs_map mode=isotropic product=789 declared=44704x27940 "
    "box=44704x27940 box_mm=223.5200x139.7000 obs=44704x27940 "
    "screen=2560x1440 monitors=1 px_mm_x=10.0000 px_mm_y=10.0000 "
    "band_x=12.50_px band_y=3.00_px\n"
)


@pytest.fixture
def metrics_env(monkeypatch):
    """Deterministic metrics reads: fixed GetSystemMetrics and two records."""

    def fake(index):
        return {76: 100, 77: 200, 7: 1920, 8: 1080, 2: 2}[index]

    monkeypatch.setattr(mapping.ctypes.windll.user32, "GetSystemMetrics", fake)
    monkeypatch.setattr(mapping, "monitor_records", lambda: list(RECORDS_DUO))


def test_metrics_readout_line_verbatim(metrics_env):
    assert metrics_readout_line() + "\n" == METRICS_LINE


def test_metrics_gate_on_emits_the_verbatim_line(metrics_env, monkeypatch, capsys):
    monkeypatch.setenv(METRICS_DIAG_ENV, "1")
    log_metrics_diagnostic()
    assert capsys.readouterr().out == METRICS_LINE


def test_metrics_gate_off_emits_nothing(metrics_env, monkeypatch, capsys):
    monkeypatch.delenv(METRICS_DIAG_ENV, raising=False)
    log_metrics_diagnostic()
    assert capsys.readouterr().out == ""


def test_pen_map_line_verbatim(monkeypatch, capsys):
    monkeypatch.setattr(mapping, "monitor_count", lambda: 1)
    state = TabletState()
    state.pen_product_id = 789
    state.pen_obs_max_x = 44704
    state.pen_obs_max_y = 27940
    config = DriverConfig()
    config.orientation_degrees = 0
    _log_pen_mapping_diagnostic(
        state, config, PEN_SPECS_DEFAULT, (0, 0, 2560, 1440), "isotropic",
        44704, 27940, (10.0, 10.0), (12.5, 3.0))
    assert capsys.readouterr().out == PEN_MAP_LINE
