"""U383 / #383: the Focused-Device section above the pen tabs builds whenever
the driver runs -- headless Tk construction pins.

Root cause (Leg 1): not a merge loss. The bar has lived in gui.py since
9a09202 (U73 d) and was last changed by c06bf19 (U87); gui.py changed only
by the a56b9f8 import refactor since. The section vanished for the
maintainer because SettingsWindow.__init__ gated it on a connection that
existed ~1 s after manager start (main() passed device_info=None otherwise),
and later connections never added the section back.

The suite still maps no window: each fixture builds a withdrawn root, no
test pumps the event loop, and every constructed window stays unmapped
(verified: winfo_viewable()==0 after construction).
"""

import sys
import tkinter as tk

import pytest

import gui
import touch_controller
from touch_controller import DriverConfig, TabletState


class StubManager:
    def __init__(self, conns=()):
        self._conns = list(conns)

    def list_connections(self):
        return list(self._conns)


class RecordingMessageBox:
    def __init__(self):
        self.errors = []

    def showerror(self, title, message):
        self.errors.append((title, message))


def make_conn(base, alias="wacom-intuos5-m", product_id=789):
    return {"product_id": product_id,
            "product_string": "Wacom Intuos5 touch M",
            "alias": alias,
            "config": base.fork_for_device(alias)}


@pytest.fixture
def base_config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return DriverConfig()


CONSTRUCTED = []


@pytest.fixture(scope="module")
def tk_root():
    # One Tk root for the whole module; it is created once and never
    # destroyed. On Windows, creating a fresh first Tk root after a
    # destroy() can fail to relocate init.tcl (measured U383). pytest exits
    # with the interpreter alive, so no re-init is ever attempted.
    root = tk.Tk()
    root.withdraw()
    yield root
    for win in CONSTRUCTED:
        try:
            win.destroy()
        except tk.TclError:
            pass


def build_window(root, base, device_info, monkeypatch):
    box = RecordingMessageBox()
    monkeypatch.setattr(gui, "messagebox", box)
    monkeypatch.setattr(gui, "ToastNotification", lambda *args, **kwargs: None)
    gui_config = device_info["base_config"] if device_info else base
    win = gui.SettingsWindow(root, TabletState(), gui_config, device_info)
    CONSTRUCTED.append(win)
    # No test pumps the event loop, so the constructed window must never map
    # onto the screen; this pins the claim the conftest header makes.
    assert win.winfo_viewable() == 0
    return win, box


def test_section_present_with_live_connection(tk_root, base_config, monkeypatch):
    conns = [make_conn(base_config)]
    di = {"connection": conns[0], "base_config": base_config,
          "manager": StubManager(conns)}
    win, box = build_window(tk_root, base_config, di, monkeypatch)
    assert win.device_label.master["text"] == " Focused Device "
    assert win.device_label.cget("text") == "wacom-intuos5-m"


def test_section_present_without_launch_connection(tk_root, base_config, monkeypatch):
    di = {"connection": None, "base_config": base_config,
          "manager": StubManager([])}
    win, box = build_window(tk_root, base_config, di, monkeypatch)
    assert win.device_label.master["text"] == " Focused Device "
    assert win.device_label.cget("text") == "No device connected yet"


def test_no_device_build_focuses_first_live_connection(tk_root, base_config, monkeypatch):
    c1 = make_conn(base_config, alias="alias-one")
    c2 = make_conn(base_config, alias="alias-two", product_id=791)
    di = {"connection": None, "base_config": base_config,
          "manager": StubManager([c1, c2])}
    win, box = build_window(tk_root, base_config, di, monkeypatch)
    assert di["connection"] is c1
    assert win.device_label.cget("text") == "alias-one"
    # The adopted focus must be the editing target too, not just the label
    # (reviewer P0: a label-only half-focus writes the global config and
    # saves the untouched device config).
    assert win.config is c1["config"]


def test_refresh_adopts_live_connection_when_focus_died(tk_root, base_config, monkeypatch):
    stale = make_conn(base_config, alias="stale-alias")
    live = make_conn(base_config, alias="live-alias", product_id=791)
    di = {"connection": stale, "base_config": base_config,
          "manager": StubManager([live])}
    win, box = build_window(tk_root, base_config, di, monkeypatch)
    win._refresh_device_combo()
    assert di["connection"] is live
    assert win.device_label.cget("text") == "live-alias"
    assert win.config is live["config"]


def test_plain_window_without_device_info(tk_root, base_config, monkeypatch):
    win, box = build_window(tk_root, base_config, None, monkeypatch)
    assert not hasattr(win, "devices_combo")
    assert not hasattr(win, "device_label")


def test_live_connections_empty_without_focus(tk_root, base_config, monkeypatch):
    di = {"connection": None, "base_config": base_config, "manager": None}
    win, box = build_window(tk_root, base_config, di, monkeypatch)
    assert win._live_connections() == []


def test_bar_cells_do_not_share_a_grid_slot(tk_root, base_config, monkeypatch):
    conns = [make_conn(base_config)]
    di = {"connection": conns[0], "base_config": base_config,
          "manager": StubManager(conns)}
    win, box = build_window(tk_root, base_config, di, monkeypatch)
    positions = []
    for child in win.device_label.master.winfo_children():
        info = child.grid_info()
        if not info:
            continue
        positions.append((info["row"], info["column"]))
    dupes = sorted({pos for pos in positions if positions.count(pos) > 1})
    assert dupes == []


def test_alias_assign_without_focus_reports_no_device(tk_root, base_config, monkeypatch):
    di = {"connection": None, "base_config": base_config,
          "manager": StubManager([])}
    win, box = build_window(tk_root, base_config, di, monkeypatch)
    win.alias_entry.insert(0, "new-alias")
    assert win.assign_alias() is False
    assert box.errors[-1][0] == "No Device Focused"


def test_save_device_without_focus_reports_no_device(tk_root, base_config, monkeypatch):
    di = {"connection": None, "base_config": base_config,
          "manager": StubManager([])}
    win, box = build_window(tk_root, base_config, di, monkeypatch)
    assert win.save_to_focused_device() is False
    assert box.errors[-1][0] == "No Device Focused"


def test_copy_to_alias_without_focus_reports_no_device(tk_root, base_config, monkeypatch):
    di = {"connection": None, "base_config": base_config,
          "manager": StubManager([])}
    win, box = build_window(tk_root, base_config, di, monkeypatch)
    win.copy_alias_combo.set("some-alias")
    assert win.copy_settings_to_alias() is False
    assert box.errors[-1][0] == "No Device Focused"


def test_save_to_file_without_focus_falls_back_to_global(tk_root, base_config, monkeypatch, tmp_path):
    di = {"connection": None, "base_config": base_config,
          "manager": StubManager([])}
    win, box = build_window(tk_root, base_config, di, monkeypatch)
    assert win.save_to_file() is True
    assert box.errors == []
    assert (tmp_path / "user.cfg").exists()


def test_build_device_info_keeps_manager_without_connection(base_config):
    mgr = StubManager([])
    di = touch_controller.build_device_info(None, base_config, mgr)
    assert di["connection"] is None
    assert di["base_config"] is base_config
    assert di["manager"] is mgr


class FakeManagerThread:
    def __init__(self, *args, **kwargs):
        pass

    def start(self):
        pass

    def get_primary_connection(self):
        return None


def test_main_passes_manager_bundle_without_connection(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # main() reassigns the module globals; re-registering the current values
    # makes monkeypatch restore them at teardown.
    monkeypatch.setattr(touch_controller, "config", touch_controller.config)
    monkeypatch.setattr(touch_controller, "DEBUG", touch_controller.DEBUG)
    monkeypatch.setattr(sys, "argv", ["touch_controller.py"])
    monkeypatch.setattr(touch_controller.time, "sleep", lambda s: None)
    monkeypatch.setattr(touch_controller, "DeviceManagerThread", FakeManagerThread)
    captured = {}

    def recorder(state, gui_config, pause_event, stop_event, device_info=None):
        captured["device_info"] = device_info
    monkeypatch.setattr(gui, "launch_gui", recorder)

    assert touch_controller.main() == 0
    di = captured["device_info"]
    assert di["connection"] is None
    assert isinstance(di["manager"], FakeManagerThread)
    assert di["base_config"] is not None
