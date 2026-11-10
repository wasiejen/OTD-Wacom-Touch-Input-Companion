"""U386 / TODO #385: wireless_pen_size renamed to wireless_tablet_size,
deprecation alias with a loud notice, and NO silent default when the
non-detecting wireless receiver (id 132) runs with the size unset.

Ruling (maintainer 2026-11-07, TODO #385 DECISION line): the wireless path
has nothing to do with the pen - the receiver connects directly to the
tablet. Planner ruling (protocol): unset + no detection = loud notice
naming the choices, never a silent default.

RED-before (measured against 05c47e4, the Leg 1 tip = pre-Leg-2, one run):
- config default is no longer the silent "medium" (AttributeError: no
  wireless_tablet_size attribute);
- the new key is unknown pre-change (warning + ignored, routing line absent);
- the old key printed NO deprecation notice and the unset receiver printed
  the SILENT line "pen_specs= wireless_size=medium product=132 using=789";
- the GUI had no wireless-size control and no notice label at all.

Headless Tk construction follows tests/test_gui_buttons_touch.py: one
withdrawn root per module, no event pump, every window stays unmapped.
No device, no live input.
"""

import json
import tkinter as tk

import pytest

import gui
import touch_controller
from touch_controller import (
    DriverConfig,
    PEN_SPECS_BY_PRODUCT,
    PEN_SPECS_DEFAULT,
    TabletState,
    pen_specs_for_product,
)

WIRELESS_RECEIVER_ID = 132

# Byte-exact CLI shapes (designed with the protocol Leg 2 decision entry).
UNSET_NOTICE = (
    "!! WIRELESS TABLET SIZE NOT SET: receiver id 132 cannot detect its paired tablet.\n"
    "!! choose one: small (788) | medium (789) | large (791) - GUI Pen tab \"Wireless Tablet Size\", "
    "or \"motion_and_thresholds.wireless_tablet_size\" in the config file.\n"
    "!! using MEDIUM extents (789) until a size is chosen: FALLBACK, not a default.\n"
    "pen_specs= wireless_tablet_size=unset product=132 using=789 fallback=not_chosen\n"
)

GUI_NOTICE_TEXT = (
    "Wireless tablet size NOT SET: the receiver (id 132) cannot detect its "
    "paired tablet. Choose small (788) / medium (789) / large (791) below - "
    "until chosen the driver maps the MEDIUM extents (789). A size change "
    "applies at the next device reconnect."
)


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return DriverConfig()


def write_cfg(path, motion):
    path.write_text(json.dumps({"motion_and_thresholds": motion}), encoding="utf-8")
    return str(path)


# --- config lanes ---------------------------------------------------------------

def test_default_is_unset_no_silent_medium(cfg):
    """RED-before: the silent WIRELESS_PEN_SIZE_DEFAULT default is gone."""
    assert cfg.wireless_tablet_size is None


def test_new_key_honored_and_routes_the_receiver(capsys, cfg, tmp_path):
    capsys.readouterr()  # drain the fixture's config-load banner
    path = write_cfg(tmp_path / "user.cfg", {"wireless_tablet_size": "small"})
    assert cfg.load_from_file(path) is True
    assert cfg.wireless_tablet_size == "small"
    capsys.readouterr()
    assert pen_specs_for_product(WIRELESS_RECEIVER_ID, cfg) is PEN_SPECS_BY_PRODUCT[788]
    assert capsys.readouterr().out == (
        "pen_specs= wireless_tablet_size=small product=132 using=788\n")


def test_new_key_bad_value_rejects_the_load(cfg, tmp_path):
    path = write_cfg(tmp_path / "bad.cfg", {"wireless_tablet_size": "huge"})
    assert cfg.load_from_file(path) is False
    assert cfg.wireless_tablet_size is None


def test_legacy_key_migrates_with_loud_notice(capsys, cfg, tmp_path):
    """RED-before: old configs keep working AND the rename is announced."""
    capsys.readouterr()
    path = write_cfg(tmp_path / "old.cfg", {"wireless_pen_size": "large"})
    assert cfg.load_from_file(path) is True
    out = capsys.readouterr().out
    assert "legacy key 'wireless_pen_size'" in out, (
        "no loud deprecation notice naming the old key")
    assert "'wireless_tablet_size'" in out, (
        "the deprecation notice does not name the new key")
    assert "migrated" in out
    assert cfg.wireless_tablet_size == "large"
    capsys.readouterr()
    assert pen_specs_for_product(WIRELESS_RECEIVER_ID, cfg) is PEN_SPECS_BY_PRODUCT[791]


def test_legacy_key_bad_value_errors_naming_the_new_key(capsys, cfg, tmp_path):
    capsys.readouterr()
    path = write_cfg(tmp_path / "oldbad.cfg", {"wireless_pen_size": "huge"})
    assert cfg.load_from_file(path) is False
    out = capsys.readouterr().out
    assert "wireless_tablet_size" in out, (
        "the error must name the new key for an old-key value error")
    assert cfg.wireless_tablet_size is None


def test_both_keys_present_new_wins_legacy_notices(capsys, cfg, tmp_path):
    capsys.readouterr()
    path = write_cfg(tmp_path / "both.cfg", {
        "wireless_pen_size": "large", "wireless_tablet_size": "small"})
    assert cfg.load_from_file(path) is True
    assert cfg.wireless_tablet_size == "small"
    out = capsys.readouterr().out
    assert "legacy key 'wireless_pen_size'" in out


def test_unset_receiver_prints_the_loud_notice_shape(capsys, cfg):
    """RED-before: unset + no detection = notice shape, never the silent line."""
    capsys.readouterr()
    assert cfg.wireless_tablet_size is None
    assert pen_specs_for_product(WIRELESS_RECEIVER_ID, cfg) is PEN_SPECS_DEFAULT
    out = capsys.readouterr().out
    assert out == UNSET_NOTICE
    for size in ("small", "medium", "large"):
        assert size in out, f"the notice must name the choice '{size}'"


def test_explicit_medium_routes_without_unset_notice(capsys, cfg):
    """An explicit medium prints the routing line only - no unset notice."""
    capsys.readouterr()
    cfg.wireless_tablet_size = "medium"
    assert pen_specs_for_product(WIRELESS_RECEIVER_ID, cfg) is PEN_SPECS_BY_PRODUCT[789]
    out = capsys.readouterr().out
    assert out == "pen_specs= wireless_tablet_size=medium product=132 using=789\n"
    assert "NOT SET" not in out


def test_unset_round_trips_as_null(capsys, cfg, tmp_path):
    path = str(tmp_path / "rt.cfg")
    capsys.readouterr()
    cfg.save_to_file(path)
    with open(path, encoding="utf-8") as f:
        assert json.load(f)["motion_and_thresholds"]["wireless_tablet_size"] is None
    fresh = DriverConfig()
    assert fresh.load_from_file(path) is True
    assert fresh.wireless_tablet_size is None
    assert pen_specs_for_product(WIRELESS_RECEIVER_ID, fresh) is PEN_SPECS_DEFAULT


# --- GUI lanes (headless withdrawn root, test_gui_buttons_touch pattern) --------

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
def gui_base(tmp_path, monkeypatch):
    # chdir BEFORE DriverConfig(): the constructor loads the CWD's user.cfg,
    # and the repo root carries the maintainer's live file.
    monkeypatch.chdir(tmp_path)
    return DriverConfig()


def make_window(root, conn, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    base = DriverConfig()
    monkeypatch.setattr(gui, "messagebox", StubBox())
    monkeypatch.setattr(gui, "ToastNotification", lambda *args, **kwargs: None)
    device_info = {"connection": conn, "base_config": base,
                   "manager": None}
    gui_config = conn["config"] if conn is not None else base
    win = gui.SettingsWindow(root, TabletState(), gui_config, device_info)
    CONSTRUCTED.append(win)
    assert win.winfo_viewable() == 0
    return win


def make_conn(base, product_id):
    return {"product_id": product_id,
            "product_string": "Wacom Wireless Receiver" if product_id == 132
            else "Wacom Intuos5 touch M",
            "alias": None,
            "config": base.fork_for_device(None)}


def test_gui_notice_shows_for_unset_wireless_receiver(tk_root, monkeypatch, tmp_path, gui_base):
    """RED-before: unset + live receiver (id 132) = loud notice label."""
    base = gui_base
    conn = make_conn(base, WIRELESS_RECEIVER_ID)
    win = make_window(tk_root, conn, monkeypatch, tmp_path)
    assert win.wireless_size_notice.cget("text") == GUI_NOTICE_TEXT


def test_gui_notice_hidden_for_wired_receiver(tk_root, monkeypatch, tmp_path, gui_base):
    base = gui_base
    conn = make_conn(base, 789)
    win = make_window(tk_root, conn, monkeypatch, tmp_path)
    assert win.wireless_size_notice.cget("text") == ""


def test_gui_notice_hidden_when_size_chosen(tk_root, monkeypatch, tmp_path, gui_base):
    base = gui_base
    conn = make_conn(base, WIRELESS_RECEIVER_ID)
    conn["config"].wireless_tablet_size = "large"
    win = make_window(tk_root, conn, monkeypatch, tmp_path)
    assert win.wireless_size_notice.cget("text") == ""
    assert win.wireless_size_combo.get() == "large"


def test_gui_combo_offers_unset_plus_tablet_sizes(tk_root, monkeypatch, tmp_path, gui_base):
    base = gui_base
    conn = make_conn(base, WIRELESS_RECEIVER_ID)
    win = make_window(tk_root, conn, monkeypatch, tmp_path)
    assert tuple(win.wireless_size_combo["values"]) == ("(unset)", "small", "medium", "large")
    assert win.wireless_size_combo.get() == "(unset)"


def test_gui_apply_sets_the_size_and_clears_the_notice(tk_root, monkeypatch, tmp_path, gui_base):
    base = gui_base
    conn = make_conn(base, WIRELESS_RECEIVER_ID)
    fork = conn["config"]
    win = make_window(tk_root, conn, monkeypatch, tmp_path)
    win.wireless_size_combo.set("large")
    assert win.apply_settings() is True
    assert fork.wireless_tablet_size == "large"
    assert win.wireless_size_notice.cget("text") == ""


def test_gui_apply_unset_clears_the_size(tk_root, monkeypatch, tmp_path, gui_base):
    base = gui_base
    conn = make_conn(base, WIRELESS_RECEIVER_ID)
    fork = conn["config"]
    fork.wireless_tablet_size = "medium"
    win = make_window(tk_root, conn, monkeypatch, tmp_path)
    win.wireless_size_combo.set("(unset)")
    assert win.apply_settings() is True
    assert fork.wireless_tablet_size is None
    assert win.wireless_size_notice.cget("text") == GUI_NOTICE_TEXT
