"""U381 leg A.3 (#381): the per-button touch part holds its configured
modifier while a finger keeps contact, releases it on the full release, and
defaults to OFF so an untouched config fires nothing.

Byte states are copied from maintainer/draft/WTD_pen_support_probe_data.md:
button 1 lifecycle lines 11-14 (touch contact, press, press release still
touching, full release) and the per-button contact rows lines 11, 16, 21, 26,
31, 36, 41, 46 (byte 5 bit N-1). Two rows are grammar-derived and marked at
their site: the simultaneous two-finger contact (the capture shows one finger
at a time), and it reuses only the recorded per-button bit values.

No device, no GUI, no live injection: the pins assert action selection and
hold state through stub_send_input.
"""

import json

import pytest

from touch_controller import (
    ActionDispatcher,
    BUTTON_TOUCH_MASKS,
    DriverConfig,
    TabletState,
    TOUCH_ACTION_CHOICES,
    TOUCH_CONTROL_KEYS,
    TOUCH_NO_ACTION,
    process_button_report,
)

VK_CTRL = 162
VK_SHIFT = 160
VK_ALT = 164

# Recorded button 1 lifecycle, probe lines 11-14.
BTN1_TOUCH = [0x03, 0x80, 0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x00]
BTN1_PRESS = [0x03, 0x80, 0x00, 0x00, 0x01, 0x01, 0x00, 0x00, 0x00, 0x00]
BTN1_PRESS_RELEASE = [0x03, 0x80, 0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x00]
FULL_RELEASE = [0x03, 0x80, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00]


def build_row(press=0, touch=0, central=0, dial=0):
    """The recorded column layout: byte 2 dial, byte 3 central, byte 4 press,
    byte 5 touch contact."""
    return [0x03, 0x80, dial, central, press, touch, 0, 0, 0, 0]


@pytest.fixture
def config(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    return DriverConfig()


@pytest.fixture
def pair(config):
    state = TabletState()
    return state, ActionDispatcher(state, config)


def process(report, pair, config):
    state, dispatcher = pair
    process_button_report(report, state, dispatcher, config)
    return state


def key_events(events):
    """(vk, down?) pairs in emission order."""
    return [(e[1], e[2] == 0) for e in events if e[0] == "key"]


def downs(events, vk):
    return sum(1 for vk_code, down in key_events(events) if vk_code == vk and down)


def ups(events, vk):
    return sum(1 for vk_code, down in key_events(events) if vk_code == vk and not down)


def enable_touch(config, slot, modifier):
    config.feature_toggles[f"btn{slot}_touch"] = True
    config.action_mapping[f"btn{slot}_touch"] = modifier


# ==============================================================================
# Hold / release lifecycle over the recorded byte states
# ==============================================================================

def test_touch_contact_holds_modifier_until_full_release(config, pair, stub_send_input):
    enable_touch(config, 1, "ctrl")
    # A distinct press action keeps the modifier counts readable: the default
    # btn1_press action ("undo") taps Ctrl itself.
    config.action_mapping["btn1_press"] = "volume_up"

    process(BTN1_TOUCH, pair, config)
    assert downs(stub_send_input, VK_CTRL) == 1
    assert ups(stub_send_input, VK_CTRL) == 0

    # Recorded press row: the modifier stays held, no second key-down, and the
    # press action fires alongside it.
    process(BTN1_PRESS, pair, config)
    assert downs(stub_send_input, VK_CTRL) == 1
    assert downs(stub_send_input, 0xAF) == 1
    assert ups(stub_send_input, VK_CTRL) == 0

    # Recorded press-release-still-touching row: contact stands, hold stands.
    process(BTN1_PRESS_RELEASE, pair, config)
    assert ups(stub_send_input, VK_CTRL) == 0

    # Recorded full-release row: the hold ends.
    process(FULL_RELEASE, pair, config)
    assert ups(stub_send_input, VK_CTRL) == 1
    assert pair[1].touch_held == {}


def test_touch_hold_lands_before_the_press_action_of_the_same_row(config, pair,
                                                                  stub_send_input):
    """Recorded press row carries both bits: the modifier must precede the action."""
    enable_touch(config, 1, "shift")
    config.action_mapping["btn1_press"] = "undo"   # ctrl + z tap

    process(BTN1_PRESS, pair, config)

    events = key_events(stub_send_input)
    assert events[0] == (VK_SHIFT, True)
    assert downs(stub_send_input, VK_CTRL) == 1


def test_press_fires_while_the_touch_hold_stands(config, pair, stub_send_input):
    """Touch state never blocks a press (U381 restates the U71 rule)."""
    enable_touch(config, 1, "alt")
    config.action_mapping["btn1_press"] = "volume_up"

    process(BTN1_TOUCH, pair, config)
    process(BTN1_PRESS, pair, config)

    assert downs(stub_send_input, VK_ALT) == 1
    assert downs(stub_send_input, 0xAF) == 1


def test_two_contacts_hold_two_modifiers_and_release_owns_its_slot(config, pair,
                                                                   stub_send_input):
    """Derived row (capture shows one finger at a time): bits 0+1 of byte 5,
    each bit value copied from the recorded contact rows of buttons 1 and 2."""
    enable_touch(config, 1, "ctrl")
    enable_touch(config, 2, "shift")

    process(build_row(touch=0x03), pair, config)
    assert downs(stub_send_input, VK_CTRL) == 1
    assert downs(stub_send_input, VK_SHIFT) == 1

    # Button 1 lifts, button 2 keeps contact (derived from the recorded bits).
    process(build_row(touch=0x02), pair, config)
    assert ups(stub_send_input, VK_CTRL) == 1
    assert ups(stub_send_input, VK_SHIFT) == 0
    assert pair[1].touch_held == {2: "shift"}


# ==============================================================================
# Default off and disabled legs
# ==============================================================================

@pytest.mark.parametrize("slot", range(1, 9))
def test_default_config_touch_contact_fires_nothing(slot, config, pair, stub_send_input):
    """Recorded per-button contact rows (probe lines 11,16,21,26,31,36,41,46)."""
    process(build_row(touch=BUTTON_TOUCH_MASKS[slot - 1]), pair, config)

    assert stub_send_input == []
    # The contact edge is still tracked, so a later enable sees the release.
    assert pair[0].button_touch_mask == BUTTON_TOUCH_MASKS[slot - 1]


@pytest.mark.parametrize("slot", range(1, 9))
def test_mapping_without_toggle_fires_nothing(slot, config, pair, stub_send_input):
    config.action_mapping[f"btn{slot}_touch"] = "ctrl"

    process(build_row(touch=BUTTON_TOUCH_MASKS[slot - 1]), pair, config)

    assert stub_send_input == []
    assert pair[1].touch_held == {}


def test_unknown_touch_modifier_is_ignored(config, pair, stub_send_input):
    enable_touch(config, 1, "superkey")

    process(BTN1_TOUCH, pair, config)
    process(FULL_RELEASE, pair, config)

    assert stub_send_input == []
    assert pair[1].touch_held == {}


# ==============================================================================
# Config lanes: defaults, save round trip, validation
# ==============================================================================

def test_touch_defaults_and_keys_join_save_dict(config, tmp_path):
    for key in TOUCH_CONTROL_KEYS:
        assert config.feature_toggles[key] is False
        assert config.action_mapping[key] == TOUCH_NO_ACTION

    path = tmp_path / "user.cfg"
    config.save_to_file(str(path))
    data = json.loads(path.read_text())
    for key in TOUCH_CONTROL_KEYS:
        assert data["feature_toggles"][key] is False
        assert data["action_mapping"][key] == TOUCH_NO_ACTION


def test_touch_config_round_trip(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = DriverConfig()
    cfg.feature_toggles["btn3_touch"] = True
    cfg.action_mapping["btn3_touch"] = "shift"
    path = tmp_path / "user.cfg"
    cfg.save_to_file(str(path))

    fresh = DriverConfig()
    assert fresh.load_from_file(str(path)) is True
    assert fresh.feature_toggles["btn3_touch"] is True
    assert fresh.action_mapping["btn3_touch"] == "shift"


def test_load_rejects_unknown_touch_modifier(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    cfg = DriverConfig()
    path = tmp_path / "bad.cfg"
    data = {"action_mapping": {"btn1_touch": "superkey"}}
    path.write_text(json.dumps(data))

    loaded = cfg.load_from_file(str(path))

    assert loaded is False
    assert cfg.action_mapping["btn1_touch"] == TOUCH_NO_ACTION
    assert "unknown action" in capsys.readouterr().out


def test_touch_action_choices_are_the_documented_vocabulary():
    assert TOUCH_ACTION_CHOICES == ("none", "ctrl", "shift", "alt")
