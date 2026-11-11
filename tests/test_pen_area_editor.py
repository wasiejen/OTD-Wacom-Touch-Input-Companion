"""U414 L3 (#406, option A) + U416 L3 (#406 defect round 1): rect editor.

Model pins run headless (pure `PenAreaCanvasModel` math, no tkinter); the
widget pins use the module-scoped hidden Tk root like test_gui_device_bar.

U416 (maintainer host defect: black canvas + "No display geometry ..." on
the junk-metrics host): the editor now BUILDS its model always - bounds
from virtual metrics, else the EnumDisplayMonitors union, else a single
fallback rectangle with the pad's aspect - and the stored rect is
FRACTIONAL. Device identity is required only at SAVE time (it names the
lane). RED-before 03c0a40: the old inert-without-metrics gate + rect_px
save.
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


def test_apply_scale_resizes_footprint_at_fixed_center():
    # U424r3 R3-L3 (#424 item 3b): sensitivity is an editable handle on
    # the footprint. RED-before cc392fc: AttributeError apply_scale.
    m = _model()
    cx = m.rect[0] + m.rect[2] / 2
    cy = m.rect[1] + m.rect[3] / 2
    m.apply_scale(2.0)
    assert m.rect[2] == pytest.approx(2.0 * BOX_MM[0], rel=1e-9)
    assert m.rect[3] == pytest.approx(2.0 * BOX_MM[1], rel=1e-9)
    assert (m.rect[0] + m.rect[2] / 2, m.rect[1] + m.rect[3] / 2) == \
        pytest.approx((cx, cy))


def test_apply_scale_clamps_inside_the_desktop():
    m = _model()
    m.apply_scale(1000.0)  # absurd px/mm -> clamp to the desktop width
    assert m.rect[2] == pytest.approx(SCREEN[2])
    assert m.rect[0] == pytest.approx(0.0)


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


def test_widget_save_writes_fractional_entry_for_current_display_key(root, widget_config, monkeypatch):
    calls = []
    monkeypatch.setattr(gui, "reset_pen_area_key_cache", lambda: calls.append(1))
    widget = _build_widget(root, widget_config, monkeypatch)
    assert widget.persist_entry() is True
    entry = list(widget_config.pen_area_maps["0123456789ab"]["entries"])[0]
    assert entry["display_key"] == touch_controller.display_key_for_records(RECORDS)
    assert entry["monitors"] == RECORDS
    rect_frac = entry["rect_frac"]
    assert len(rect_frac) == 4
    assert 0.0 <= rect_frac[0] <= 1.0 and rect_frac[2] > 0.0 and rect_frac[2] <= 1.0
    assert rect_frac[0] + rect_frac[2] <= 1.000001
    assert calls == [1]


def test_widget_editable_without_identity_save_refuses(root, widget_config, monkeypatch):
    # U416: identity names the LANE, so it gates SAVE only. Editing must
    # work without it (the model is live, drag math runs).
    monkeypatch.setattr(gui, "virtual_desktop_metrics", lambda: SCREEN)
    monkeypatch.setattr(gui, "monitor_records", lambda: list(RECORDS))
    device_info = {"connection": {"product_id": 791}, "base_config": widget_config}
    widget = gui.PenAreaEditor(root, widget_config, device_info)
    assert widget.model is not None
    widget.model.move_by(100000, 0)
    assert widget.model.rect[0] == 3440 - widget.model.rect[2]  # drag clamped
    assert widget.persist_entry() is False
    assert widget.remove_entry() is False
    assert widget_config.pen_area_maps == {}


def test_widget_builds_on_metrics_less_host_from_records(root, widget_config, monkeypatch):
    # U416 defect core repro: junk/None virtual metrics (maintainer host).
    # Bounds fall back to the EnumDisplayMonitors union; canvas usable.
    monkeypatch.setattr(gui, "virtual_desktop_metrics", lambda: None)
    monkeypatch.setattr(gui, "monitor_records", lambda: list(RECORDS))
    device_info = {"connection": {"identity": "0123456789ab", "product_id": 791},
                   "base_config": widget_config}
    widget = gui.PenAreaEditor(root, widget_config, device_info)
    assert widget.model is not None
    assert (widget.model.desktop_w, widget.model.desktop_h) == (3440.0, 1440.0)
    widget.model.move_by(100000, 0)
    assert widget.model.rect[0] == 3440 - widget.model.rect[2]
    assert widget.persist_entry() is True


def test_widget_falls_back_to_pad_aspect_rect(root, widget_config, monkeypatch):
    # U416 last resort: NO metrics AND no usable enumeration -> the canvas
    # still renders a single virtual-desktop rectangle with the pad's
    # aspect (fractions stay load-stable via the empty-records display key).
    monkeypatch.setattr(gui, "virtual_desktop_metrics", lambda: None)
    monkeypatch.setattr(gui, "monitor_records", lambda: [])
    device_info = {"connection": {"identity": "0123456789ab", "product_id": 791},
                   "base_config": widget_config}
    widget = gui.PenAreaEditor(root, widget_config, device_info)
    assert widget.model is not None
    box = touch_controller.rotated_pen_box_mm(
        touch_controller.PEN_SPECS_BY_PRODUCT[791], 0)
    assert widget.model.desktop_w / widget.model.desktop_h == pytest.approx(
        box[0] / box[1], rel=1e-6)
    assert len(widget.model.monitor_canvas_rects()) == 1
    assert widget.persist_entry() is True
    entry = list(widget_config.pen_area_maps["0123456789ab"]["entries"])[0]
    assert entry["monitors"] == []
    assert entry["display_key"] == touch_controller.display_key_for_records([])


def test_widget_loads_stored_fraction_back_into_model_px(root, widget_config, monkeypatch):
    # Fraction round-trip through the editor: saved fraction -> model px
    # on reopen matches the same bounds.
    monkeypatch.setattr(gui, "virtual_desktop_metrics", lambda: SCREEN)
    monkeypatch.setattr(gui, "monitor_records", lambda: list(RECORDS))
    device_info = {"connection": {"identity": "0123456789ab", "product_id": 791},
                   "base_config": widget_config}
    widget = gui.PenAreaEditor(root, widget_config, device_info)
    widget.model.rect = [516.0, 144.0, 1720.0, 720.0]  # 16:10-ish; aspect may lock
    assert widget.persist_entry() is True
    key = touch_controller.display_key_for_records(RECORDS)
    frac = widget_config.pen_area_maps["0123456789ab"]["entries"][0]["rect_frac"]
    reopened = gui.PenAreaEditor(root, widget_config, device_info)
    assert reopened.model.rect == pytest.approx([frac[0] * 3440, frac[1] * 1440,
                                                 frac[2] * 3440, frac[3] * 1440])
