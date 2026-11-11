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


def make_window(root, cfg, monkeypatch, tmp_path, product_id=791, connection=None):
    monkeypatch.setattr(gui, "messagebox", StubBox())
    monkeypatch.setattr(gui, "ToastNotification", lambda *args, **kwargs: None)
    device_info = {"connection": connection if connection is not None else
                   {"config": cfg, "identity": "0123456789ab",
                    "product_id": product_id},
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
    # U424r3 STEP-3 P2-1 close: an OLD stored entry also echoes its
    # effective sensitivity s = rw/box_mm_w on open (direct, not
    # composition).
    model = win.pen_area_editor.model
    assert float(win.rect_entries["s_px"].get()) == pytest.approx(
        model.rect[2] / model.box_mm[0], rel=1e-3)


# --- U424r3 R3-L1 (#424 round-3 item 1): contextual fields + stable place ---


def test_relative_speed_field_shows_only_in_relative_mode(modes_cfg, tk_root, monkeypatch, tmp_path):
    # RED-before 11a7939: the speed entry was always visible.
    win = make_window(tk_root, modes_cfg, monkeypatch, tmp_path)
    speed = win.entries["pen_speed"]
    assert speed.winfo_manager() == ""  # rect_1to1 default: hidden
    win.pen_mode_var.set("relative")
    win._on_pen_mode_select()
    assert speed.winfo_manager() == "grid"
    win.pen_mode_var.set("stretched")
    win._on_pen_mode_select()
    assert speed.winfo_manager() == ""
    # Mode-independent pressure threshold stays put in every mode.
    assert win.entries["pen_pressure_threshold"].winfo_manager() == "grid"


def test_rect_panel_keeps_its_place_across_mode_cycles(modes_cfg, tk_root, monkeypatch, tmp_path):
    # RED-before 11a7939: re-pack APPENDED the panel at the tab bottom.
    # Pin = real pack order (Tcl `pack slaves`), not creation order.
    win = make_window(tk_root, modes_cfg, monkeypatch, tmp_path)

    def slaves():
        # Pack order of DIRECT slaves, keyed by widget path tails (the Tcl
        # list items are window objects/paths depending on host version).
        raw = win.tk.call("pack", "slaves", str(win._pen_scroll_frame))
        return [str(item).rstrip("'>").split(".")[-1] for item in raw]

    # (U424r3 R3-L5: the wireless frame left the Pen tab - the stability
    # anchor is now the rect panel's own index in the full slave order;
    # a re-append moves it to the end and this index changes.)
    order0 = slaves()
    idx_rect = order0.index(win.pen_rect_panel._name)
    for mode in ("relative", "stretched", "relative", "rect_1to1"):
        win.pen_mode_var.set(mode)
        win._on_pen_mode_select()
    order1 = slaves()
    assert order1.index(win.pen_rect_panel._name) == idx_rect


# --- U424r3 R3-L3 (#424 round-3 item 3): bidirectional sync + px/mm handle ---


def test_sensitivity_field_echoes_and_edits_the_footprint(modes_cfg, tk_root, monkeypatch, tmp_path):
    # RED-before cc392fc: KeyError 's_px' (no sensitivity field).
    win = make_window(tk_root, modes_cfg, monkeypatch, tmp_path)
    model = win.pen_area_editor.model
    box_w = model.box_mm[0]
    s0 = model.rect[2] / box_w
    assert float(win.rect_entries["s_px"].get()) == pytest.approx(s0, rel=1e-3)
    cx = model.rect[0] + model.rect[2] / 2
    cy = model.rect[1] + model.rect[3] / 2
    win.rect_entries["s_px"].delete(0, tk.END)
    win.rect_entries["s_px"].insert(0, "0.5")
    win._apply_rect_field("s_px")
    assert model.rect[2] == pytest.approx(0.5 * box_w, rel=1e-6)
    assert (model.rect[0] + model.rect[2] / 2,
            model.rect[1] + model.rect[3] / 2) == pytest.approx((cx, cy))
    # and the echo follows the edit
    assert float(win.rect_entries["s_px"].get()) == pytest.approx(0.5, rel=1e-3)


def test_numeric_panel_labels_the_dpi_scaled_basis(modes_cfg, tk_root, monkeypatch, tmp_path):
    # U424r3 R3-L4 (#424 item 4): honest labeling after the basis
    # investigation. RED-before 3a902da: no basis hint existed.
    win = make_window(tk_root, modes_cfg, monkeypatch, tmp_path)
    texts = []
    stack = [win.pen_rect_panel]
    while stack:
        for child in stack.pop().winfo_children():
            if hasattr(child, "cget"):
                try:
                    texts.append(str(child.cget("text")))
                except tk.TclError:
                    pass
            stack.append(child)
    assert any("DPI-scaled" in t and "injection" in t for t in texts)


def test_rect_drag_updates_the_numeric_fields(modes_cfg, tk_root, monkeypatch, tmp_path):
    # Item 3a: drag/resize -> fields (one-way today). Driven through the
    # editor's own event handlers (no Tk event pump needed).
    win = make_window(tk_root, modes_cfg, monkeypatch, tmp_path)
    editor = win.pen_area_editor
    model = editor.model
    press = type("E", (), {"x": 5.0, "y": 5.0})()
    editor._press(press)
    editor._motion(type("E", (), {"x": 105.0, "y": 55.0})())
    assert float(win.rect_entries["x_px"].get()) == pytest.approx(model.rect[0], abs=1)
    assert float(win.rect_entries["y_px"].get()) == pytest.approx(model.rect[1], abs=1)
    assert float(win.rect_entries["s_px"].get()) == pytest.approx(
        model.rect[2] / model.box_mm[0], rel=1e-3)


# --- U424r3 R3-L5 (#424 round-3 item 5): wireless lane -> Focused Device ---


def test_wireless_lane_lives_in_focused_device_for_receiver_only(
        modes_cfg, tk_root, monkeypatch, tmp_path):
    # RED-before 8742a4f (scratch): lane was a Pen-tab child and ALWAYS
    # visible; here = hidden for the wired 791, gridded under Focused
    # Device for the receiver 132.
    win = make_window(tk_root, modes_cfg, monkeypatch, tmp_path)  # 791
    lane = win._pen_wireless_frame
    assert lane.winfo_manager() == ""
    win2 = make_window(tk_root, modes_cfg, monkeypatch, tmp_path,
                       product_id=132)
    lane2 = win2._pen_wireless_frame
    assert lane2.winfo_manager() == "grid"

    def is_descendant(widget, ancestor):  # no winfo_ancestors on this Tk
        master = widget.master
        while master is not None:
            if str(master) == str(ancestor):
                return True
            master = getattr(master, "master", None)
        return False

    assert is_descendant(lane2, win2._focused_device_frame)
    assert not is_descendant(lane2, win2._pen_scroll_frame)


def test_wireless_lane_syncs_dynamically_on_rebind(modes_cfg, tk_root, monkeypatch, tmp_path):
    win = make_window(tk_root, modes_cfg, monkeypatch, tmp_path)  # starts 791
    lane = win._pen_wireless_frame
    assert lane.winfo_manager() == ""
    win.device_info["connection"] = {"config": modes_cfg,
                                     "identity": "0123456789ab",
                                     "product_id": 132}
    win._rebind_focused_device()
    assert lane.winfo_manager() == "grid"
    win.device_info["connection"] = {"config": modes_cfg,
                                     "identity": "0123456789ab",
                                     "product_id": 791}
    win._rebind_focused_device()
    assert lane.winfo_manager() == ""


# --- U424r3 R3-L2 (#424 round-3 item 2): Apply Live = single apply path ----


def test_save_pen_area_button_gone_remove_rect_stays(modes_cfg, tk_root, monkeypatch, tmp_path):
    # RED-before 6c10b03: the editor still rendered "Save Pen Area".
    win = make_window(tk_root, modes_cfg, monkeypatch, tmp_path)
    texts = []
    for child in win.pen_area_editor.winfo_children():
        if hasattr(child, "cget"):
            try:
                texts.append(str(child.cget("text")))
            except tk.TclError:
                pass
    assert "Save Pen Area" not in texts
    assert "Remove Rect" in texts


def test_apply_writes_the_rect_entry_like_other_settings(modes_cfg, tk_root, monkeypatch, tmp_path):
    win = make_window(tk_root, modes_cfg, monkeypatch, tmp_path)
    model = win.pen_area_editor.model
    model.rect = [0.0, 0.0, model.desktop_w / 2, model.desktop_h / 2]
    assert win.apply_settings() is True
    entries = modes_cfg.pen_area_maps["0123456789ab"]["entries"]
    frac = entries[0]["rect_frac"]
    assert frac == pytest.approx([0.0, 0.0, 0.5, 0.5])


def test_remove_rect_does_not_switch_modes(modes_cfg, tk_root, monkeypatch, tmp_path):
    # Panel semantics: Remove drops the entry, the mode stays 1:1.
    win = make_window(tk_root, modes_cfg, monkeypatch, tmp_path)
    assert win.apply_settings() is True  # seed an entry via Apply
    assert win.pen_area_editor.remove_entry() is True
    assert modes_cfg.pen_mode == "rect_1to1"
    assert win.pen_rect_panel.winfo_manager() == "pack"
    assert not modes_cfg.pen_area_maps.get("0123456789ab", {}).get("entries")


# --- U424r4 R4-B (#424 round-4 item B): orientation sync (pen path wins) ---


def test_orientation_apply_reorients_editor_box_not_the_footprint(
        modes_cfg, tk_root, monkeypatch, tmp_path):
    # RED-before c8189e8: box stayed the 0-deg layout after applying 90.
    from touch_controller import pen_specs_for_product, rotated_pen_box_mm
    win = make_window(tk_root, modes_cfg, monkeypatch, tmp_path)
    model = win.pen_area_editor.model
    specs = pen_specs_for_product(791, modes_cfg)
    assert model.box_mm == pytest.approx(rotated_pen_box_mm(specs, 0))
    footprint_px = list(model.rect)
    win.entries["orientation_degrees"].delete(0, tk.END)
    win.entries["orientation_degrees"].insert(0, "90")
    assert win.apply_settings() is True
    assert modes_cfg.orientation_degrees == 90
    # Editor agrees with the pen: rotated box mm...
    assert model.box_mm == pytest.approx(rotated_pen_box_mm(specs, 90))
    assert model.box_mm[0] == pytest.approx(rotated_pen_box_mm(specs, 0)[1])
    # ...and the SAME rect px footprint the pen maps (pen path wins).
    assert model.rect == footprint_px
    # Sensitivity echo follows the new box (w axis).
    assert float(win.rect_entries["s_px"].get()) == pytest.approx(
        model.rect[2] / model.box_mm[0], rel=1e-3)


def test_editor_built_after_orientation_shows_rotated_box(modes_cfg, tk_root, monkeypatch, tmp_path):
    from touch_controller import pen_specs_for_product, rotated_pen_box_mm
    modes_cfg.orientation_degrees = 180
    win = make_window(tk_root, modes_cfg, monkeypatch, tmp_path)
    specs = pen_specs_for_product(791, modes_cfg)
    assert win.pen_area_editor.model.box_mm == pytest.approx(
        rotated_pen_box_mm(specs, 180))


# --- U424r4 R4-C (#424 round-4 item C): cabled->wireless switch pickup ---


def _live_pair(cfg, product_id):
    return {"config": cfg, "identity": f"0123456789{product_id}",
            "product_id": product_id}


def test_build_adopts_live_wireless_when_stored_focus_is_dead(
        modes_cfg, tk_root, monkeypatch, tmp_path):
    # RED-before 1234b34: _build_device_bar trusted the tray's stale
    # (dead cabled) connection dict -> picker hidden though wireless
    # was live. Reopen-after-swap path.
    import gui as gui_mod
    cabled = _live_pair(modes_cfg, 791)
    win = make_window(tk_root, modes_cfg, monkeypatch, tmp_path,
                      connection=cabled)
    wireless = _live_pair(modes_cfg, 132)
    monkeypatch.setattr(win, "_live_connections", lambda: [wireless])
    win._adopt_stale_focus()
    assert win.device_info["connection"] is wireless
    assert win._pen_wireless_frame.winfo_manager() == "grid"


def test_window_open_switch_refresh_adopts_wireless_shows_picker(
        modes_cfg, tk_root, monkeypatch, tmp_path):
    # Window open during the swap: refresh adopts wireless -> rebind ->
    # lane visible (the missed-hook fix also rides the 5 s poll).
    win = make_window(tk_root, modes_cfg, monkeypatch, tmp_path)  # 791
    assert win._pen_wireless_frame.winfo_manager() == ""
    wireless = _live_pair(modes_cfg, 132)
    monkeypatch.setattr(win, "_live_connections", lambda: [wireless])
    monkeypatch.setattr(gui, "ToastNotification", lambda *a, **k: None)
    win._refresh_device_combo()
    assert win.device_info["connection"] is wireless
    assert win._pen_wireless_frame.winfo_manager() == "grid"


def test_device_poll_reschedules(modes_cfg, tk_root, monkeypatch, tmp_path):
    # RED-before 1234b34: no poll existed (_device_poll_ms missing).
    win = make_window(tk_root, modes_cfg, monkeypatch, tmp_path)
    assert win._device_poll_ms == 5000
    scheduled = []
    monkeypatch.setattr(win, "after",
                        lambda ms, fn, *a: scheduled.append((ms, fn)))
    monkeypatch.setattr(win, "_refresh_device_combo", lambda: None)
    win._device_poll_tick()
    assert scheduled and scheduled[0][0] == 5000


def test_subset_checkboxes_seed_from_the_stored_subset(modes_cfg, tk_root, monkeypatch, tmp_path):
    # U424 UNIT VERDICT P1 (RED-before 62483be: vars were hardcoded True,
    # the UI lied about a stored restriction and one click silently
    # broadened the stretch target).
    modes_cfg.pen_stretch_screens = [DISP2]
    win = make_window(tk_root, modes_cfg, monkeypatch, tmp_path)
    assert win.stretch_screen_vars[DISP1].get() is False
    assert win.stretch_screen_vars[DISP2].get() is True
    assert win.stretch_screen_panel.winfo_manager() != "pack"  # mode-gated
    win.stretch_screen_vars[DISP1].set(True)
    win._on_stretch_screens_change()
    assert modes_cfg.pen_stretch_screens is None  # all selected again


def test_legacy_stretch_toggle_absent_from_feature_vars(modes_cfg, tk_root, monkeypatch, tmp_path):
    # U424 UNIT VERDICT P2-2: the raw pen_stretch_full_desktop checkbox
    # must not render via the generic feature loop either (retired).
    win = make_window(tk_root, modes_cfg, monkeypatch, tmp_path)
    assert "pen_stretch_full_desktop" not in win.feature_vars


def test_editor_view_stays_put_across_edge_rect_moves(modes_cfg, tk_root, monkeypatch, tmp_path):
    # U424 L3 (#424 canvas jitter): the view transform must derive from
    # the SCREEN BOUNDS once, and the canvas must not slide when the
    # rect's readout text changes width. RED-before 0e28886: the frame
    # request width oscillated with the rect position (rect-dependent
    # label text width re-centered the fixed-size canvas in its cell).
    win = make_window(tk_root, modes_cfg, monkeypatch, tmp_path)
    editor = win.pen_area_editor
    model = editor.model
    scale0 = model.scale
    x0 = model.monitor_canvas_rects()[0][0]
    w0 = editor.winfo_reqwidth()
    rw, rh = model.rect[2], model.rect[3]
    model.rect = [0.0, 0.0, rw, rh]
    editor._redraw()
    assert editor.winfo_reqwidth() == w0, "view row resized by left-edge readout"
    model.rect = [model.desktop_w - rw, model.desktop_h - rh, rw, rh]
    editor._redraw()
    assert editor.winfo_reqwidth() == w0, "view row resized by far-edge readout"
    assert model.scale == scale0
    assert model.monitor_canvas_rects()[0][0] == x0


def test_virtual_desktop_dpi_helper_returns_positive_pair(modes_cfg):
    dpi = gui.virtual_desktop_dpi()
    assert isinstance(dpi, tuple) and len(dpi) == 2
    assert all(isinstance(v, float) and v > 0 for v in dpi)
