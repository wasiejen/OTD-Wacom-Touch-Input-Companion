"""U414 L3 (#406, option A): pen-area rect canvas editor.

Model pins run headless (pure `PenAreaCanvasModel` math, no tkinter); the
widget pins use the module-scoped hidden Tk root like test_gui_device_bar.
RED-before at 1b9ca84: ImportError on `PenAreaCanvasModel` /
`PenAreaEditor` from gui.
"""

import pytest

import gui
import touch_controller
from gui import PenAreaCanvasModel
from touch_controller import pen_area_rect_aspect_lock

SCREEN = (0, 0, 3440, 1440)
ORIGIN_SCREEN = (-1440, 0, 4880, 2560)
RECORDS = ["\\\\.\\DISPLAY1|0|0|3440x1440"]
RECORDS_DUO = ["\\\\.\\DISPLAY1|0|0|3440x1440", "\\\\.\\DISPLAY2|3440|0|1440x2560"]
RECT = [500, 120, 1920, 1200]
BOX_MM = (325.12, 203.2)
CANVAS = (320.0, 120.0)


def _model(metrics=SCREEN, records=None, rect=RECT):
    return PenAreaCanvasModel(metrics, records if records is not None else RECORDS,
                              CANVAS[0], CANVAS[1], BOX_MM[0], BOX_MM[1], rect)


def test_aspect_lock_helper_rederives_rh():
    assert pen_area_rect_aspect_lock([0, 0, 1000, 7], 325.12, 203.2) == \
        [0, 0, 1000, 625]
    assert pen_area_rect_aspect_lock([10, 20, 160, 99], 16.0, 10.0) == \
        [10, 20, 160, 100]


def test_model_scale_and_canvas_rect_round_trip():
    model = _model()
    scale = min(320.0 / 3440, 120.0 / 1440)
    assert model.scale == pytest.approx(scale)
    x0, y0, x1, y1 = model.rect_canvas()
    assert (x0 / scale, y0 / scale) == pytest.approx((RECT[0], RECT[1]))
    assert ((x1 - x0) / scale, (y1 - y0) / scale) == pytest.approx((RECT[2], RECT[3]))


def test_model_monitor_outlines_use_records_and_origin():
    model = _model(ORIGIN_SCREEN, RECORDS_DUO)
    boxes = model.monitor_canvas_rects()
    scale = min(320.0 / 4880, 120.0 / 2560)
    assert len(boxes) == 2
    assert boxes[0][0] == pytest.approx((0 - (-1440)) * scale)
    assert boxes[1][0] == pytest.approx((3440 - (-1440)) * scale)


def test_model_drag_clamps_to_desktop():
    model = _model()
    model.move_by(100000, 0)  # push far past the right edge: clamps
    assert model.rect[0] == 3440 - RECT[2]
    model.move_by(-100000, 0)
    assert model.rect[0] == 0


def test_model_handle_resize_locks_aspect_and_clamps():
    model = _model()
    handle = model.handle_canvas()
    model.resize_handle_to(handle[0] + 100 * model.scale, handle[1] + 100 * model.scale)
    assert model.rect[3] == round(model.rect[2] * BOX_MM[1] / BOX_MM[0])
    model.resize_handle_to(100000.0, 100000.0)
    assert model.rect[0] + model.rect[2] <= 3440
    assert model.rect[1] + model.rect[3] <= 1440
    assert model.rect[3] == round(model.rect[2] * BOX_MM[1] / BOX_MM[0])


def test_model_readout_names_px_and_mm():
    out = _model().readout()
    assert "1920x1200 px" in out
    assert "5.9055 px/mm" in out  # 1920 / 325.12 = 5.90551...


@pytest.fixture(scope="module")
def root():
    # Same pattern as tests/test_gui_device_bar.py: one withdrawn root.
    import tkinter as tk
    root = tk.Tk()
    root.withdraw()
    yield root
    root.destroy()


@pytest.fixture
def widget_config(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    return touch_controller.DriverConfig()


def _build_widget(root, widget_config, monkeypatch, metrics=SCREEN):
    monkeypatch.setattr(gui, "virtual_desktop_metrics", lambda: metrics)
    monkeypatch.setattr(gui, "monitor_records", lambda: list(RECORDS))
    touch_controller.reset_pen_area_key_cache()
    device_info = {"connection": {"identity": "0123456789ab", "product_id": 791},
                   "base_config": widget_config}
    return gui.PenAreaEditor(root, widget_config, device_info)


def test_widget_save_writes_entry_for_current_display_key(root, widget_config, monkeypatch):
    calls = []
    monkeypatch.setattr(gui, "reset_pen_area_key_cache", lambda: calls.append(1))
    widget = _build_widget(root, widget_config, monkeypatch)
    assert widget.save_entry() is True
    entry = list(widget_config.pen_area_maps["0123456789ab"]["entries"])[0]
    assert entry["display_key"] == touch_controller.display_key_for_records(RECORDS)
    assert entry["monitors"] == RECORDS
    assert len(entry["rect_px"]) == 4 and entry["rect_px"][2] > 0
    assert calls == [1]


def test_widget_save_disabled_without_identity(root, widget_config, monkeypatch):
    monkeypatch.setattr(gui, "virtual_desktop_metrics", lambda: SCREEN)
    monkeypatch.setattr(gui, "monitor_records", lambda: list(RECORDS))
    device_info = {"connection": {"product_id": 791}, "base_config": widget_config}
    widget = gui.PenAreaEditor(root, widget_config, device_info)
    assert widget.save_entry() is False
    assert widget_config.pen_area_maps == {}


def test_widget_disabled_without_metrics(root, widget_config, monkeypatch):
    monkeypatch.setattr(gui, "virtual_desktop_metrics", lambda: None)
    monkeypatch.setattr(gui, "monitor_records", lambda: list(RECORDS))
    device_info = {"connection": {"identity": "0123456789ab", "product_id": 791},
                   "base_config": widget_config}
    widget = gui.PenAreaEditor(root, widget_config, device_info)
    assert widget.model is None
    assert widget.save_entry() is False
