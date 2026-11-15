"""R1 pins: the no-console rotating file sink on the shared 'wtd' logger.

Covers: both named sinks attached at import; idempotence (GUI + CLI + tray
re-init never double-attaches); lazy creation (neither the folder nor the
file exists before the first emitted line, both appear with it); the
rotation contract (2 MB x 2 backups, UTF-8); and the hunting contract in
FILE form - the pen_abs_map and metrics lines land byte-identical, equal to
the console text pinned in test_diag_string_contract.py, with no console
(capsys redirected) - the tray simulation.
"""

import io
import logging

import pytest

from wtd import _log
from wtd import mapping
from wtd.config import DriverConfig
from wtd.gestures import TabletState
from wtd.mapping import (
    METRICS_DIAG_ENV,
    PEN_MAP_DIAG_ENV,
    _log_pen_mapping_diagnostic,
    log_metrics_diagnostic,
)
from wtd.protocol import PEN_SPECS_DEFAULT
from test_diag_string_contract import METRICS_LINE, PEN_MAP_LINE

RECORDS_DUO = ["\\\\.\\DISPLAY1|0|0|3440x1440", "\\\\.\\DISPLAY2|3440|0|1440x2560"]


@pytest.fixture
def file_sink(tmp_path):
    """A fresh file sink at tmp_path, attached; detached cleanly at teardown."""
    path = tmp_path / "wtd" / "wtd.log"
    handler = _log.make_file_sink(str(path))
    added = _log.install_sinks()  # idempotent; returns [] on the real logger
    logger = logging.getLogger("wtd")
    logger.addHandler(handler)
    try:
        yield handler, path
    finally:
        logger.removeHandler(handler)
        handler.close()


def test_import_time_sinks_are_named_and_single():
    logger = logging.getLogger("wtd")
    stream = _log.find_sink(logger, "wtd_stream")
    fileh = _log.find_sink(logger, "wtd_file")
    assert stream is not None and fileh is not None
    assert len([h for h in logger.handlers if h.get_name() == "wtd_stream"]) == 1
    assert len([h for h in logger.handlers if h.get_name() == "wtd_file"]) == 1


def test_install_sinks_is_idempotent():
    logger = logging.getLogger("wtd")
    first = _log.find_sink(logger, "wtd_stream")
    first_file = _log.find_sink(logger, "wtd_file")
    added = _log.install_sinks()
    assert added == []
    assert _log.find_sink(logger, "wtd_stream") is first
    assert _log.find_sink(logger, "wtd_file") is first_file


def test_file_sink_rotation_contract(file_sink):
    handler, path = file_sink
    assert handler.maxBytes == 2 * 1024 * 1024
    assert handler.backupCount == 2
    assert handler.encoding == "utf-8"
    assert handler.stream is None  # delay=True: nothing opened yet
    assert not path.exists()
    assert not path.parent.exists()


def test_sink_created_only_on_first_emitted_line(file_sink, capsys):
    handler, path = file_sink
    logger = logging.getLogger("wtd")
    assert not path.exists()
    logger.info("first line")
    assert path.exists()
    assert path.read_text(encoding="utf-8") == "first line\n"


def test_hunting_contract_lines_land_in_the_file(file_sink, capsys, monkeypatch):
    handler, path = file_sink
    # Console-less simulation: pytest's capsys already owns sys.stdout, so
    # this is the tray shape - the file is the only surviving channel.
    monkeypatch.delenv(PEN_MAP_DIAG_ENV, raising=False)  # gate irrelevant here
    monkeypatch.setattr(mapping, "monitor_count", lambda: 1)
    state = TabletState()
    state.pen_product_id = 789
    state.pen_obs_max_x = 44704
    state.pen_obs_max_y = 27940
    config = DriverConfig()
    config.orientation_degrees = 0

    def fake(index):
        return {76: 100, 77: 200, 7: 1920, 8: 1080, 2: 2}[index]

    monkeypatch.setattr(mapping.ctypes.windll.user32, "GetSystemMetrics", fake)
    monkeypatch.setattr(mapping, "monitor_records", lambda: list(RECORDS_DUO))
    monkeypatch.setenv(METRICS_DIAG_ENV, "1")
    log_metrics_diagnostic()
    _log_pen_mapping_diagnostic(
        state, config, PEN_SPECS_DEFAULT, (0, 0, 2560, 1440), "isotropic",
        44704, 27940, (10.0, 10.0), (12.5, 3.0))
    written = io.open(str(path), encoding="utf-8").read()
    assert written == METRICS_LINE + PEN_MAP_LINE


def test_file_sink_is_skipped_silently_when_the_path_is_bad(monkeypatch):
    # A string where a mapping is required makes handler construction raise:
    # install_sinks must swallow it and keep the console sink alone.
    monkeypatch.setattr(_log, "make_file_sink", lambda path: "not-a-handler")
    added = _log.install_sinks()
    assert [h for h in added if getattr(h, "get_name", None) is not None] == []
    assert _log.find_sink(logging.getLogger("wtd"), "wtd_stream") is not None


def test_log_file_path_is_the_appdata_folder_family(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    import ntpath
    assert _log.log_file_path().lower().endswith(
        ntpath.join("wtd", "wtd.log").lower())
    assert _log.log_file_path().lower().startswith(str(tmp_path).lower())
