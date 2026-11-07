"""U381 / #381 GUI pins: the Buttons tab exposes the touch part of every
express key with the existing toggle-plus-combobox grid pattern, and the part
defaults to off.

Headless Tk construction only, same discipline as tests/test_gui_device_bar.py:
one withdrawn root per module, no event pump, and every constructed window
stays unmapped. No device, no live input.
"""

import tkinter as tk

import pytest

import gui
from touch_controller import DriverConfig, TabletState, TOUCH_ACTION_CHOICES

CONSTRUCTED = []


@pytest.fixture(scope="module")
def tk_root():
    # One Tk root for the module, never destroyed: a fresh first root after
    # destroy() can fail to relocate init.tcl on Windows (measured U383).
    root = tk.Tk()
    root.withdraw()
    yield root
    for win in CONSTRUCTED:
        try:
            win.destroy()
        except tk.TclError:
            pass


@pytest.fixture
def base_config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return DriverConfig()


@pytest.fixture
def window(tk_root, base_config, monkeypatch):
    class StubBox:
        def showerror(self, title, message):
            pass

    monkeypatch.setattr(gui, "messagebox", StubBox())
    monkeypatch.setattr(gui, "ToastNotification", lambda *args, **kwargs: None)
    win = gui.SettingsWindow(tk_root, TabletState(), base_config, None)
    CONSTRUCTED.append(win)
    # Nothing pumps the event loop, so no window may map onto the screen.
    assert win.winfo_viewable() == 0
    return win


def test_buttons_tab_builds_one_touch_row_entry_per_express_key(window):
    expected = sorted(gui.BUTTON_TOUCH_KEYS)
    assert sorted(window.touch_vars) == expected
    assert sorted(window.touch_combos) == expected


def test_touch_widgets_default_off_and_none(window):
    for key in gui.BUTTON_TOUCH_KEYS:
        assert window.touch_vars[key].get() is False
        assert window.touch_combos[key].get() == "none"


def test_touch_combo_offers_the_modifier_vocabulary(window):
    assert tuple(window.touch_combos["btn1_touch"]["values"]) == TOUCH_ACTION_CHOICES


def test_press_rows_keep_their_own_toggle_and_action(window):
    """The touch part adds columns; it does not displace the press controls."""
    assert len(window.button_vars) == 11
    assert len(window.button_combos) == 11


def test_features_tab_does_not_duplicate_the_touch_toggles(window):
    for key in gui.BUTTON_TOUCH_KEYS:
        assert key not in window.feature_vars
