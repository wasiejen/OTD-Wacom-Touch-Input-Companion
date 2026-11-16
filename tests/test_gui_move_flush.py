"""u-flushhz-gui: the pen_move_flush_hz knob gets a Pen-tab spinbox.

Display/edit only: no new config key and no new driver behavior. Pins:
the field shows the stored value (default 250.0) with the spin range;
Apply Live writes it through the shared numeric-entry lane (the same lane
pen_speed uses) and the per-dispatcher time gate reads the new interval
live off the connection's config object (the U386 fork contract, tested
here at the reader's own read point _move_flush_interval); bad or
out-of-range input is refused all-or-nothing (the orientation_degrees
pattern); the value round-trips through the config file.

Headless withdrawn root, the test_gui_pen_modes pattern (WM-103: one
process root, never destroyed; windows withdrawn, winfo_viewable 0).
"""

import tkinter as tk

import pytest

import gui
from wtd.config import DriverConfig
from wtd.gestures import TabletState
from wtd.sender import ActionDispatcher

DISP1 = "\\\\.\\DISPLAY1|0|0|1920x1080"
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
    """Records the rejection messages apply_settings shows on bad input."""

    def __init__(self):
        self.errors = []

    def showerror(self, title, message):
        self.errors.append((title, message))


@pytest.fixture
def cfg_factory(monkeypatch, tmp_path):
    """DriverConfig builders on a tmp cwd with a stubbed two-value desktop."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(gui, "monitor_records", lambda: [DISP1])
    monkeypatch.setattr(gui, "virtual_desktop_metrics", lambda: (0, 0, 1920, 1080))
    monkeypatch.setattr(gui, "virtual_desktop_dpi", lambda: (96.0, 96.0))

    def make(**overrides):
        cfg = DriverConfig()
        for name, value in overrides.items():
            setattr(cfg, name, value)
        return cfg

    return make


def make_window(root, cfg, monkeypatch):
    box = StubBox()
    monkeypatch.setattr(gui, "messagebox", box)
    monkeypatch.setattr(gui, "ToastNotification", lambda *args, **kwargs: None)
    device_info = {"connection": {"config": cfg, "identity": "0123456789ab",
                                  "product_id": 791},
                   "base_config": cfg, "manager": None}
    win = gui.SettingsWindow(root, TabletState(), cfg, device_info)
    CONSTRUCTED.append(win)
    assert win.winfo_viewable() == 0
    return win, box


# -- Pin 1: the field exists, bounded, and echoes the stored value ----------

def test_field_shows_the_stored_default_with_the_spin_range(cfg_factory, tk_root,
                                                            monkeypatch):
    cfg = cfg_factory()
    win, _ = make_window(tk_root, cfg, monkeypatch)
    spin = win.entries["pen_move_flush_hz"]
    assert spin.get() == "250.0" == str(cfg.pen_move_flush_hz)
    assert float(spin.cget("from")) == gui.MOVE_FLUSH_HZ_MIN
    assert float(spin.cget("to")) == gui.MOVE_FLUSH_HZ_MAX


def test_field_echoes_a_non_default_stored_value(cfg_factory, tk_root, monkeypatch):
    cfg = cfg_factory(pen_move_flush_hz=333.0)
    win, _ = make_window(tk_root, cfg, monkeypatch)
    assert win.entries["pen_move_flush_hz"].get() == "333.0"


# -- Pin 2: Apply Live reaches the dispatcher slot's time-gate read ---------

def test_apply_live_propagates_the_interval_to_the_dispatcher(cfg_factory, tk_root,
                                                              monkeypatch):
    # The dispatcher shares the connection's config object (the manager
    # spawns readers on conn["config"]; the GUI mutates that object in
    # place), so the gate read is the propagation seam.
    cfg = cfg_factory()
    dispatcher = ActionDispatcher(TabletState(), cfg)
    assert dispatcher._move_flush_interval() == pytest.approx(1.0 / 250.0)
    win, _ = make_window(tk_root, cfg, monkeypatch)
    spin = win.entries["pen_move_flush_hz"]
    spin.delete(0, tk.END)
    spin.insert(0, "80")
    assert win.apply_settings() is True
    assert cfg.pen_move_flush_hz == 80.0
    assert dispatcher._move_flush_interval() == pytest.approx(1.0 / 80.0)


# -- Pin 3: bad or out-of-range input is refused, all-or-nothing ------------

def test_bad_or_out_of_range_input_is_refused_all_or_nothing(cfg_factory, tk_root,
                                                             monkeypatch):
    cfg = cfg_factory()
    win, box = make_window(tk_root, cfg, monkeypatch)
    spin = win.entries["pen_move_flush_hz"]
    speed = win.entries["pen_speed"]
    original_speed = cfg.pen_speed
    speed.delete(0, tk.END)
    speed.insert(0, "3.5")  # a co-applied change that must never land alone
    for junk in ("fast", "", "0", "-5", "5", "1001"):
        spin.delete(0, tk.END)
        spin.insert(0, junk)
        assert win.apply_settings() is False, junk
        assert cfg.pen_move_flush_hz == 250.0, junk
    assert cfg.pen_speed == original_speed, (
        "the refused Apply Live still moved pen_speed: not all-or-nothing")
    assert box.errors, "the user got no rejection message"


# -- Pin 4: the applied value survives a config file round trip -------------

def test_value_round_trips_through_the_config_file(cfg_factory, tmp_path):
    cfg = cfg_factory(pen_move_flush_hz=120.0)
    path = tmp_path / "rt_user.cfg"
    cfg.save_to_file(str(path))
    fresh = DriverConfig()
    fresh.load_from_file(str(path))
    assert fresh.pen_move_flush_hz == 120.0
