"""Start-line parser pins (estate WTD plan Phase B unit B2, review Item 9).

One argparse parser now owns the whole start line. These pins hold two things
steady: every spelling the driver has always accepted still parses to the same
effective values (the compat matrix below, asserted through BOTH the parsed
namespace and the legacy tuple adapters, which is the shape callers saw before
B2), and a start argument nobody asked for is now an error instead of a silent
skip - the '-wirelessize' case that the old substring match turned into wireless
mode.

Discriminator labels: the unknown-flag lanes fail red on the pre-B2 tree (R1:
the parser surface is new; R2: a permissive parser); the compat rows are
contract locks that must stay green across the change.
"""

import pytest

from touch_controller import (
    StartArgumentError,
    parse_start_args,
    resolve_cli_lanes,
    resolve_config_ladder,
    resolve_config_paths,
    resolve_start_lanes,
)

ALIAS_NOTE = ("[Start Parameter] '--save-as kitchen' used the device-alias lane "
              "- deprecated, use '--save-device kitchen'; "
              "'--save-as <path>' keeps the save-file lane")


def namespace_start(argv):
    """The folded token list the single parser actually saw (for pass-through pins)."""
    namespace, _ = parse_start_args(argv)
    return namespace.start_tokens


def effective(argv):
    """The effective start line: parsed namespace plus the two decision lanes.

    The ladder runs over a fully stubbed filesystem so a spelling, not the
    machine, decides the answer: with a config present in the current folder the
    default lane is 'user.cfg', and the per-user lane is a fixed name so a
    developer's real %APPDATA% can never add a migration note to a row.
    """
    namespace, notes = parse_start_args(argv)
    list_devices, restore_with, alias, save_as_path = resolve_start_lanes(namespace, notes)
    config_path, save_path, ladder_notes = resolve_config_ladder(
        namespace.config, save_as_path,
        path_exists=lambda path: True, path_mtime=lambda path: 0.0,
        appdata_path="P:/profile/wtd/user.cfg")
    return {
        "config": config_path,
        "save": save_path,
        "list": list_devices,
        "restore": restore_with,
        "alias": alias,
        "wireless": namespace.wireless,
        "debug": namespace.debug,
        "notes": notes + ladder_notes,
    }


COMPAT_CASES = [
    # (argv, expected overrides) - everything else is the plain default line.
    ([], {"config": "user.cfg", "save": "user.cfg"}),
    (["--config", "base.cfg"], {"config": "base.cfg", "save": "base.cfg"}),
    (["-config", "base.cfg"], {"config": "base.cfg", "save": "base.cfg"}),
    (["--config=base.cfg"], {"config": "base.cfg", "save": "base.cfg"}),
    (["-config=base.cfg"], {"config": "base.cfg", "save": "base.cfg"}),
    (["-CONFIG", "base.cfg"], {"config": "base.cfg", "save": "base.cfg"}),
    (["--config", "base.cfg", "--save-as", "other.cfg"],
     {"config": "base.cfg", "save": "other.cfg"}),
    (["--save-as", "out.cfg"], {"save": "out.cfg"}),
    (["--list"], {"list": True}),
    (["-list"], {"list": True}),
    (["--restore-with", "Kitchen"], {"restore": "Kitchen"}),
    (["--restore-with=Kitchen"], {"restore": "Kitchen"}),
    (["-restore-with", "Kitchen"], {"restore": "Kitchen"}),
    (["--save-device", "kitchen"], {"alias": "kitchen"}),
    (["--save-device=kitchen"], {"alias": "kitchen"}),
    (["-save-device", "kitchen"], {"alias": "kitchen"}),
    (["--save-as", "kitchen"], {"alias": "kitchen", "save": "user.cfg",
                                "notes": [ALIAS_NOTE]}),
    (["--save-as=kitchen"], {"alias": "kitchen", "notes": [ALIAS_NOTE]}),
    (["-save-as", "kitchen"], {"alias": "kitchen", "notes": [ALIAS_NOTE]}),
    (["--wireless"], {"wireless": True}),
    (["-wireless"], {"wireless": True}),
    # Intentional widening, not compat: pre-B2 the flag test was lowercase, so
    # '-WIRELESS' was skipped and wireless mode stayed off.
    (["-WIRELESS"], {"wireless": True}),
    (["--debug"], {"debug": True}),
    (["-debug"], {"debug": True}),
    (["-DEBUG"], {"debug": True}),
    (["--list", "--config", "base.cfg", "-wireless"],
     {"list": True, "config": "base.cfg", "save": "base.cfg", "wireless": True}),
    (["--save-device", "kitchen", "-wireless"], {"alias": "kitchen",
                                                 "wireless": True}),
    # Documented change (not compat): pre-B2 the LAST alias spelling won. Since
    # B2 '--save-device' owns the alias and the deprecated bare '--save-as' still
    # gets its note in either order.
    (["--save-device", "kept", "--save-as", "kitchen"],
     {"alias": "kept", "notes": [ALIAS_NOTE]}),
    (["--save-as", "kitchen", "--save-device", "kept"],
     {"alias": "kept", "notes": [ALIAS_NOTE]}),
    # Reviewer P1: a path-form --save-as beside --save-device keeps the save lane.
    (["--save-device", "kept", "--save-as", "out.cfg"],
     {"alias": "kept", "save": "out.cfg"}),
]


@pytest.mark.parametrize("argv", [case[0] for case in COMPAT_CASES])
def test_compat_spellings_parse_to_the_same_effective_values(argv):
    """Contract lock: every accepted spelling keeps its meaning through B2.

    Asserted twice on purpose - through the parsed namespace (the new view) and
    through the legacy tuple adapters (the view the pre-B2 callers had) - so the
    adapter cannot drift away from the parser it is built on.
    """
    expected = dict({"config": "user.cfg", "save": "user.cfg", "list": False,
                     "restore": None, "alias": None, "wireless": False,
                     "debug": False, "notes": []})
    for case_argv, overrides in COMPAT_CASES:
        if case_argv == argv:
            expected.update(overrides)
            break

    assert effective(argv) == expected, argv

    list_devices, restore_with, alias, notes, path_args = resolve_cli_lanes(argv)
    load, save, path_notes, other = resolve_config_paths(
        path_args, path_exists=lambda path: True)
    assert list_devices is expected["list"], argv
    assert restore_with == expected["restore"], argv
    assert alias == expected["alias"], argv
    assert load == expected["config"], argv
    assert save == expected["save"], argv
    assert notes == [n for n in expected["notes"] if "save-as" in n], argv
    # The adapters exist to keep reporting the tokens the user typed: the lane
    # options drop out, everything else passes through unchanged.
    lane_tokens = {"--list", "-list", "--restore-with", "-restore-with",
                   "--save-device", "-save-device"}
    folded_lane_value = namespace_start(argv)
    assert all(token.partition("=")[0] not in lane_tokens for token in path_args), \
        (argv, path_args)
    assert all(token not in {"--config", "-config"} and not token.startswith("--config=")
               for token in other), (argv, other)
    assert isinstance(path_args, list) and isinstance(other, list), argv


UNKNOWN_CASES = [
    ["-wirelessize"],          # the Item 9 bug: matched '-wireless' as a substring
    ["--wireles"],             # an abbreviation nobody asked for
    ["--bogus"],
    ["-x"],
    ["--config", "base.cfg", "-wirelessize"],
]


@pytest.mark.parametrize("argv", UNKNOWN_CASES)
def test_unknown_start_flag_is_an_error(argv):
    """DISCRIMINATOR: pre-B2 these were skipped silently ('-wirelessize' even
    switched on wireless mode); now the line is refused, not partly applied."""
    with pytest.raises(StartArgumentError):
        parse_start_args(argv)


def test_wirelessize_does_not_turn_on_wireless_mode():
    """DISCRIMINATOR for the exact Item 9 report, stated as the user-visible rule."""
    with pytest.raises(StartArgumentError) as excinfo:
        parse_start_args(["-wirelessize"])
    assert "wirelessize" in str(excinfo.value)


def test_save_device_deprecation_line_is_byte_stable():
    """Contract lock: the U413 note keeps its exact wording.

    '--save-as <bare-name>' routes to the alias lane and says so, naming the
    spelling to use; '--save-device' is the clean flag and stays silent.
    """
    assert effective(["--save-as", "kitchen"])["notes"] == [ALIAS_NOTE]
    assert effective(["--save-device", "kitchen"])["notes"] == []


def test_value_option_without_a_value_keeps_its_legacy_note():
    """Contract lock: a missing value is a note and the lane is skipped."""
    _, _, _, notes, _ = resolve_cli_lanes(["--restore-with"])
    assert notes == ["[Start Parameter] --restore-with needs a device name - ignored"]

    _, _, _, notes, _ = resolve_cli_lanes(["--save-device"])
    assert notes == ["[Start Parameter] --save-device needs a device alias - ignored"]

    load, save, notes, other = resolve_config_paths(["--config"],
                                                   path_exists=lambda path: True,
                                                   path_mtime=lambda path: 0.0)
    assert notes == ["[Start Parameter] --config needs a path - ignored"]
    assert load == "user.cfg" and save == "user.cfg" and other == []


def test_boolean_flags_still_accept_a_value_they_never_used():
    """Contract lock: '--debug=x' and twins keep turning the flag on.

    Pre-B2 the flag test was a substring match, so a value glued to a boolean
    flag still switched it on. argparse would refuse '--debug=x' outright, so the
    compat fold drops the inline value and says so once.
    """
    for argv, key in ((["--debug=x"], "debug"), (["-DEBUG=on"], "debug"),
                      (["--wireless=1"], "wireless"), (["--list=yes"], "list")):
        result = effective(argv)
        assert result[key] is True, argv
        assert len(result["notes"]) == 1 and "takes no value" in result["notes"][0], argv


def test_help_is_the_one_lane_that_exits_before_the_driver_starts():
    """-h/--help prints argparse's usage and exits 0 (reviewer P1 correction).

    The pre-B2 parser skipped '-h' silently and the driver started anyway.
    """
    for argv in (["-h"], ["--help"]):
        with pytest.raises(SystemExit) as excinfo:
            parse_start_args(argv)
        assert excinfo.value.code == 0, argv


def test_repeated_save_as_collapses_to_the_last_value():
    """Documented collapse: argparse keeps one value per option.

    Pre-B2 '--save-as kitchen --save-as out.cfg' produced the alias AND the save
    path from the two occurrences at once. Since B2 the last value wins and the
    alias lane is off, because the parser holds one dest per option.
    """
    result = effective(["--save-as", "kitchen", "--save-as", "out.cfg"])
    assert result["alias"] is None
    assert result["save"] == "out.cfg"
    assert result["notes"] == []


def test_note_order_is_documented_argv_then_lane_then_ladder():
    """Notes print in one fixed order: value fixes, then the lane decision, then
    the search-order ladder - so a line with two complaints reads predictably."""
    result = effective(["--save-as", "kitchen", "--restore-with"])
    assert len(result["notes"]) == 2, result["notes"]
    assert "needs a device name" in result["notes"][0]
    assert result["notes"][1] == ALIAS_NOTE
