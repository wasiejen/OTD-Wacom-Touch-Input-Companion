"""D1 pins: identity-override hint and the Restore-to-Defaults lane (R4-a).

Headless SettingsWindow pins on a withdrawn root (U383 shape): the hint
states the focused identity's active config sections computed from the base
config; the reset removes the device_settings entry ONLY (pen area maps and
aliases untouched), re-forks the LIVE config object from base in place
(readers never restart), asks first NAMING the sections, changes the FILE
only at the next save, and leaves other devices untouched.
"""

import sys

import pytest

import gui
from gui import device_display_name  # noqa: F401  (name pin: module intact)
from wtd.config import DriverConfig, device_identity_hash

WACOM_PATH = "WACOM Pen&0b57:05a3"
_ROOT = [None]


class BoxLog:
    """messagebox stub: records dialogs; askyesno answers from a plan."""

    def __init__(self, answer=True):
        self.answer = answer
        self.errors = []
        self.infos = []
        self.asks = []

    def showerror(self, title, message):
        self.errors.append((title, message))

    def showinfo(self, title, message):
        self.infos.append((title, message))

    def askyesno(self, title, message):
        self.asks.append((title, message))
        return self.answer


def _root():
    # WM-103: create the process's first Tk once, never destroy it; reuse a
    # sibling test module's withdrawn root when that one already exists.
    if _ROOT[0] is not None and _ROOT[0].winfo_exists():
        return _ROOT[0]
    for mod in list(sys.modules.values()):
        root = getattr(mod, "_tk_root", None) if isinstance(mod, object) else None
        root = getattr(mod, "root", None) or root
        candidate = getattr(mod, "_tk_root", None)
        if candidate is not None and candidate.winfo_exists():
            return candidate
    import tkinter as tk
    root = tk.Tk()
    root.withdraw()
    _ROOT[0] = root
    return root


def _cfg(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return DriverConfig()


def _make_window(root, cfg, monkeypatch, conns, focused):
    monkeypatch.setattr(gui, "messagebox", BoxLog())
    monkeypatch.setattr(gui, "ToastNotification", lambda *a, **k: None)
    class _Manager:
        def list_connections(self_inner):
            return list(conns)
    device_info = {"connection": focused, "base_config": cfg,
                   "manager": _Manager()}
    win = gui.SettingsWindow(root, __import__("wtd.gestures", fromlist=["TabletState"]).TabletState(),
                             focused["config"], device_info)
    assert win.winfo_viewable() == 0
    return win


def _conn(cfg, path, product_id=789):
    return {"config": cfg.fork_for_device(None, device_identity_hash(path)),
            "identity": device_identity_hash(path), "product_id": product_id}


@pytest.fixture
def env(tmp_path, monkeypatch):
    root = _root()
    cfg = _cfg(tmp_path, monkeypatch)
    ident_a = device_identity_hash(WACOM_PATH)
    ident_b = device_identity_hash("WACOM Second&0b57:0999")
    cfg.set_device_identity_settings(ident_a, {
        "motion_and_thresholds": {"pen_speed": 4.0},
        "feature_toggles": {"pen_absolute_mode": False},
    })
    cfg.pen_area_maps[ident_a] = {"entries": [{"alias": "keep-me", "rect": [0, 0, 10, 10]}]}
    cfg.set_device_alias(ident_a, "Alpha")
    cfg.set_device_alias(ident_b, "Beta")
    cfg.set_device_identity_settings(ident_b, {
        "action_mapping": {"1f_tap": "right_click"}})
    conn_a = _conn(cfg, WACOM_PATH)
    conn_b = _conn(cfg, "WACOM Second&0b57:0999", product_id=788)
    conn_c = _conn(cfg, "WACOM Third&0b57:0777", product_id=791)
    win = _make_window(root, cfg, monkeypatch,
                       [conn_a, conn_b, conn_c], conn_a)
    return cfg, win, conn_a, conn_b, conn_c, ident_a, ident_b


def test_hint_names_active_sections_computed_from_config(env):
    cfg, win, _, _, _, ident_a, _ = env
    assert hasattr(win, "identity_hint_label")
    assert win.identity_hint_label.cget("text") == (
        "Device-specific values active: feature_toggles, motion_and_thresholds")
    # Honest = computed NOW: strip the entry behind the widget and refresh.
    del cfg.device_settings[ident_a]
    win._refresh_identity_hint()
    assert win.identity_hint_label.cget("text") == (
        "Device-specific values active: (none)")


def test_hint_follows_focus_change(env):
    cfg, win, conn_a, conn_b, _, _, _ = env
    win._device_conns = [conn_a, conn_b]
    win.devices_combo.current(1)
    assert win._on_device_selected() is True
    assert win.identity_hint_label.cget("text") == (
        "Device-specific values active: action_mapping")
    win.devices_combo.current(0)
    assert win._on_device_selected() is True
    assert "motion_and_thresholds" in win.identity_hint_label.cget("text")


def test_reset_cancel_changes_nothing(env, monkeypatch):
    cfg, win, conn_a, _, _, ident_a, _ = env
    box = BoxLog(answer=False)
    monkeypatch.setattr(gui, "messagebox", box)
    entry_before = dict(cfg.device_settings[ident_a])
    pen_before = dict(cfg.pen_area_maps[ident_a])
    live_pen_speed = conn_a["config"].pen_speed
    assert win.reset_focused_device_defaults() is False
    assert box.asks and "feature_toggles" in box.asks[0][1]
    assert "motion_and_thresholds" in box.asks[0][1]
    assert cfg.device_settings[ident_a] == entry_before
    assert cfg.pen_area_maps[ident_a] == pen_before
    assert conn_a["config"].pen_speed == live_pen_speed


def test_reset_confirm_clears_entry_reforks_live_keeps_lanes(env, monkeypatch, tmp_path):
    cfg, win, conn_a, _, _, ident_a, _ = env
    box = BoxLog(answer=True)
    monkeypatch.setattr(gui, "messagebox", box)
    cfg.save_to_file(str(tmp_path / "user.cfg"))
    mtime = (tmp_path / "user.cfg").stat().st_mtime_ns
    import logging
    reset_log = []

    class _Cap(logging.Handler):
        def emit(self, record):
            reset_log.append(record.getMessage())

    cap = _Cap()
    logging.getLogger("wtd").addHandler(cap)
    live_cfg = conn_a["config"]
    try:
        assert live_cfg.pen_speed == 4.0  # identity overlay live
        assert win.reset_focused_device_defaults() is True
    finally:
        logging.getLogger("wtd").removeHandler(cap)
    assert ident_a not in cfg.device_settings
    assert live_cfg is conn_a["config"]  # object identity kept: readers live
    base_fork = cfg.fork_for_device(None, None)
    assert live_cfg.pen_speed == base_fork.pen_speed
    assert live_cfg.feature_toggles["pen_absolute_mode"] == (
        base_fork.feature_toggles["pen_absolute_mode"])
    assert cfg.pen_area_maps[ident_a]["entries"][0]["alias"] == "keep-me"
    assert cfg.device_aliases[ident_a] == "Alpha"
    assert (tmp_path / "user.cfg").stat().st_mtime_ns == mtime  # no file write
    assert win.identity_hint_label.cget("text") == (
        "Device-specific values active: (none)")
    assert reset_log and "Restored defaults" in reset_log[0]


def test_reset_other_device_untouched(env, monkeypatch):
    cfg, win, conn_a, conn_b, _, ident_a, ident_b = env
    box = BoxLog(answer=True)
    monkeypatch.setattr(gui, "messagebox", box)
    assert win.reset_focused_device_defaults() is True
    assert ident_a not in cfg.device_settings
    assert ident_b in cfg.device_settings
    assert conn_b["config"].action_mapping["1f_tap"] == "right_click"


def test_reset_nothing_to_reset_informs(env, monkeypatch):
    cfg, win, conn_a, _, conn_c, _, _ = env
    box = BoxLog(answer=True)
    monkeypatch.setattr(gui, "messagebox", box)
    win._device_conns = [conn_a, conn_c]
    win.devices_combo.current(1)
    assert win._on_device_selected() is True
    assert win.reset_focused_device_defaults() is False
    assert box.infos and not box.asks
    assert conn_c["identity"] not in cfg.device_settings
