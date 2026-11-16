"""Argument parser, config/CLI lanes, device CLI commands, and main(),
moved from touch_controller."""

import time
import threading
import argparse
import ctypes
import os
import subprocess
import sys
from wtd import config as wtd_config
from wtd.config import (CONFIG_FILE_PATH, DEVICE_SECTION_KEYS, DriverConfig, appdata_config_path, config, snapshot_device_sections)
from wtd.mapping import (log_metrics_diagnostic)
from wtd.gestures import (TabletState)
from wtd.devices import (DeviceManagerThread, build_device_info, shutdown_readers)
from wtd._log import logger as _log  # shared wtd logger (see wtd/_log.py)




def config_lane_notes(cwd_path, appdata_path, path_exists, path_mtime):
    """One-time notice when a cwd config wins over a differing per-user file.

    Both files existing with different modification times means the user has two
    live copies. The current folder wins (search order, Item 5) and nothing is
    moved or copied, so say exactly that once at start instead of letting the
    user guess which file the driver read.
    """
    if not (path_exists(cwd_path) and path_exists(appdata_path)):
        return []
    try:
        cwd_stamp = path_mtime(cwd_path)
        appdata_stamp = path_mtime(appdata_path)
    except OSError:
        return []
    if cwd_stamp == appdata_stamp:
        return []
    return [f"[Config Migration] Using '{cwd_path}' from the current folder while "
            f"'{appdata_path}' also exists (different modification time) - the "
            "current folder wins; nothing was moved or copied."]


def resolve_config_paths(args, path_exists=None, path_mtime=None, appdata_path=None):
    """Adapter over the single start parser: the legacy four-tuple view.

    Unit B2 moved the actual parsing to parse_start_args; this keeps the shape the
    path pins already assert - (config_path, save_path, notes, other_args) - where
    ``other_args`` are the tokens the path options did not consume. Prefer
    parse_start_args plus resolve_config_ladder in new code.
    """
    namespace, notes = parse_start_args(args)
    # The lane decision lives in resolve_start_lanes only; the adapter borrows
    # its answer so the save-file target cannot drift from the alias routing.
    _, _, _, save_as_path = resolve_start_lanes(namespace, [])
    config_path, save_path, ladder_notes = resolve_config_ladder(
        namespace.config, save_as_path,
        path_exists=path_exists, path_mtime=path_mtime, appdata_path=appdata_path)

    other_args = []
    skip_values = {"--config", "--save-as"}
    index = 0
    tokens = namespace.start_tokens
    while index < len(tokens):
        name = tokens[index].partition("=")[0]
        if name in skip_values:
            if "=" not in tokens[index]:
                index += 1
            index += 1
            continue
        other_args.extend(namespace.start_origins[index])
        index += 1

    return config_path, save_path, notes + ladder_notes, other_args


class StartArgumentError(ValueError):
    """One bad start argument. The text is what the user reads, verbatim."""


# Compat spellings the driver has always accepted: the same long option written
# with a single dash, in any case. argparse reaches '--config' and '--config=X'
# on its own but cannot be given '-config' as a long option, and its own
# abbreviation matching would also guess at things like '-wirelessize' - so the
# compat layer is this exact table, applied before parsing. Anything not listed
# here reaches argparse and is reported as an unknown flag (unit B2 / Item 9:
# the old substring match let '-wirelessize' turn on wireless mode).
START_FLAG_FOLD = {
    "--list": "--list",
    "--restore-with": "--restore-with",
    "--save-device": "--save-device",
    "--save-as": "--save-as",
    "--config": "--config",
    "--wireless": "--wireless",
    "--debug": "--debug",
    "--elevated": "--elevated",
    "-list": "--list",
    "-restore-with": "--restore-with",
    "-save-device": "--save-device",
    "-save-as": "--save-as",
    "-config": "--config",
    "-wireless": "--wireless",
    "-debug": "--debug",
    "-elevated": "--elevated",
}

# Value-taking options and the word each legacy note used for the value.
START_VALUE_WORDS = {
    "--config": "path",
    "--save-as": "path",
    "--restore-with": "device name",
    "--save-device": "device alias",
}


class _StartArgumentParser(argparse.ArgumentParser):
    """argparse that reports through StartArgumentError instead of exiting.

    main() owns the exit code and the bracket prefix, so a bad command line reads
    like every other start note instead of dumping argparse's usage block.
    """

    def error(self, message):
        raise StartArgumentError(message)


def build_start_parser():
    """The one parser for the whole start line (unit B2 / review Item 9).

    Help stays argparse's own: -h/--help prints the usage block and exits 0
    before the driver starts anything (unit B2 reviewer round: pre-B2 a '-h' was
    skipped silently and the driver started anyway). allow_abbrev is off so no
    spelling is invented: only the flags listed here and the folded compat
    spellings parse.
    """
    parser = _StartArgumentParser(prog="touch_controller.py", allow_abbrev=False)
    parser.add_argument("--config")
    parser.add_argument("--save-as")
    parser.add_argument("--save-device")
    parser.add_argument("--restore-with")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--wireless", action="store_true")
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--elevated", action="store_true")
    return parser


def normalize_start_args(argv):
    """Fold compat spellings and apply the legacy value rules, in one pass.

    Returns (tokens, notes, origins). '-DEBUG' becomes '--debug' and
    '--config=X' passes through argparse's own '=' handling; a value option that
    got no value (end of line, or followed by another flag) is dropped with the
    exact note the old hand-written parsers printed, so the note contracts
    survive strict parsing, and the token a note names is the spelling the user
    typed. ``origins`` maps each emitted token back to the argv item it came
    from (a list, because one folded token can carry two of them), so the legacy
    adapters can still report what the user typed.
    """
    tokens = []
    origins = []
    notes = []
    index = 0
    while index < len(argv):
        arg = argv[index]
        name, sep, value = arg.partition("=")
        folded = START_FLAG_FOLD.get(name.lower())
        if folded is None:
            tokens.append(arg)
            origins.append([arg])
            index += 1
            continue

        canonical = folded + (sep + value if sep else "")
        word = START_VALUE_WORDS.get(folded)
        if word is None:
            # A boolean flag that was handed a value ('--debug=x', '--list=yes')
            # keeps the pre-B2 reading: the flag is on, the value is ignored.
            # Passing the inline value through would make argparse refuse a line
            # the driver has always accepted.
            if sep:
                canonical = folded
                notes.append(f"[Start Parameter] {name} takes no value - '{value}' "
                             "ignored")
            tokens.append(canonical)
            origins.append([arg])
            index += 1
            continue

        if value:
            tokens.append(canonical)
            origins.append([arg])
            index += 1
            continue
        following = argv[index + 1] if index + 1 < len(argv) else None
        if following is not None and not following.startswith("-"):
            tokens.append(folded + "=" + following)
            origins.append([arg, following])
            index += 2
            continue
        notes.append(f"[Start Parameter] {name} needs a {word} - ignored")
        index += 1
    return tokens, notes, origins


def parse_start_args(argv):
    """Parse a start command line once: folded tokens in, namespace and notes out.

    This is the single lane every start argument reaches. Returns
    (namespace, notes); an unknown flag or an unexpected value raises
    StartArgumentError, which main() prints as one start note. The namespace also
    carries the tokens argparse saw and the argv each token came from.
    """
    tokens, notes, origins = normalize_start_args(argv)
    parser = build_start_parser()
    namespace = parser.parse_args(tokens)
    namespace.start_tokens = tokens
    namespace.start_origins = origins
    return namespace, notes


def resolve_start_lanes(namespace, notes):
    """Decide which lane the parsed line means, and note the deprecated spelling.

    '--save-device <alias>' is that lane's own flag (U413 ruling option 2);
    '--save-as <bare-name>' is its deprecated spelling and still routes, loudly,
    with the note text kept byte-stable from the pre-B2 parser because pins read
    it; '--save-as <path>' keeps the save-file lane. When both alias spellings
    appear, '--save-device' wins. Returns (list_devices, restore_with,
    save_as_alias, save_as_path).
    """
    save_as_alias = namespace.save_device
    save_as_path = namespace.save_as
    bare_save_as = namespace.save_as and not looks_like_path(namespace.save_as)
    if bare_save_as:
        # The deprecated alias spelling always gets its note, whichever order the
        # two alias flags appear in, so the warning cannot be hidden by a second
        # flag. '--save-device' still owns the alias when both are present.
        notes.append(
            f"[Start Parameter] '--save-as {namespace.save_as}' used the "
            f"device-alias lane - deprecated, use '--save-device "
            f"{namespace.save_as}'; '--save-as <path>' keeps the save-file lane")
        if not save_as_alias:
            save_as_alias = namespace.save_as
        # Only the ALIAS reading consumes --save-as; a path-form value stays the
        # save-file target even next to --save-device (reviewer P1: dropping it
        # silently redirected an alias save into the loaded config file).
        save_as_path = None
    return (namespace.list, namespace.restore_with, save_as_alias, save_as_path)


def resolve_config_ladder(explicit_config, explicit_save_as,
                          path_exists=None, path_mtime=None, appdata_path=None):
    """Apply the approved search order to the parsed values (unit B1 / Item 5).

    An explicit --config wins; otherwise a cwd config is used when it exists and
    the per-user lane otherwise. The probes stay injectable so the ladder can be
    pinned without touching a real profile. Returns (config_path, save_path,
    notes); --save-as omitted follows the loaded file.
    """
    exists = os.path.exists if path_exists is None else path_exists
    get_mtime = os.path.getmtime if path_mtime is None else path_mtime
    appdata = appdata_config_path() if appdata_path is None else appdata_path
    notes = []

    if explicit_config:
        config_path = explicit_config
    elif exists(CONFIG_FILE_PATH):
        config_path = CONFIG_FILE_PATH
        notes.extend(config_lane_notes(CONFIG_FILE_PATH, appdata, exists, get_mtime))
    else:
        config_path = appdata

    save_path = explicit_save_as or config_path
    return config_path, save_path, notes


# ---------------------------------------------------------------------------
# --elevated startup lane (maintainer decision 2026-11-16, WTD mirror of the
# shipped FST --elevate; route = research estate 2026-11-13_elevated-input.md
# section 5 lane 1): a frozen exe given --elevated self-elevates through ONE
# runas relaunch of itself, strips the flag from the child's args (loop
# guard) and exits 0. main() calls handle_elevated_flag right after the
# strict start parse and BEFORE any config read, device, or window init, so
# a bad command line never raises a UAC prompt and the flag never reaches
# the config or the device lanes (tests/test_elevated_flag.py).
# ---------------------------------------------------------------------------

# Both accepted spellings, lowercased for matching; the START_FLAG_FOLD
# table canonicalizes the same pair for the parser.
ELEVATED_SPELLINGS = ("--elevated", "-elevated")

ELEVATE_DEV_MESSAGE = ('[Elevation] dev mode - relaunch the exe manually '
                       'or use scheduled task')

# ShellExecuteW failure band labels (cross-product polish 2026-11-16, FST
# mirror): the documented <= 32 return values are error codes, and the
# shipped failure line carried only the number, so a declined UAC prompt (5)
# and a missing exe (2) looked identical in wtd.log. Values verified against
# the installed Windows SDK: SE_ERR_* in um/shellapi.h, ERROR_BAD_FORMAT in
# shared/winerror.h (2026-11-16).
SE_ERR_NAMES = {
    2: 'SE_ERR_FNF',
    3: 'SE_ERR_PNF',
    5: 'SE_ERR_ACCESSDENIED',
    8: 'SE_ERR_OOM',
    11: 'ERROR_BAD_FORMAT',
    26: 'SE_ERR_SHARE',
    27: 'SE_ERR_ASSOCINCOMPLETE',
    28: 'SE_ERR_DDETIMEOUT',
    29: 'SE_ERR_DDEFAIL',
    30: 'SE_ERR_DDEBUSY',
    31: 'SE_ERR_NOASSOC',
    32: 'SE_ERR_DLLNOTFOUND',
}


def _se_err_name(code):
    """Readable label for a ShellExecuteW failure code; a code the SDK table
    above does not name keeps its number (never an invented symbol)."""
    name = SE_ERR_NAMES.get(code)
    return name if name is not None else f'code {code}'


def _shell32_is_user_an_admin():
    """The bare ctypes seam for IsUserAnAdmin (tests patch this)."""
    return bool(ctypes.windll.shell32.IsUserAnAdmin())


def _shell32_runas_relaunch(exe_path, parameters, work_dir):
    """The bare ctypes seam for ShellExecuteW(None, "runas", ...) (tests
    patch this); returns the HINSTANCE int (<= 32 = SE_ERR_* failure).
    restype is pinned to c_void_p (cross-product polish 2026-11-16): the
    documented return is an HINSTANCE-cast INT_PTR, so ctypes' default c_int
    truncates the high dword on x64 and a healthy relaunch could read as a
    failure; c_void_p hands the whole value back as an int (NULL arrives as
    None -> 0 = the failure band). work_dir pins the child's CWD to the
    parent's so relative --config / --save-as values resolve on the same
    footing as without the relaunch (the leg the FST unit's reviewer P2
    2026-11-16 required)."""
    shell32 = ctypes.windll.shell32
    shell32.ShellExecuteW.restype = ctypes.c_void_p
    return int(shell32.ShellExecuteW(None, 'runas', exe_path, parameters,
                                     work_dir, 1) or 0)


def _is_process_elevated():
    """Wrapped admin-state check: True = high IL already. A win32 failure
    is named in a warning and treated as NOT elevated - the honest runas
    prompt beats a false '[Elevation] running elevated' claim."""
    try:
        return _shell32_is_user_an_admin()
    except Exception as error:
        _log.warning(f"[Elevation] IsUserAnAdmin failed: {error!r} - "
                     "treating the process as not elevated")
        return False


def _is_dev_python():
    """Dev-mode detection via sys.executable NAME: a source/pytest run
    lives under python.exe; the frozen build runs its own exe, so its
    basename never starts with 'python'."""
    return os.path.basename(sys.executable).lower().startswith('python')


def _strip_elevated_flag(argv):
    """The argv without either elevated spelling, inline values included
    (the same partition rule normalize_start_args uses); order preserved -
    the remaining tokens are the child's start line."""
    return [arg for arg in argv
            if arg.partition("=")[0].lower() not in ELEVATED_SPELLINGS]


def handle_elevated_flag(argv):
    """The --elevated startup lane - called by main() only when the parsed
    start line carries the flag; self-contained on purpose (a no-flag
    caller leaves every seam untouched).

    not flagged (self-check): argv is left alone, no win32 call at all.
    already elevated: ONE '[Elevation] running elevated' log line, the
    run continues.
    not elevated + dev run (python.exe): log ELEVATE_DEV_MESSAGE at INFO
    (console + wtd.log) and continue - no ShellExecute attempt, so no prompt
    and no loop.
    not elevated + frozen exe: ONE runas relaunch of sys.executable with
    the original args WITHOUT the flag (the child never sees it = loop
    guard), then exit 0 via SystemExit."""
    rest = _strip_elevated_flag(argv)
    if len(rest) == len(argv):
        return
    if _is_process_elevated():
        _log.info('[Elevation] running elevated')
        return
    if _is_dev_python():
        # user-facing instruction (FST F-B2 class: stays console-visible) AND
        # mirrored to wtd.log (polish P2, FST mirror): the shared 'wtd'
        # logger's stream sink prints the bare message to the CURRENT
        # sys.stdout at INFO - the _log.py contract keeps an INFO line
        # visible exactly where print was, so nothing moves on screen and the
        # line survives a console-less start (pythonw.exe / tray).
        _log.info(ELEVATE_DEV_MESSAGE)
        return
    _log.info('[Elevation] relaunching elevated via runas')
    # list2cmdline applies the exact MSVC argv-quoting rules the child's C
    # runtime reverses (FST reviewer P1 2026-11-16: hand-rolled space-
    # quoting corrupts spaced args with trailing backslashes or embedded
    # quotes); order preserved.
    se_result = _shell32_runas_relaunch(sys.executable,
                                        subprocess.list2cmdline(rest),
                                        os.getcwd())
    if se_result <= 32:
        # SE_ERR_* (5 = the declined UAC prompt): the exit code stays 0 per
        # spec, but the failure gets a named warning line - a silent exit
        # would leave the log indistinguishable from a successful relaunch.
        # The polish unit adds the SE_ERR_* name so the log says WHICH
        # failure happened; the 'runas relaunch returned N' phrase stays
        # because the shipped lane pins it.
        _log.warning(f'[Elevation] runas relaunch returned {se_result} '
                     f'({_se_err_name(se_result)}) - no elevated instance '
                     'started; launch the exe as administrator manually '
                     'if none appeared')
    sys.exit(0)


# CLI device lanes (U88 Part C): config-file maintenance without the driver.
# '--list', '--restore-with <name>' and '--save-device <alias>' (U413; the
# alias lane of '--save-as', kept working with a deprecation note) run
# against the loaded config and exit; the manager thread and the GUI stay
# untouched.

def looks_like_path(value):
    """True when a '--save-as' value names a file instead of a device alias.

    A value with a path separator or a file extension keeps the Part A
    save-path lane; any other value names a device alias.
    """
    return "/" in value or "\\" in value or bool(os.path.splitext(value)[1])


def resolve_cli_lanes(args):
    """Adapter over the single start parser: the legacy five-tuple view.

    Unit B2 moved the actual parsing to parse_start_args; this keeps the shape the
    device-lane pins already assert - (list_devices, restore_with, save_as_alias,
    notes, path_args) - where ``path_args`` are the tokens the lane options did
    not consume, so the path and flag lanes still see them. Unknown flags raise
    StartArgumentError instead of passing through silently. Prefer
    parse_start_args plus resolve_start_lanes in new code.
    """
    namespace, notes = parse_start_args(args)
    list_devices, restore_with, save_as_alias, _ = resolve_start_lanes(namespace, notes)
    bare_save_as = bool(namespace.save_as) and not looks_like_path(namespace.save_as)

    path_args = []
    skip_values = {"--restore-with", "--save-device"}
    skip_flags = {"--list"}
    tokens = namespace.start_tokens
    index = 0
    while index < len(tokens):
        token = tokens[index]
        name = token.partition("=")[0]
        if name in skip_flags:
            index += 1
            continue
        # '--save-as' belongs to the path lane unless ITS OWN value routed to the
        # device-alias lane, so only the alias form is consumed here (a path-form
        # value survives even when --save-device also set the alias).
        if name in skip_values or (name == "--save-as" and bare_save_as):
            if "=" not in token:
                index += 1
            index += 1
            continue
        path_args.extend(namespace.start_origins[index])
        index += 1

    return list_devices, restore_with, save_as_alias, notes, path_args


def find_device_entry(config, name):
    """Locate one stored device entry by alias or identity key.

    Order: identity key, the identity lane of a stored alias name, then the
    devices map. Returns (lane, key, entry); the entry can be empty when the
    name is known but carries no saved sections. None when nothing is stored.
    """
    if isinstance(config.device_settings.get(name), dict):
        return "identity", name, config.device_settings[name]
    for identity, stored_alias in config.device_aliases.items():
        if stored_alias != name:
            continue
        entry = config.device_settings.get(identity)
        return "identity", identity, entry if isinstance(entry, dict) else {}
    if isinstance(config.devices.get(name), dict):
        return "alias", name, config.devices[name]
    return None


def cli_list_devices(config):
    """Print the stored device entries (alias + identity key) and return them."""
    records = config.device_entry_records()
    _log.info(f"[Config] Stored devices ({len(records)}):")
    for alias, identity in records:
        _log.info(config.format_device_entry(alias, identity))
    return records


def cli_restore_with(config, name):
    """Load one device's saved values, then re-save the live values (option B).

    The entry validates all-or-nothing before it is applied; a rejected entry
    prints its errors and leaves the current settings untouched. On success
    the live snapshot replaces the stored entry, so the file mirrors the
    live state. Returns True when values were applied and re-saved.
    """
    found = find_device_entry(config, name)
    if found is None:
        _log.info(f"[Config Error] No saved device '{name}'. Nothing applied.")
        return False

    lane, key, entry = found
    sections = {section: value for section, value in entry.items()
                if section in DEVICE_SECTION_KEYS}
    validated, warnings, errors = config.validate_config_data(sections, "")
    for note in warnings:
        _log.info(f"[Config Warning] {lane} '{key}': {note}")
    if errors:
        for err in errors:
            _log.info(f"[Config Error] Invalid {lane} entry '{key}': {err}")
        _log.info(f"[Config] Restore of '{name}' aborted; current settings stay in place.")
        return False

    for attr, value in validated["motion"].items():
        setattr(config, attr, value)
    for attr, value in validated["toggles"].items():
        config.feature_toggles[attr] = value
    for attr, value in validated["mapping"].items():
        config.action_mapping[attr] = value
    config.sync_sensitivities()

    snapshot = snapshot_device_sections(config)
    if lane == "identity":
        config.set_device_identity_settings(key, snapshot)
    else:
        config.set_device_entry(key, snapshot)
    config.save_to_file()
    _log.info(f"[Config] Restored '{name}'; current live values re-saved as its state.")
    return True


def cli_save_as(config, alias):
    """Store the current settings under a new device alias and save the file.

    An existing alias with the same name is overwritten; the bindings and
    entries of other devices stay untouched.
    """
    config.set_device_entry(alias, snapshot_device_sections(config))
    config.save_to_file()
    _log.info(f"[Config] Current settings saved under alias '{alias}'.")
    return True


def main():
    global config
    prefer_wireless = False

    try:
        start, start_notes = parse_start_args(sys.argv[1:])
    except StartArgumentError as exc:
        # Strict since unit B2 (review Item 9): a start argument the driver does
        # not know is a mistake in the command line, so it is reported and the
        # driver does not start. The loop this replaces matched substrings, which
        # let '-wirelessize' quietly turn on wireless mode.
        _log.info(f"[Start Parameter] {exc}")
        return 1

    # --elevated startup lane (maintainer decision 2026-11-16): runs right
    # after the strict parse - so a bad command line never raises a UAC
    # prompt - and before any config read, device, or window init. A
    # not-elevated frozen run relaunches the exe elevated via runas and
    # exits 0 inside the lane; an elevated or dev run continues here (the
    # flag parsed into the namespace, so nothing downstream re-reads argv).
    if start.elevated:
        handle_elevated_flag(sys.argv[1:])

    list_devices, restore_with, save_as_alias, save_as_path = \
        resolve_start_lanes(start, start_notes)
    config_path, save_path, ladder_notes = resolve_config_ladder(
        start.config, save_as_path)
    for note in start_notes + ladder_notes:
        _log.info(note)

    # U412 / TODO #412: opt-in startup metrics readout (env WTD_METRICS_DIAG=1),
    # silent by default; diagnostic only, no mapping is consulted or changed.
    log_metrics_diagnostic()

    if start.debug:
        wtd_config.DEBUG = True  # runtime-shared: readers gate on wtd.config.DEBUG
        _log.info("[Start Parameter] DEBUG active")
    if start.wireless:
        prefer_wireless = True
        _log.info("[Start Parameter] Wireless preference active")

    if config_path != CONFIG_FILE_PATH:
        _log.info(f"[Start Parameter] Config file: '{config_path}'")
    # Unit B1: one config object, built here from the resolved lane. The
    # import-time global carries defaults only, so no file is read at import.
    config = DriverConfig(config_path)
    if save_path != config_path:
        _log.info(f"[Start Parameter] Save file: '{save_path}'")
    config.save_path = save_path

    # CLI maintenance lanes run against the loaded file and exit: the manager
    # thread and the GUI stay unstarted.
    if list_devices:
        cli_list_devices(config)
        return 0

    if restore_with or save_as_alias:
        applied = True
        if restore_with:
            applied = cli_restore_with(config, restore_with)
        if save_as_alias:
            cli_save_as(config, save_as_alias)
        return 0 if applied else 1

    _log.info("\n=== Initializing Wacom Device Manager Thread ===")

    touch_enabled_event = threading.Event()
    touch_enabled_event.set()

    stop_event = threading.Event()
    
    # Initialize and start the master management thread
    manager = DeviceManagerThread(touch_enabled_event, stop_event, prefer_wireless=prefer_wireless,
                                  poll_interval=5.0, base_config=config)
    manager.start()

    # Wait for the manager to attempt the initial connection
    time.sleep(1.0)

    # Fetch active connection references for GUI bindings (if available)
    conn = manager.get_primary_connection()
    if conn:
        state = conn['state']
        action_dispatch = conn['action']
        gui_config = conn['config']
    else:
        state = TabletState()  # Fallback empty state for GUI
        gui_config = config
        action_dispatch = None
    # U383: always carry the manager in the bundle -- the device section is
    # built above the tabs even when no device had connected at launch.
    device_info = build_device_info(conn, config, manager)

    if action_dispatch:
        actions = list(action_dispatch.ACTION_DISPATCH.keys())
        config.set_available_actions(actions)
        gui_config.set_available_actions(actions)

    _log.info("\n=== Wacom Touch Driver Running ===")

    try:
        from gui import launch_gui
        launch_gui(state, gui_config, touch_enabled_event, stop_event,
                   device_info=device_info)
    except SystemExit:
        pass
    except ImportError:
        # Fallback loop if GUI isn't installed in the environment
        try:
            while not stop_event.is_set():
                time.sleep(1)
        except KeyboardInterrupt:
            pass
    finally:
        stop_event.set()
        shutdown_readers(manager)
        _log.info("\n=== Wacom Touch Driver successfully stopped ===")

    return 0
