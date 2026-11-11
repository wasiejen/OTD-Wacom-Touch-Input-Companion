"""U424 L2 (#424 rulings 1-3 + reviewer P2-2): tri-modal Pen tab.

One radio selector (relative | stretched | rect_1to1); stretched shows
per-screen checkboxes (default ALL = today, unselected screens not
reachable); rect_1to1 shows the canvas editor + numeric x/y/w/h fields
in px AND mm (bidirectional, aspect-locked, clamped to the display
union); Save/Remove stay inside the rect panel. The legacy absolute
checkbox is RETIRED - no dual control. Mode switches apply LIVE (ruling
3). Headless withdrawn root, test_wireless_tablet_size pattern.

RED-before b5f0840/305... : no pen_mode_var / stretch_screen_vars /
rect_entries / virtual_desktop_dpi on the built window.
"""

import tkinter as tk

import pytest

import gui
import touch_controller
from touch_controller import DriverConfig, TabletState, resolved_pen_mode

SCREEN = (0, 0, 3440, 1440)
DISP1 = "\\\\.\\DISPLAY1|0|0|1920x1080"
DISP2 = "\\\\.\\DISPLAY2|1920|0|1520x1440"
CONSTRUCTED = []


@pytest.fixture(scope="module")
def tk_root():
    root = tk.Tk()
    root.withdraw()
    yield root
    for win in CONSTRUCTED:
        try:
            win.destroy()
        except tk.TclError:
            pass


class StubBox:
    def showerror(self, title, message):
        pass


@pytest.fixture
def modes_cfg(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(gui, "monitor_records", lambda: [DISP1, DISP2])
    monkeypatch.setattr(gui, "virtual_desktop_metrics", lambda: SCREEN)
    monkeypatch.setattr(gui, "virtual_desktop_dpi", lambda: (96.0, 96.0))
    return DriverConfig()


def make_window(root, cfg, monkeypatch, tmp_path):
    monkeypatch.setattr(gui, "messagebox", StubBox())
    monkeypatch.setattr(gui, "ToastNotification", lambda *args, **kwargs: None)
    device_info = {"connection": {"config": cfg, "identity": "0123456789ab",
                                  "product_id": 791},
                   "base_config": cfg, "manager": None}
    win = gui.SettingsWindow(root, TabletState(), cfg, device_info)
    CONSTRUCTED.append(win)
    assert win.winfo_viewable() == 0
    return win


def test_mode_radios_exist_and_default_to_the_resolved_mode(modes_cfg, tk_root, monkeypatch, tmp_path):
    win = make_window(tk_root, modes_cfg, monkeypatch, tmp_path)
    assert win.pen_mode_var.get() == resolved_pen_mode(modes_cfg) == "rect_1to1"
    labels = [cb["text"] for cb in win.pen_mode_radios]
    assert any("Relative" in t for t in labels)
    assert any("stretch" in t.lower() for t in labels)
    assert any("1:1" in t for t in labels)


def test_mode_select_applies_live_and_swaps_panels(modes_cfg, tk_root, monkeypatch, tmp_path):
    win = make_window(tk_root, modes_cfg, monkeypatch, tmp_path)
    # rect_1to1 default: editor managed, stretch screen panel not.
    assert win.pen_rect_panel.winfo_manager() == "pack"
    assert not win.stretch_screen_panel.winfo_manager() == "pack"
    win.pen_mode_var.set("stretched")
    win._on_pen_mode_select()
    assert modes_cfg.pen_mode == "stretched"  # LIVE, no Save click
    assert not win.pen_rect_panel.winfo_manager() == "pack"
    assert win.stretch_screen_panel.winfo_manager() == "pack"
    win.pen_mode_var.set("relative")
    win._on_pen_mode_select()
    assert modes_cfg.pen_mode == "relative"
    assert not win.pen_rect_panel.winfo_manager() == "pack"
    assert not win.stretch_screen_panel.winfo_manager() == "pack"


def test_legacy_absolute_checkbox_retired_no_dual_control(modes_cfg, tk_root, monkeypatch, tmp_path):
    win = make_window(tk_root, modes_cfg, monkeypatch, tmp_path)
    assert not hasattr(win, "pen_absolute_var")
    assert "pen_absolute_mode" not in win.feature_vars


def test_stretch_screen_checkboxes_default_all_and_restrict_live(modes_cfg, tk_root, monkeypatch, tmp_path):
    win = make_window(tk_root, modes_cfg, monkeypatch, tmp_path)
    assert sorted(win.stretch_screen_vars) == sorted([DISP1, DISP2])
    assert all(var.get() for var in win.stretch_screen_vars.values())
    assert modes_cfg.pen_stretch_screens is None  # default all
    win.stretch_screen_vars[DISP1].set(False)
    win._on_stretch_screens_change()
    assert modes_cfg.pen_stretch_screens == [DISP2]
    win.stretch_screen_vars[DISP1].set(True)
    win.stretch_screen_vars[DISP2].set(True)
    win._on_stretch_screens_change()
    assert modes_cfg.pen_stretch_screens is None  # all -> full fill


def test_numeric_fields_bidirectional_aspect_locked(modes_cfg, tk_root, monkeypatch, tmp_path):
    win = make_window(tk_root, modes_cfg, monkeypatch, tmp_path)
    model = win.pen_area_editor.model
    box = model.box_mm
    win.rect_entries["w_px"].delete(0, tk.END)
    win.rect_entries["w_px"].insert(0, "1720")
    win._apply_rect_field("w_px")
    assert model.rect[2] == pytest.approx(1720.0)
    assert model.rect[3] == pytest.approx(1720.0 * box[1] / box[0], rel=1e-3)
    # px -> mm echo (96 dpi fixture: 1 mm = 96/25.4 px)
    assert float(win.rect_entries["w_mm"].get()) == pytest.approx(
        1720.0 * 25.4 / 96.0, rel=1e-3)
    # mm -> px direction
    win.rect_entries["x_mm"].delete(0, tk.END)
    win.rect_entries["x_mm"].insert(0, "254")  # 10 inches
    win._apply_rect_field("x_mm")
    assert model.rect[0] == pytest.approx(254.0 * 96.0 / 25.4)
    # clamp to the union: past the right edge lands ON the edge
    win.rect_entries["x_px"].delete(0, tk.END)
    win.rect_entries["x_px"].insert(0, "99999")
    win._apply_rect_field("x_px")
    assert model.rect[0] + model.rect[2] == pytest.approx(3440.0)


def test_numeric_fields_echo_the_stored_rect_on_open(modes_cfg, tk_root, monkeypatch, tmp_path):
    from touch_controller import display_key_for_records
    modes_cfg.set_pen_area_entry("0123456789ab",
                                 display_key_for_records([DISP1, DISP2]),
                                 [DISP1, DISP2],
                                 [0.25, 0.25, 0.5, 0.5])
    win = make_window(tk_root, modes_cfg, monkeypatch, tmp_path)
    assert float(win.rect_entries["x_px"].get()) == pytest.approx(860.0, abs=1)
    assert float(win.rect_entries["w_px"].get()) == pytest.approx(1720.0, abs=1)


def test_virtual_desktop_dpi_helper_returns_positive_pair(modes_cfg):
    dpi = gui.virtual_desktop_dpi()
    assert isinstance(dpi, tuple) and len(dpi) == 2
    assert all(isinstance(v, float) and v > 0 for v in dpi)
