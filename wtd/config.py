"""Configuration: DriverConfig, config-path and orientation bounds
constants, and the device alias/identity helpers moved from
touch_controller."""

import json
import math
import os
import copy
import hashlib
from wtd._log import logger as _log  # shared wtd logger (see wtd/_log.py)



CONFIG_FILE_PATH = "user.cfg"
# Per-user config folder inside %APPDATA% (unit B1 / review Item 5).
APPDATA_DIR_NAME = "wtd"

DEBUG = False 
# DEBUG = True  

# Touch-part config (U381 / #381): byte 5 of the 0x03 report marks a finger in
# contact with express key n; the configured modifier is held for the duration
# of that contact and released on the recorded full-release state. Defaults are
# disabled (toggle False plus "none"), so the touch part fires nothing until
# the user enables it per button. Defined above DriverConfig: its defaults
# reference them, and the module config instance builds at import time.
TOUCH_CONTROL_KEYS = tuple(f"btn{n}_touch" for n in range(1, 9))
TOUCH_NO_ACTION = "none"
TOUCH_MODIFIER_KEYS = ("ctrl", "shift", "alt")
TOUCH_ACTION_CHOICES = (TOUCH_NO_ACTION,) + TOUCH_MODIFIER_KEYS

# ==============================================================================
# CONFIGURATION CLASS
# ==============================================================================
# Settings alias rule (U72 §4): model-class from the wired product_id.
# Two same-model tablets collapse to one alias. The wireless receiver
# (132 / 0x0084) cannot resolve its paired tablet from enumeration; it
# resolves through the manual binding map below, else the placeholder.
# 788 (0x0314, small) is listed upstream but never observed: unverified.
MODEL_CLASS_ALIASES = {788: "wacom-intuos5-s", 789: "wacom-intuos5-m", 791: "wacom-intuos5-l"}
WIRELESS_DEFAULT_ALIAS = "wireless"
# Sections a devices entry may carry. "bindings" is the manual override key
# (GUI tags the receiver twin once; stored per alias, U72 §4 fallback layer).
DEVICE_SECTION_KEYS = ("motion_and_thresholds", "feature_toggles", "action_mapping")
# WTD-14: per-section overlay acceptance iterates those file sections and
# routes each validator bucket back to its fork attribute.
DEVICE_SECTION_TO_BUCKET = {"motion_and_thresholds": "motion",
                            "feature_toggles": "toggles",
                            "action_mapping": "mapping"}


def resolve_device_alias(product_id, wireless, bindings=None):
    """Resolve the settings alias for one connection per the U72 §4 rule.

    Order: wired model-class table (authoritative) → manual binding map
    (the receiver-twin override) → wireless placeholder alias. Unknown
    devices resolve to None: global sections stay the only settings source.
    """
    alias = MODEL_CLASS_ALIASES.get(product_id)
    if alias is not None:
        return alias
    for bound_alias, bound_ids in (bindings or {}).items():
        if product_id in bound_ids:
            return bound_alias
    if wireless:
        return WIRELESS_DEFAULT_ALIAS
    return None


def snapshot_device_sections(config, section_names=DEVICE_SECTION_KEYS):
    """Snapshot the named sections of a live config instance as plain dicts.

    Only DEVICE_SECTION_KEYS are readable; the result is what a device entry
    or the '--save-device <alias>' lane stores (U413; the old bare-name
    '--save-as <alias>' spelling still routes here, with a notice).
    """
    sections = {}
    for name in section_names:
        if name == "motion_and_thresholds":
            sections[name] = {key: getattr(config, key) for key in config.MOTION_KEYS}
        elif name == "feature_toggles":
            sections[name] = dict(config.feature_toggles)
        elif name == "action_mapping":
            sections[name] = dict(config.action_mapping)
    return sections


# Device identity lane (U87): one interface's identity key is a hash over its
# full interface path plus its serial string. Interfaces never match exactly
# (#248 ruling), so a bare interface index is never an identity key.
DEVICE_ALIAS_PREFIX = "device_alias_"
DEVICE_SETTINGS_PREFIX = "device_settings_"
DEVICE_IDENTITY_HASH_LENGTH = 12
# Pen-area map lane (U414 / #406, approved design proposal §2): per-device
# list of user rectangles, one entry per display configuration, keyed by a
# sha1 display key. {"entries": [{"display_key", "monitors", "rect_px"}]}
PEN_AREA_MAP_PREFIX = "pen_area_map_"
# PENAREA (2026-11-16): the fractions are no longer bounded to 0..1 (the
# rect may extend outside the display union), so the schema keeps a
# magnitude bound instead: the normalized 0..65535 pen math must stay far
# from the float limit (a hand-edited 1e305 reaches `int(inf)` in
# update_pen_cursor). The editor's own cap is 10x the desktop, i.e.
# fractions around 10, so 100 is far above anything it can produce.
PEN_AREA_FRACTION_LIMIT = 100.0

# Wireless tablet size lane (U344 goal 2; renamed U386 / TODO #385): the
# wireless add-on is the same receiver (product id 132) on ALL tablet sizes,
# so the paired tablet size is NOT detectable from enumeration. The user
# config names the tablet size (small/medium/large) and the driver routes id
# 132 to the matching model table. Ruled 2026-11-07: the wireless path has
# nothing to do with the pen - the receiver connects directly to the tablet -
# so the old pen wording was renamed to tablet-size wording; the old config
# key stays readable as a deprecation alias with a loud notice. NO default:
# unset + non-detecting receiver = loud notice naming the choices, never a
# silent medium (planner ruling; the medium EXTENTS stay the labeled
# fallback until a size is chosen). The constants live above the config
# class: the module-level DriverConfig instance reads them at import time.
WIRELESS_RECEIVER_PRODUCT_ID = 132
# Maintainer ruling 2026-11-04 (U370 leg 5): a wireless package carries the
# SAME coordinates as wired (only fewer bundled fields), so this map selects
# the model extents preset and NOTHING else -- no separate wireless
# coordinate scaling exists or should be invented (pinned by
# test_wireless_size_selects_only_the_model_extents_preset).
WIRELESS_TABLET_SIZES = ("small", "medium", "large")
WIRELESS_TABLET_SIZE_TO_PRODUCT = {"small": 788, "medium": 789, "large": 791}
WIRELESS_TABLET_SIZE_KEY = "wireless_tablet_size"
WIRELESS_TABLET_SIZE_LEGACY_KEY = "wireless_pen_size"  # read-only deprecation alias

# U424 / #424 ruling: ONE stored pen-movement mode replaces the effective
# state of the old boolean toggles. `None` (= unset) derives from the
# legacy toggles (migration table, protocol Decision ledger): absolute off
# -> relative; absolute + stretch -> stretched; absolute, no stretch ->
# rect_1to1 (its documented no-rect fallback IS the old isotropic
# contain default - prior effective behavior preserved).
PEN_MODES = ("relative", "stretched", "rect_1to1")
PEN_MODE_KEY = "pen_mode"
# WTD-BATCH: rate cap of the reader-loop flush of the absolute lane's
# latest-wins move slot, in Hz. 250 Hz sits above any credible pad report
# rate (health traffic passes essentially uncollapsed) and far below any
# drain-burst rate.
PEN_MOVE_FLUSH_HZ_KEY = "pen_move_flush_hz"
PEN_MOVE_FLUSH_HZ_DEFAULT = 250.0


def resolved_pen_mode(config):
    """The effective movement mode: the stored `pen_mode` or its migration.

    The stored mode WINS over legacy toggle writes (no dual control, L1);
    unset falls back to the toggle-derived migration so never-migrated
    configs keep their prior effective behavior byte-unchanged.
    """
    mode = getattr(config, "pen_mode", None)
    if mode in PEN_MODES:
        return mode
    toggles = config.feature_toggles
    if not toggles.get("pen_absolute_mode", True):
        return "relative"
    if toggles.get("pen_stretch_full_desktop", False):
        return "stretched"
    return "rect_1to1"


def device_identity_hash(path, serial_number=""):
    """Identity key of one interface: 12-hex hash over interface path + serial.

    ``path`` is the hidapi path (bytes or str); the serial string participates
    even when empty, so the same path with a different serial stays distinct.
    """
    if isinstance(path, (bytes, bytearray)):
        path_text = path.decode("utf-8", errors="ignore")
    else:
        path_text = str(path)
    serial_text = serial_number or ""
    key = f"{path_text}|{serial_text}".encode("utf-8")
    return hashlib.sha1(key).hexdigest()[:DEVICE_IDENTITY_HASH_LENGTH]


class DriverConfig:
    # Keys saved under "motion_and_thresholds", in file order. Load validation
    # accepts exactly these keys.
    MOTION_KEYS = (
        "scroll_sensitivity_x", "scroll_sensitivity_y",
        "max_tap_duration", "double_tap_timeout", "tap_max_movement",
        "max_gesture_touch_session_duration", "press_hold_duration",
        "swipe_threshold_x", "swipe_threshold_y", "axis_dominance_ratio",
        "pinch_sensitivity", "pinch_discrete_threshold",
        "scroll_activation_threshold", "pinch_activation_threshold",
        "alt_tab_activation_threshold", "alt_tab_step_threshold",
        "alt_tab_step_sensitivity", "cursor_deadzone", "cursor_min_sens",
        "cursor_max_sens", "cursor_speed_low", "cursor_speed_high",
        "scroll_deadzone", "scroll_min_sens", "scroll_max_sens",
        "scroll_speed_low", "scroll_speed_high",
        "pen_speed", "pen_pressure_threshold",
        "dial_step_threshold", "orientation_degrees", "wireless_tablet_size",
        "pen_mode", "pen_stretch_screens", PEN_MOVE_FLUSH_HZ_KEY,
    )

    # Sections the saved file may hold. "devices" is the reserved slot (c):
    # accepted on load, no device-keyed behavior yet.
    CONFIG_SECTIONS = ("motion_and_thresholds", "feature_toggles",
                       "action_mapping", "devices")

    @staticmethod
    def _describe(value):
        """Short type/value label for config error messages."""
        return f"{type(value).__name__} {value!r}"

    @staticmethod
    def _coerce_number(value):
        """Return a float for numbers and numeric strings; None otherwise."""
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value.strip())
            except ValueError:
                return None
        return None

    @staticmethod
    def _coerce_bool(value):
        """Return a bool for bools and 0/1; None otherwise."""
        if isinstance(value, bool):
            return value
        if isinstance(value, int) and value in (0, 1):
            return bool(value)
        return None

    @staticmethod
    def _device_lane_key(section):
        """Return the identity-lane prefix when a top-level key is one, else None."""
        if section.startswith(DEVICE_ALIAS_PREFIX):
            return DEVICE_ALIAS_PREFIX
        if section.startswith(DEVICE_SETTINGS_PREFIX):
            return DEVICE_SETTINGS_PREFIX
        if section.startswith(PEN_AREA_MAP_PREFIX):
            return PEN_AREA_MAP_PREFIX
        return None

    @staticmethod
    def _validated_pen_area_entry(entry):
        """Return the normalized pen-area entry (rect_frac), or an error string.

        Schema (U416 / #406 defect round 1): display_key non-empty str,
        monitors list of str, rect_frac [rx, ry, rw, rh] FRACTIONS of the
        union bounds of the entry's own monitors (finite; 0 < rw,rh).
        PENAREA (2026-11-16): rx/ry may be negative and the rect may reach
        past 1 - the pen-area rect may extend outside the display union.
        Fractions survive hosts that answer junk
        for the virtual-screen metrics. Legacy U414 rect_px entries are
        MIGRATED at load by dividing through the union bounds of the
        entry's own monitor records - the exact source of the px datum
        (documented choice; no current-display guesswork, since those
        metrics may be the junk this schema exists to survive).
        """
        if not isinstance(entry, dict):
            return "must be an object"
        display_key = entry.get("display_key")
        if not isinstance(display_key, str) or not display_key:
            return "needs a non-empty 'display_key' string"
        monitors = entry.get("monitors")
        if not isinstance(monitors, list) or not all(isinstance(m, str) for m in monitors):
            return "needs a 'monitors' list of strings"
        if "rect_frac" in entry:
            values = entry["rect_frac"]
        elif "rect_px" in entry:
            legacy = entry["rect_px"]
            if (not isinstance(legacy, (list, tuple)) or len(legacy) != 4
                    or not all(isinstance(v, (int, float)) and not isinstance(v, bool)
                               for v in legacy)):
                return "legacy rect_px must be [rx, ry, rw, rh] numbers"
            union = union_rect_from_records(monitors)
            if union is None or union[2] <= 0 or union[3] <= 0:
                return ("legacy rect_px needs parseable 'monitors' bounds to "
                        "convert to rect_frac (U416)")
            values = [legacy[0] / union[2], legacy[1] / union[3],
                      legacy[2] / union[2], legacy[3] / union[3]]
        else:
            return "needs a 'rect_frac' list"
        if (not isinstance(values, (list, tuple)) or len(values) != 4
                or not all(isinstance(v, (int, float)) and not isinstance(v, bool)
                           for v in values)):
            return "rect_frac must be [rx, ry, rw, rh] numbers"
        rx, ry, rw, rh = (float(v) for v in values)
        # PENAREA (2026-11-16): the old "stay inside 0..1" containment rule
        # is retired. The rect may extend beyond the display union, so the
        # fractions may be negative or exceed 1; the schema keeps only the
        # integrity checks (finite values, positive size). The placement
        # policy is the editor's: "at least one corner of the pen-area
        # rectangle must remain inside the display area" (maintainer rule).
        if not all(math.isfinite(v) for v in (rx, ry, rw, rh)):
            return "rect_frac must be finite numbers"
        if rw <= 0 or rh <= 0:
            return "rect_frac needs positive width and height"
        if any(abs(v) > PEN_AREA_FRACTION_LIMIT for v in (rx, ry, rw, rh)):
            return ("rect_frac must stay within +/-"
                    f"{PEN_AREA_FRACTION_LIMIT:g} of the union bounds")
        return {"display_key": display_key, "monitors": list(monitors),
                "rect_frac": [rx, ry, rw, rh]}

    @staticmethod
    def _error_reference(raw_text, key, item_index, after_line=0, end_line=0):
        """Point at the offending entry: its file line when it has one,
        otherwise its item index inside the section. after_line (a lane
        header line, WTD-14) scopes the search STRICTLY BELOW that line
        and end_line bounds it to the lane's own block - needed by the
        per-device overlay lanes, where the same key text repeats across
        devices: a key that is NOT in the lane's saved block must not
        borrow another lane's line (honesty rule, decision 14). 0/0 keeps
        the old whole-file first match."""
        needle = f'"{key}"'
        for lineno, line in enumerate(
                raw_text.splitlines()[after_line:end_line or None],
                start=after_line + 1):
            if needle in line:
                return f"line {lineno}"
        return f"item {item_index + 1}"

    @staticmethod
    def _lane_start_line(raw_text, lane, key):
        """1-based line of one overlay lane's header in the saved file.

        WTD-14: identity lanes are named 'device_settings_<id>' (unique);
        alias entries are found by their alias key inside 'devices'.
        Returns 0 when the header is not in the text - the caller then
        names NO line (honest 'item N'), instead of searching the whole
        file and risking another device's line.
        """
        needle = (f'"{DEVICE_SETTINGS_PREFIX}{key}"' if lane == "identity"
                  else f'"{key}"')
        for lineno, line in enumerate(raw_text.splitlines(), start=1):
            if needle in line:
                return lineno
        return 0

    @staticmethod
    def _lane_block_end(raw_text, lane_start):
        """Last line of one lane's own block (WTD-14 honesty bound).

        The block ends right before the next top-level key - the first
        following line carrying the header's own indentation and a quote.
        A key that drifted in-memory but is absent from this saved block
        then falls back to 'item N' instead of borrowing a LATER lane's
        line. Returns the file's last line when no following key exists.
        """
        lines = raw_text.splitlines()
        header = lines[lane_start - 1]
        indent = header[:len(header) - len(header.lstrip())]
        for lineno in range(lane_start + 1, len(lines) + 1):
            if lines[lineno - 1].startswith(f'{indent}"'):
                return lineno - 1
        return len(lines)

    def _overlay_raw_text(self):
        """Best-effort saved-file text for fork-time line references.

        WTD-14: fork runs against the IN-MEMORY entry, which Apply Live
        may have changed since the file was written - a 'line N' here is
        FILE-relative and block-bounded; an entry that is not in the file
        degrades to the validator's 'item N' form. Any read/decode
        failure reads as no text (same policy as the load path's broad
        guard), never a crash on the fork hot path.
        """
        try:
            with open(self.config_path, "r") as f:
                return f.read()
        except Exception:
            return ""

    @staticmethod
    def _overlay_reject_summary(key, bad_names, applied_count):
        """One owner for the overlay reject summary line (WTD-14).

        All sections bad reproduces the pre-WTD-14 whole-reject line
        byte-for-byte (pinned). A partial reject names the rejected
        sections and states that the valid ones stayed applied.
        """
        if applied_count == 0:
            return (f"[Config Error] Overlay for '{key}' not applied; "
                    "previous settings stay.")
        return (f"[Config Error] Overlay for '{key}': "
                f"{', '.join(repr(n) for n in bad_names)} not applied; "
                f"{applied_count} valid section(s) applied.")

    def __init__(self, filepath=None):
        # Resolved load/save lanes; --config/--save-as overwrite these in main().
        self.config_path = filepath or CONFIG_FILE_PATH
        self.save_path = self.config_path
        # Reserved per-device map (c): identity rules are pending upstream research.
        self.devices = {}
        # Identity lanes (U87): user alias names and per-device settings,
        # each keyed by the device identity hash of its interface.
        self.device_aliases = {}
        self.device_settings = {}
        # Pen-area map lanes (U414 / #406): identity -> {"entries": [...]}.
        self.pen_area_maps = {}

        # Motion Parameters
        
        self.sensitivities = {}
        # Piecewise Linear Acceleration Settings Cursor
        self.cursor_deadzone = 0.0           # Distance (in px/packet) to discard as noise/jitter
        self.cursor_min_sens = 0.3          # Flat base multiplier for precision work
        self.cursor_max_sens = 1.6           # Hard cap multiplier (e.g., 2.5x to 3.0x base speed)
        self.cursor_speed_low = 5.0                 # Upper bound of flat precision zone (px/packet)
        self.cursor_speed_high = 80.0               # Speed at which max acceleration ceiling is reached
        self.sensitivities["cursor_acceleration"] = [self.cursor_deadzone, self.cursor_min_sens, self.cursor_max_sens, self.cursor_speed_low, self.cursor_speed_high, 1, 1]
        
        # Piecewise Linear Acceleration Settings Scrolling
        self.scroll_deadzone = 0.0           # Distance (in px/packet) to discard as noise/jitter
        self.scroll_min_sens = 1.2          # Flat base multiplier for precision work
        self.scroll_max_sens = 36.0           # Hard cap multiplier (e.g., 2.5x to 3.0x base speed)
        self.scroll_speed_low = 12.0                 # Upper bound of flat precision zone (px/packet)
        self.scroll_speed_high = 60.0               # Speed at which max acceleration ceiling is reached 
        # Scroll Sensitivity (Positive = Traditional, Negative = Natural)
        self.scroll_sensitivity_x = 1.0    # Positive = Natural, Negative = Traditional
        self.scroll_sensitivity_y = 1.0     # Natural Vertical Scrolling
        self.sensitivities["scroll_acceleration"] = [self.scroll_deadzone, self.scroll_min_sens, self.scroll_max_sens, self.scroll_speed_low, self.scroll_speed_high, self.scroll_sensitivity_x, self.scroll_sensitivity_y]
        
        # Minimal movement required to declare a 2F intention
        self.scroll_activation_threshold = 30.0  # Centroid distance in px before scrolling locks in
        self.pinch_activation_threshold = 40.0   # Spread change in px before pinching locks in
        
        # Pen Movement (U70): speed is pixels per millimeter of pen travel in
        # relative mode; absolute mode maps the digitizer to the screen 1:1.
        # The pressure threshold avoids light-contact flapping (units 0..2047).
        self.pen_speed = 8.0
        self.pen_pressure_threshold = 40.0
        # WTD-BATCH: positional-move flush rate cap (Hz) of the absolute
        # lane's latest-wins slot; see PEN_MOVE_FLUSH_HZ_DEFAULT above.
        self.pen_move_flush_hz = PEN_MOVE_FLUSH_HZ_DEFAULT

        # Rotary dial (U71): dial angle units traversed before one CW/CCW step
        # action fires. Recorded sweeps step 1..5 units per packet.
        self.dial_step_threshold = 4.0

        # Device orientation (U74; fractions accepted since the 2026-11-16
        # GUI wrap fix): degrees 0 <= d < 360. Rotates touch and
        # pen coordinates around the tablet center before the gesture/cursor
        # path; positive degrees turn the input clockwise on screen.
        self.orientation_degrees = 0

        # Wireless tablet size (U344 goal 2; renamed U386 / TODO #385): the
        # receiver id 132 is shared by every tablet size, so the user names
        # the size of the tablet the add-on is paired to; the driver routes
        # 132 to that model table. NO default (U386 planner ruling): unset
        # means no explicit choice, and the reader prints a loud notice
        # naming the choices instead of silently using medium.
        self.wireless_tablet_size = None
        # U424 / #424: the single stored movement mode (None = unset ->
        # toggle migration, see resolved_pen_mode). Set via set_pen_mode;
        # switching modes applies LIVE and never touches the stored rect.
        self.pen_mode = None
        # U424 / #424 ruling 1: stretched-mode screen subset (list of
        # monitor-record strings naming the stretch target; None/empty =
        # ALL screens, the pre-U424 full-desktop behavior).
        self.pen_stretch_screens = None

        # Gesture Thresholds
        self.max_tap_duration = 0.250       # Seconds
        self.double_tap_timeout = 0.250     # Seconds for 1F drag
        self.tap_max_movement = 10          # Coordinate drift 
        self.max_gesture_touch_session_duration = 0.300       # Seconds limit
        
        # 1-Finger Press-and-Hol Drag Config
        self.press_hold_duration = 0.200    # Seconds required to convert static touch into left-click hold
        
        # Motion & Directional Bounds for Gesture recognition (in Wacom coordinate units)
        self.swipe_threshold_x = 60      # Horizontal distance needed to trigger swipe
        self.swipe_threshold_y = 60      # Vertical distance needed to trigger swipe
        self.axis_dominance_ratio = 1.3  # Primary axis must be 1.3x larger than cross axis
        
        # Pinch Thresholds
        self.pinch_sensitivity = 1.0 # Pixels per continuous zoom step
        self.pinch_discrete_threshold = 50.0    # Distance change needed for 3F/4F pinch trigger
        
        # 5-Finger Alt-Tab Configuration
        self.alt_tab_activation_threshold = 40.0  # px distance to initiate Alt-Tab overlay
        self.alt_tab_step_threshold = 60.0        # px distance per window switch step 
        self.alt_tab_step_sensitivity = 0.2         # packets needed to move one step
        
        # Available Feature Toggles (True = Enabled, False = Disabled)        
        self.feature_toggles = {
            "cursor_acceleration": True,
            "scroll_acceleration": True,
            "1f_tap" : True,
            "1f_press" : True,
            "1f_double_tap" : True,
            "2f_tap" : False,
            "2f_press" : True,
            "2f_scroll" : True,
            "2f_pinch" : True,
            "3f_tap" : True,
            "3f_press" : False,
            "3f_swipe_up" : True,
            "3f_swipe_down" : True,
            "3f_swipe_left" : True,
            "3f_swipe_right" : True,
            "3f_pinch_in" : True,
            "3f_pinch_out" : True,
            "4f_tap" : True,
            "4f_press" : False,
            "4f_swipe_left" : True,
            "4f_swipe_right" : True,
            "4f_swipe_up" : True,
            "4f_swipe_down" : True,
            "4f_pinch_in" : True,
            "4f_pinch_out" : True,
            "5f_alt_tab" : True,
            "5f_tap" : False,
            "5f_press" : False,
            "5f_swipe_left" : False,
            "5f_swipe_right" : False,
            "5f_swipe_up" : False,
            "5f_swipe_down" : False,
            "5f_pinch_in" : False,
            "5f_pinch_out" : False,
            # Pen movement mode: True = absolute screen mapping, False = relative deltas.
            "pen_absolute_mode" : True,
            # Ultrawide mapping (U344 goal 3): False = 1:1 on both axes
            # (equal px-per-mm, centered slice); True = the whole digitizer
            # box stretched across the full desktop per axis.
            "pen_stretch_full_desktop" : False,
            # Touch buttons and rotary dial (U71): one toggle per control.
            "btn1_press" : True,
            "btn2_press" : True,
            "btn3_press" : True,
            "btn4_press" : True,
            "btn5_press" : True,
            "btn6_press" : True,
            "btn7_press" : True,
            "btn8_press" : True,
            "central_press" : True,
            "dial_cw" : True,
            "dial_ccw" : True,
            # Touch part of each express key (U381 #381): holds its modifier
            # while the finger keeps contact. Default DISABLED -- the touch
            # part fires nothing until the user turns it on per button.
            "btn1_touch" : False,
            "btn2_touch" : False,
            "btn3_touch" : False,
            "btn4_touch" : False,
            "btn5_touch" : False,
            "btn6_touch" : False,
            "btn7_touch" : False,
            "btn8_touch" : False,
            #"0f" : True,
        }
        
        # Available Preset Actions
        self.AVAILABLE_ACTIONS = [
            "left_click",
            "left_hold",
            "left_hold_release",
            "right_click",
            "middle_click",
            "task_view",
            "show_desktop",
            "window_up",
            "window_down",
            "window_left",
            "window_right",
            "window_minimize",
            "window_maximize",
            "desktop_left",
            "desktop_right",
            "next_window",
            "prev_window",
            "next",
            "prev",
            "undo",
            "redo",
            "ctrl_alt_tab_initiate",
            # TRAY unit (#448): assignable touch on/off toggle (flips the
            # shared tray touch-output flag; see ActionDispatcher.
            # handle_touch_toggle).
            "touch_toggle",
            # #472: clipboard trio, session lock and Esc - plain shortcut taps
            # (ActionDispatcher.ACTION_DISPATCH), selectable for any gesture,
            # pen button or tablet button. lock_workstation is Win+L, the same
            # lock the Start-menu Lock entry performs.
            "copy",
            "cut",
            "paste",
            "lock_workstation",
            "escape",
            # "ctrl_alt_tab_next",
            # "ctrl_alt_tab_prev",
            # "ctrl_alt_tab_commit"
        ]
        
        # Mappable Actions
        self.action_mapping = {
            # Taps
            "1f_tap": "left_click",
            "1f_double_tap" : "left_hold",
            "2f_tap": "right_click",
            "3f_tap": "middle_click",
            "4f_tap": "task_view",
            "5f_tap": "task_view",
            
            # Hold Presses            
            "1f_press" : "left_hold",
            "2f_press": "right_click",  # should it be deactivated for instant 2f scroll and pinch?
            "3f_press": "middle_click",
            "4f_press": "task_view",
            "5f_press": "task_view",    # supersided by 5f_alt_tab
            
            # 3-Finger Swipes & Pinches
            "3f_swipe_up": "window_up",         # Win + Up
            "3f_swipe_down": "window_down",     # Win + Down
            "3f_swipe_left": "window_left",     # Win + Left
            "3f_swipe_right": "window_right",   # Win + Right
            "3f_pinch_in": "undo",      
            "3f_pinch_out": "redo", 

            # 4-Finger Swipes & Pinches
            "4f_swipe_up": "task_view",         # Win + Tab
            "4f_swipe_down": "show_desktop",    # Win + D
            "4f_swipe_left": "prev",     # Alt + Left
            "4f_swipe_right": "next",     # Alt + Right
            "4f_pinch_in": "window_minimize",
            "4f_pinch_out": "window_maximize",

            # 5-Finger Swipes & Pinches     # overwritten/replaced by 5f_alt_tab
            "5f_swipe_up": "undo",     
            "5f_swipe_down": "redo",   
            "5f_swipe_left": "prev",   
            "5f_swipe_right": "next",  
            "5f_pinch_in": "window_minimize",
            "5f_pinch_out": "window_maximize",
            
            # Pen buttons (U70): status bits 1 and 2; press/release like a click.
            "pen_button_1": "right_click",
            "pen_button_2": "middle_click",

            # Touch buttons and rotary dial (U71). Button defaults are placeholder
            # actions; the Buttons tab remaps every control. Mouse-button actions
            # hold while pressed, other actions tap on the press edge.
            "btn1_press": "undo",
            "btn2_press": "redo",
            "btn3_press": "task_view",
            "btn4_press": "show_desktop",
            "btn5_press": "next",
            "btn6_press": "prev",
            "btn7_press": "left_click",
            "btn8_press": "right_click",
            "central_press": "middle_click",
            "dial_cw": "next",
            "dial_ccw": "prev",
            # Touch part of each express key (U381 #381): a modifier held for
            # the duration of the byte 5 contact, released on full release.
            # Vocabulary is TOUCH_ACTION_CHOICES, not AVAILABLE_ACTIONS; the
            # default "none" plus the False toggle keeps the part disabled.
            "btn1_touch": TOUCH_NO_ACTION,
            "btn2_touch": TOUCH_NO_ACTION,
            "btn3_touch": TOUCH_NO_ACTION,
            "btn4_touch": TOUCH_NO_ACTION,
            "btn5_touch": TOUCH_NO_ACTION,
            "btn6_touch": TOUCH_NO_ACTION,
            "btn7_touch": TOUCH_NO_ACTION,
            "btn8_touch": TOUCH_NO_ACTION,
            
            #"0f" : "left_hold_release",
        }
        
        
        # Unit B1 / review Item 5: constructing a config never touches the disk.
        # The import-time module global is therefore a defaults object, and
        # main() builds the real one from the resolved lane; a caller that names
        # a file explicitly still gets it loaded here.
        if filepath:
            self.load_from_file()
        self.sync_sensitivities()
        
    def set_available_actions(self, actions):
        self.AVAILABLE_ACTIONS = actions

    def sync_sensitivities(self):
        """Rebuild the derived acceleration parameter lists from the current fields."""
        self.sensitivities["cursor_acceleration"] = [
            self.cursor_deadzone,
            self.cursor_min_sens,
            self.cursor_max_sens,
            self.cursor_speed_low,
            self.cursor_speed_high,
            1, 1
        ]
        self.sensitivities["scroll_acceleration"] = [
            self.scroll_deadzone,
            self.scroll_min_sens,
            self.scroll_max_sens,
            self.scroll_speed_low,
            self.scroll_speed_high,
            self.scroll_sensitivity_x,
            self.scroll_sensitivity_y
        ]

    def save_to_file(self, filepath=None):
        filepath = filepath or self.save_path
        # B1: only the per-user lane's folder is created on demand - a fresh
        # install has no %APPDATA%\wtd yet and its first save must not fail. A
        # different missing folder stays a user mistake the driver does not
        # repair silently (reviewer note on the first draft).
        folder = os.path.dirname(filepath)
        if folder and os.path.normcase(folder) == os.path.normcase(
                os.path.dirname(appdata_config_path())):
            os.makedirs(folder, exist_ok=True)
        data = {
            "motion_and_thresholds": {key: getattr(self, key) for key in self.MOTION_KEYS},
            "feature_toggles": self.feature_toggles,
            "action_mapping": self.action_mapping,
            "devices": self.devices,
        }
        # Identity lanes (U87): flat keys per device, next to the sections.
        for identity, alias in self.device_aliases.items():
            data[f"{DEVICE_ALIAS_PREFIX}{identity}"] = alias
        for identity, sections in self.device_settings.items():
            data[f"{DEVICE_SETTINGS_PREFIX}{identity}"] = sections
        # Pen-area map lanes (U414 / #406), flat keys beside the device lanes.
        for identity, lane in self.pen_area_maps.items():
            data[f"{PEN_AREA_MAP_PREFIX}{identity}"] = lane
        with open(filepath, "w") as f:
            json.dump(data, f, indent=4)
        _log.info(f"[Config] Saved settings to '{filepath}'")

    def validate_config_data(self, data, raw_text="", after_line=0, end_line=0):
        """Validate a loaded dict against the current schema.

        Returns (validated, warnings, errors). This method applies nothing:
        ``validated`` holds coerced values, ``errors`` names each offending key
        with a location reference. Unknown keys and sections only warn.
        """
        validated = {"motion": {}, "toggles": {}, "mapping": {}}
        warnings = []
        errors = []

        if not isinstance(data, dict):
            errors.append(f"top level must be a JSON object, got {self._describe(data)}")
            return validated, warnings, errors

        for section in data:
            if section not in self.CONFIG_SECTIONS and self._device_lane_key(section) is None:
                ref = self._error_reference(raw_text, section, 0, after_line, end_line)
                warnings.append(f"unknown section '{section}' ({ref}) ignored")

        motion = data.get("motion_and_thresholds", {})
        if not isinstance(motion, dict):
            errors.append("'motion_and_thresholds' must be a JSON object, "
                          f"got {self._describe(motion)}")
        else:
            # U386 / TODO #385: 'wireless_pen_size' was renamed to
            # 'wireless_tablet_size' (ruling 2026-11-07: the wireless path
            # connects to the tablet, not the pen). The old key stays
            # readable as a deprecation alias; the loud notice names the new
            # key, and when both keys are present the new one wins.
            has_new_wireless_key = WIRELESS_TABLET_SIZE_KEY in motion
            for index, (key, val) in enumerate(motion.items()):
                ref = self._error_reference(raw_text, key, index, after_line, end_line)
                if key == WIRELESS_TABLET_SIZE_LEGACY_KEY:
                    if val is None:
                        normalized = None
                    elif isinstance(val, str) and val.strip().lower() in WIRELESS_TABLET_SIZES:
                        normalized = val.strip().lower()
                    else:
                        errors.append(f"'motion_and_thresholds.{key}' needs one of "
                                      f"{'/'.join(WIRELESS_TABLET_SIZES)} or null, "
                                      f"got {self._describe(val)} ({ref}) "
                                      f"[renamed to '{WIRELESS_TABLET_SIZE_KEY}']")
                        continue
                    if has_new_wireless_key:
                        warnings.append(
                            f"legacy key '{key}' ({ref}): renamed to "
                            f"'{WIRELESS_TABLET_SIZE_KEY}'; the new key is present, "
                            f"so the legacy value {val!r} is ignored "
                            f"(update your settings file)")
                    else:
                        warnings.append(
                            f"legacy key '{key}' ({ref}): renamed to "
                            f"'{WIRELESS_TABLET_SIZE_KEY}'; value {normalized!r} "
                            f"migrated (update your settings file - the old key "
                            f"will stop being read in a future version)")
                        validated["motion"][WIRELESS_TABLET_SIZE_KEY] = normalized
                    continue
                if key not in self.MOTION_KEYS:
                    warnings.append(f"unknown motion key '{key}' ({ref}) ignored")
                    continue
                if key == WIRELESS_TABLET_SIZE_KEY:
                    # U344 goal 2 + U386: a NAME, not a number -- accepted
                    # values are small/medium/large, case and space
                    # tolerant, or null for the explicit "not chosen" state.
                    if val is None:
                        validated["motion"][key] = None
                    elif isinstance(val, str) and val.strip().lower() in WIRELESS_TABLET_SIZES:
                        validated["motion"][key] = val.strip().lower()
                    else:
                        errors.append(f"'motion_and_thresholds.{key}' needs one of "
                                      f"{'/'.join(WIRELESS_TABLET_SIZES)} or null, "
                                      f"got {self._describe(val)} ({ref})")
                    continue
                if key == PEN_MODE_KEY:
                    # U424 / #424: a string enum, or null = unset (the
                    # effective mode then migrates from the old toggles).
                    if val is None:
                        validated["motion"][key] = None
                    elif val in PEN_MODES:
                        validated["motion"][key] = val
                    else:
                        errors.append(f"'motion_and_thresholds.{key}' needs one of "
                                      f"{'/'.join(PEN_MODES)} or null, "
                                      f"got {self._describe(val)} ({ref})")
                    continue
                if key == "pen_stretch_screens":
                    # U424: list of monitor-record strings, or null = all.
                    if val is None:
                        validated["motion"][key] = None
                    elif isinstance(val, list) and all(isinstance(v, str) for v in val):
                        validated["motion"][key] = list(val)
                    else:
                        errors.append(f"'motion_and_thresholds.{key}' needs a list "
                                      f"of monitor-record strings or null, "
                                      f"got {self._describe(val)} ({ref})")
                    continue
                number = self._coerce_number(val)
                if number is None:
                    errors.append(f"'motion_and_thresholds.{key}' needs a number, "
                                  f"got {self._describe(val)} ({ref})")
                elif key == "orientation_degrees":
                    # U74 + F1 (2026-11-16 GUI wrap fix): the rotation matrix
                    # accepts fractions, so file validation keeps the range
                    # refuse (0 <= d < 360) but accepts fractions; the file
                    # lane still never rewrites (the GUI entry wraps by 360).
                    if not 0 <= number < 360:
                        errors.append(f"'motion_and_thresholds.{key}' needs a "
                                      f"degree value 0<=d<360, "
                                      f"got {self._describe(val)} ({ref})")
                    else:
                        validated["motion"][key] = (int(number)
                                                    if number.is_integer()
                                                    else number)
                elif key == PEN_MOVE_FLUSH_HZ_KEY:
                    # WTD-BATCH: the positional-move flush cap in Hz. Must
                    # be above 0: 0 or a negative value would park the slot
                    # forever on the time gate (the per-report queue itself
                    # would keep running), so it is rejected, not clamped.
                    if number <= 0:
                        errors.append(f"'motion_and_thresholds.{key}' needs a "
                                      f"number above 0, got "
                                      f"{self._describe(val)} ({ref})")
                    else:
                        validated["motion"][key] = number
                else:
                    validated["motion"][key] = number

        toggles = data.get("feature_toggles", {})
        if not isinstance(toggles, dict):
            errors.append("'feature_toggles' must be a JSON object, "
                          f"got {self._describe(toggles)}")
        else:
            for index, (key, val) in enumerate(toggles.items()):
                ref = self._error_reference(raw_text, key, index, after_line, end_line)
                if key not in self.feature_toggles:
                    warnings.append(f"unknown feature toggle '{key}' ({ref}) ignored")
                    continue
                flag = self._coerce_bool(val)
                if flag is None:
                    errors.append(f"'feature_toggles.{key}' needs a boolean, "
                                  f"got {self._describe(val)} ({ref})")
                else:
                    validated["toggles"][key] = flag

        mapping = data.get("action_mapping", {})
        if not isinstance(mapping, dict):
            errors.append("'action_mapping' must be a JSON object, "
                          f"got {self._describe(mapping)}")
        else:
            for index, (key, val) in enumerate(mapping.items()):
                ref = self._error_reference(raw_text, key, index, after_line, end_line)
                if key not in self.action_mapping:
                    warnings.append(f"unknown gesture '{key}' ({ref}) ignored")
                    continue
                if not isinstance(val, str):
                    errors.append(f"'action_mapping.{key}' needs an action name, "
                                  f"got {self._describe(val)} ({ref})")
                elif val not in (TOUCH_ACTION_CHOICES if key in TOUCH_CONTROL_KEYS
                                 else self.AVAILABLE_ACTIONS):
                    # U381: a btn*_touch value names a touch modifier from
                    # TOUCH_ACTION_CHOICES; every other key takes an action
                    # name from AVAILABLE_ACTIONS.
                    errors.append(f"'action_mapping.{key}' maps to unknown action "
                                  f"{self._describe(val)} ({ref})")
                else:
                    validated["mapping"][key] = val

        if "devices" in data:
            devices = data["devices"]
            ref = self._error_reference(raw_text, "devices", 0, after_line, end_line)
            if not isinstance(devices, dict):
                errors.append(f"'devices' must be a JSON object, got {self._describe(devices)} ({ref})")
            else:
                validated_devices = {}
                for index, (alias, entry) in enumerate(devices.items()):
                    entry_ref = self._error_reference(raw_text, str(alias), index, after_line, end_line)
                    if not isinstance(alias, str) or not isinstance(entry, dict):
                        errors.append(f"'devices' entry {self._describe(alias)} must be a "
                                      f"named object ({entry_ref})")
                        continue
                    validated_devices[alias] = dict(entry)
                validated["devices"] = validated_devices

        # Identity lanes (U87): device_alias_<hash> carries the user name,
        # device_settings_<hash> carries the per-device sections.
        aliases = {}
        settings = {}
        pen_area_maps = {}
        lane_seen = False
        for index, (key, value) in enumerate(data.items()):
            lane = self._device_lane_key(key)
            if lane is None:
                continue
            lane_seen = True
            ref = self._error_reference(raw_text, key, index, after_line, end_line)
            identity = key[len(lane):]
            if lane == DEVICE_ALIAS_PREFIX:
                if not isinstance(value, str):
                    errors.append(f"'{key}' needs an alias name, "
                                  f"got {self._describe(value)} ({ref})")
                else:
                    aliases[identity] = value
            elif lane == PEN_AREA_MAP_PREFIX:
                # U414 / #406 lane: {"entries": [entry, ...]}; an entry whose
                # shape is wrong is named, the other entries still load.
                if not isinstance(value, dict) or not isinstance(value.get("entries"), list):
                    errors.append(f"'{key}' needs an object with an 'entries' list, "
                                  f"got {self._describe(value)} ({ref})")
                    continue
                valid_entries = []
                for entry_index, entry in enumerate(value["entries"]):
                    result = self._validated_pen_area_entry(entry)
                    if isinstance(result, str):
                        errors.append(f"'{key}' entry {entry_index + 1}: {result} ({ref})")
                    else:
                        valid_entries.append(result)
                if valid_entries:
                    pen_area_maps[identity] = {"entries": valid_entries}
            else:
                if not isinstance(value, dict):
                    errors.append(f"'{key}' needs a settings object, "
                                  f"got {self._describe(value)} ({ref})")
                    continue
                entry = {name: value[name] for name in DEVICE_SECTION_KEYS
                         if name in value}
                validated_entry, entry_warnings, entry_errors = \
                    self.validate_config_data(entry, raw_text)
                for note in entry_warnings:
                    warnings.append(f"'{key}': {note}")
                if entry_errors:
                    for err in entry_errors:
                        errors.append(f"'{key}': {err}")
                    continue
                settings[identity] = entry
        if lane_seen:
            validated["aliases"] = aliases
            validated["settings"] = settings
            if pen_area_maps:
                validated["pen_area_maps"] = pen_area_maps

        return validated, warnings, errors

    def load_from_file(self, filepath=None):
        """Load settings all-or-nothing: validate first, apply only when valid."""
        filepath = filepath or self.config_path
        if not os.path.exists(filepath):
            _log.info(f"[Config] No '{filepath}' found. Using default driver settings.")
            return False

        try:
            with open(filepath, "r") as f:
                raw_text = f.read()
            data = json.loads(raw_text)
        except Exception as e:
            _log.info(f"[Config Error] Failed loading '{filepath}': {e}")
            return False

        validated, warnings, errors = self.validate_config_data(data, raw_text)
        for note in warnings:
            _log.info(f"[Config Warning] {note}")

        if errors:
            for err in errors:
                _log.info(f"[Config Error] Invalid setting in '{filepath}': {err}")
            _log.info(f"[Config Error] Nothing applied from '{filepath}'. "
                  "Existing settings stay in place.")
            return False

        for key, value in validated["motion"].items():
            setattr(self, key, value)
        for key, value in validated["toggles"].items():
            self.feature_toggles[key] = value
        for key, value in validated["mapping"].items():
            self.action_mapping[key] = value
        if "devices" in validated:
            self.devices = validated["devices"]
        if "aliases" in validated:
            self.device_aliases = validated["aliases"]
        if "settings" in validated:
            self.device_settings = validated["settings"]
        if "pen_area_maps" in validated:
            self.pen_area_maps = validated["pen_area_maps"]

        self.sync_sensitivities()
        _log.info(f"[Config] Successfully loaded settings from '{filepath}'")
        return True

    def device_bindings(self):
        """Manual alias bindings: {alias: product ids} from the devices map.

        Invalid or missing "bindings" keys are skipped; alias resolution
        then falls through to the wireless placeholder as before.
        """
        bindings = {}
        for alias, entry in self.devices.items():
            if not isinstance(entry, dict):
                continue
            product_ids = entry.get("bindings")
            if not isinstance(product_ids, list):
                continue
            cleaned = [pid for pid in product_ids
                       if isinstance(pid, int) and not isinstance(pid, bool)]
            if cleaned:
                bindings[alias] = cleaned
        return bindings

    def device_entry_records(self):
        """Stored device entries as (alias, identity key) pairs, sorted by alias.

        A device_alias_<hash> lane names one identity; a devices-map alias
        without an identity lane pairs with None.
        """
        records = {}
        for identity, alias in self.device_aliases.items():
            if isinstance(alias, str) and alias:
                records[alias] = identity
        for alias in self.devices:
            records.setdefault(alias, None)
        return sorted(records.items())

    @staticmethod
    def format_device_entry(alias, identity):
        """One '--list' line: '<alias>\t<identity key>' with '-' for no key."""
        return f"{alias}\t{identity or '-'}"

    def fork_for_device(self, alias=None, identity=None):
        """Build one independent config instance for a connection.

        The fork carries this config's values; a present devices[alias]
        entry overlays it PER SECTION (WTD-14 / decision 14): each valid
        section applies, each section holding a validation error stays
        out untouched and is named in its own error lines - a bad action
        key no longer poisons the good motion/toggle sections. Within a
        section atomicity stays (one bad key drops the whole section).
        All sections bad reproduces the old whole-reject behavior and
        error line. A present device_settings[identity] entry (U87)
        overlays last, so per-device values win over alias values; global
        values stay the fallback. None overlays nothing.
        """
        clone = copy.copy(self)
        clone.sensitivities = {name: list(accel)
                               for name, accel in self.sensitivities.items()}
        clone.feature_toggles = dict(self.feature_toggles)
        clone.action_mapping = dict(self.action_mapping)
        clone.device_aliases = dict(self.device_aliases)
        clone.device_settings = dict(self.device_settings)

        overlays = (("devices", alias, self.devices.get(alias)),
                    ("identity", identity, self.device_settings.get(identity)))
        raw_text = None  # lazy: only a rejected section needs the file text
        for lane, key, entry in overlays:
            if key is None or entry is None:
                continue
            sections = {name: value for name, value in entry.items()
                        if name in DEVICE_SECTION_KEYS}
            applied = {}     # section name -> validated bucket
            rejected = []    # section names with validation errors
            for name, value in sections.items():
                validated, warnings, errors = self.validate_config_data(
                    {name: value}, "")
                for note in warnings:
                    # First pass has no file text; the rejected sections'
                    # second (line-ref) pass deliberately drops warnings
                    # so nothing logs twice.
                    _log.info(f"[Config Warning] {lane} '{key}': {note}")
                if errors:
                    rejected.append(name)
                else:
                    applied[name] = validated
            if rejected:
                # WTD-14 honesty split (decision 14 "if easily possible",
                # reviewer P1): name a file line ONLY when the lane's own
                # block is in the saved file - the scan is then bounded to
                # that block, so neither an unsaved in-memory entry nor a
                # drifted key can borrow another lane's line. Without a
                # block, the honest reference is 'item N'.
                if raw_text is None:
                    raw_text = self._overlay_raw_text()
                lane_start = self._lane_start_line(raw_text, lane, key)
                lane_end = (self._lane_block_end(raw_text, lane_start)
                            if lane_start else 0)
                for name in rejected:
                    _, _, errors = self.validate_config_data(
                        {name: sections[name]},
                        raw_text if lane_start else "",
                        lane_start, lane_end)
                    for err in errors:
                        _log.info(f"[Config Error] Invalid {lane} entry "
                                  f"'{key}': {err}")
                _log.info(self._overlay_reject_summary(
                    key, rejected, len(applied)))
            for name, validated in applied.items():
                bucket = DEVICE_SECTION_TO_BUCKET[name]
                values = validated[bucket]
                if bucket == "motion":
                    for attr, value in values.items():
                        setattr(clone, attr, value)
                elif bucket == "toggles":
                    for attr, value in values.items():
                        clone.feature_toggles[attr] = value
                else:
                    for attr, value in values.items():
                        clone.action_mapping[attr] = value

        clone.sync_sensitivities()
        return clone

    def set_device_entry(self, alias, sections, bindings=None):
        """Store one devices entry: named sections plus optional bindings.

        Entry keys not covered here stay untouched, so bindings survive a
        settings save and vice versa.
        """
        entry = dict(self.devices.get(alias, {}))
        for name, value in sections.items():
            entry[name] = value
        if bindings is not None:
            entry["bindings"] = list(bindings)
        self.devices[alias] = entry

    def alias_for_identity(self, identity):
        """The user alias stored for one device identity; None when unset."""
        if identity is None:
            return None
        alias = self.device_aliases.get(identity)
        return alias if isinstance(alias, str) and alias else None

    def set_device_alias(self, identity, alias):
        """Store the display name of one device identity (U87 part a)."""
        self.device_aliases[identity] = alias

    def set_device_identity_settings(self, identity, sections):
        """Store the current per-device settings under its identity key (U87 e)."""
        entry = dict(self.device_settings.get(identity, {}))
        for name, value in sections.items():
            entry[name] = value
        self.device_settings[identity] = entry

    def set_pen_mode(self, mode):
        """Switch the movement mode LIVE (U424 / #424 ruling 3).

        The running reader resolves the mode per report, so the switch
        needs no restart (the #386 Apply-Live shape). The stored 1:1 rect
        is deliberately untouched: leaving rect_1to1 keeps it (inactive,
        instantly re-usable). Raises ValueError for unknown modes.
        """
        if mode not in PEN_MODES:
            raise ValueError(f"pen_mode must be one of {'/'.join(PEN_MODES)}, "
                             f"got {mode!r}")
        self.pen_mode = mode

    def set_pen_area_entry(self, identity, display_key, monitors, rect_frac):
        """Store the pen-area FRACTIONAL rectangle for one display config.

        U414 / #406; U416: `rect_frac` is [rx, ry, rw, rh] FRACTIONS of the
        union bounds of `monitors` (fractional so metrics-less hosts can
        store and consume the rect). PENAREA: the fractions are NOT limited
        to 0..1 - an off-screen rect part stores as negative fractions or
        fractions past 1. The entry for the current
        `display_key` is replaced; entries of other configurations stay
        (proposal §2: the GUI saves the current configuration's entry
        only). Does not save the file. Raises ValueError when the entry
        shape fails the load schema.
        """
        # U414 STEP-1 P2-3: validate on the WRITE side too (same schema as
        # load); a caller with a malformed rect fails loudly here instead of
        # saving garbage that the next load would reject.
        entry = self._validated_pen_area_entry({"display_key": display_key,
                                                "monitors": monitors,
                                                "rect_frac": list(rect_frac)})
        if isinstance(entry, str):
            raise ValueError(f"pen-area entry rejected: {entry}")
        lane = self.pen_area_maps.setdefault(identity, {"entries": []})
        for position, existing in enumerate(lane["entries"]):
            if existing.get("display_key") == display_key:
                lane["entries"][position] = entry
                return
        lane["entries"].append(entry)


config = DriverConfig()


def union_rect_from_records(records):
    """Bounding (left, top, width, height) of parsed monitor records.

    Tolerates malformed rows (skipped); returns None when nothing parses.
    """
    boxes = []
    for record in records:
        parts = record.split("|")
        try:
            left, top = int(parts[1]), int(parts[2])
            width_txt, height_txt = parts[3].split("x")
            width, height = int(width_txt), int(height_txt)
        except (IndexError, ValueError):
            continue
        boxes.append((left, top, left + width, top + height))
    if not boxes:
        return None
    left = min(box[0] for box in boxes)
    top = min(box[1] for box in boxes)
    right = max(box[2] for box in boxes)
    bottom = max(box[3] for box in boxes)
    return (left, top, right - left, bottom - top)


# ==============================================================================
# MAIN ENTRY POINT
# ==============================================================================

def appdata_config_path(env=None):
    """The per-user config lane: %APPDATA%\\wtd\\user.cfg (unit B1 / Item 5).

    Part of the maintainer-approved search order: an explicit --config first,
    then a cwd user.cfg ONLY when one exists (so an existing portable install is
    never broken), then this per-user lane. Windows provides APPDATA; elsewhere
    the same per-user roaming folder is derived from the home directory so the
    lane exists on every platform instead of failing.
    """
    environment = os.environ if env is None else env
    base = environment.get("APPDATA")
    if not base:
        base = os.path.join(os.path.expanduser("~"), "AppData", "Roaming")
    return os.path.join(base, APPDATA_DIR_NAME, CONFIG_FILE_PATH)
