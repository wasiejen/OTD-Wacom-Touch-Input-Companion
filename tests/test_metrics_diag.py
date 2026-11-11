"""U412 (#412): virtual-screen metrics readout diagnostic.

Opt-in (env WTD_METRICS_DIAG=1, same shape as WTD_PEN_MAP_DIAG): ONE line
at startup with the raw SM_*VIRTUALSCREEN returns (junk passes through
unsanitized - the whole point of the probe), SM_CMONITORS, the ctypes call
path, the EnumDisplayMonitors monitor count, and the union rect. No
mapping change; default OFF is byte-unchanged behavior. RED-before at
75c25ec: ImportError on the new API names.
"""

import pytest

import touch_controller
from touch_controller import (
    METRICS_DIAG_ENV,
    log_metrics_diagnostic,
    metrics_diag_enabled,
    metrics_readout_line,
    union_rect_from_records,
)

RECORDS_DUO = ["\\\\.\\DISPLAY1|0|0|3440x1440", "\\\\.\\DISPLAY2|3440|0|1440x2560"]


@pytest.fixture
def windll_table(monkeypatch):
    """Fake GetSystemMetrics returning a table; None entries raise."""

    def _install(table):
        def fake(index):
            value = table[index]
            if isinstance(value, Exception):
                raise value
            return value

        monkeypatch.setattr(touch_controller.ctypes.windll.user32,
                            "GetSystemMetrics", fake)
        return table

    return _install


def test_gate_off_by_default_and_on_with_env(monkeypatch):
    monkeypatch.delenv(METRICS_DIAG_ENV, raising=False)
    assert metrics_diag_enabled() is False
    monkeypatch.setenv(METRICS_DIAG_ENV, "1")
    assert metrics_diag_enabled() is True


def test_default_off_prints_nothing(windll_table, monkeypatch, capsys):
    # Control pin: without the env the startup hook emits no line even when
    # the metric reads succeed (no behavior change for normal users).
    windll_table({76: 0, 77: 0, 7: 3440, 8: 1440, 2: 2})
    monkeypatch.setattr(touch_controller, "monitor_records", lambda: list(RECORDS_DUO))
    monkeypatch.delenv(METRICS_DIAG_ENV, raising=False)
    log_metrics_diagnostic()
    assert capsys.readouterr().out == ""


def test_readout_line_is_byte_exact_with_junk_pass_through(windll_table, monkeypatch,
                                                           capsys):
    # The maintainer's suspected host shape: all four virtual-screen reads
    # return junk 0 while SM_CMONITORS answers 17. Values pass through
    # unsanitized; the union comes from the monitor records.
    windll_table({76: 0, 77: 0, 7: 0, 8: 0, 2: 17})
    monkeypatch.setattr(touch_controller, "monitor_records", lambda: list(RECORDS_DUO))
    monkeypatch.setenv(METRICS_DIAG_ENV, "1")
    log_metrics_diagnostic()
    assert capsys.readouterr().out == (
        "wtd_metrics_diag SM_XVIRTUALSCREEN=0 SM_YVIRTUALSCREEN=0 "
        "SM_CXVIRTUALSCREEN=0 SM_CYVIRTUALSCREEN=0 SM_CMONITORS=17 "
        "call=user32.GetSystemMetrics monitors=2 union=0,0,4880x2560\n")


def test_readout_names_exceptions_as_none(windll_table, monkeypatch):
    boom = OSError("no metrics")
    table = windll_table({76: boom, 77: 0, 7: 3, 8: 3, 2: 1})
    monkeypatch.setattr(touch_controller, "monitor_records", lambda: [])
    line = metrics_readout_line()
    assert "SM_XVIRTUALSCREEN=None" in line
    assert "SM_CXVIRTUALSCREEN=3 SM_CYVIRTUALSCREEN=3" in line
    assert "monitors=0 union=none" in line


def test_union_rect_math_including_negative_origin():
    # Pivoted-duo shape: left monitor at a negative virtual origin.
    records = ["\\\\.\\DISPLAY2|-1440|0|1440x2560", "\\\\.\\DISPLAY1|0|0|3440x1440"]
    assert union_rect_from_records(records) == (-1440, 0, 4880, 2560)
    assert union_rect_from_records([]) is None
    assert union_rect_from_records(["broken row"]) is None
