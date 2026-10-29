"""Tray checkbox state tests (TODO #248).

DriverTrayApp is exercised without pystray's backend and without Tk:
the constructor only needs an event pair and Pillow.
"""

import threading

import pytest

import gui


@pytest.fixture
def tray_app():
    enabled = threading.Event()
    stop = threading.Event()
    app = gui.DriverTrayApp(root=None, state_ref=None, config_ref=None,
                            enabled_event_ref=enabled, stop_event_ref=stop)
    return app


def test_checked_state_true_when_event_set(tray_app):
    tray_app.touch_enabled_event.set()
    assert tray_app.is_touch_enabled() is True


def test_checked_state_false_when_event_cleared(tray_app):
    tray_app.touch_enabled_event.clear()
    assert tray_app.is_touch_enabled() is False


def test_checked_state_follows_event_as_pystray_callback(tray_app):
    tray_app.touch_enabled_event.set()
    assert tray_app.is_touch_enabled(item=None) == tray_app.touch_enabled_event.is_set()

    tray_app.touch_enabled_event.clear()
    assert tray_app.is_touch_enabled(item=None) == tray_app.touch_enabled_event.is_set()


def test_toggle_touch_flips_checked_state(tray_app):
    tray_app.touch_enabled_event.set()

    tray_app.toggle_touch()
    assert tray_app.is_touch_enabled() == tray_app.touch_enabled_event.is_set()
    assert tray_app.is_touch_enabled() is False

    tray_app.toggle_touch()
    assert tray_app.is_touch_enabled() == tray_app.touch_enabled_event.is_set()
    assert tray_app.is_touch_enabled() is True


def test_toggle_touch_reports_status(tray_app, capsys):
    tray_app.touch_enabled_event.set()
    tray_app.toggle_touch()
    out = capsys.readouterr().out
    assert "Paused" in out

    tray_app.toggle_touch()
    out = capsys.readouterr().out
    assert "Active" in out
