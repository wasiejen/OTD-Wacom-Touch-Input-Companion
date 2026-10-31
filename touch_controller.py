import hid
import time
import math
import threading
import json
import os
import ctypes
import sys
import re
import copy

from vk_codes import vk_codes_dict  # Import vk_code dictionary

CONFIG_FILE_PATH = "user.cfg"

DEBUG = False 
# DEBUG = True  


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", ctypes.c_ulong),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]

class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.c_ushort),
        ("wScan", ctypes.c_ushort),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]

class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", ctypes.c_ulong),
        ("wParamL", ctypes.c_ushort),
        ("wParamH", ctypes.c_ushort),
    ]

class INPUT(ctypes.Structure):
    class _INPUT(ctypes.Union):
        _fields_ = [
            ("mi", MOUSEINPUT),
            ("ki", KEYBDINPUT),
            ("hi", HARDWAREINPUT),
        ]

    _anonymous_ = ("_input",)
    _fields_ = [
        ("type", ctypes.c_ulong),
        ("_input", _INPUT),
    ]
        
#
# ==============================================================================
# ACTION DISPATCHER
# ==============================================================================

class ActionDispatcher():
    
    # ==============================================================================
    # WIN32.dll CTYPES (low level windows input)
    # ==============================================================================

    # Win32 Constants
    INPUT_MOUSE = 0
    INPUT_KEYBOARD = 1

    # Movement & Coordinate Control
    MOUSEEVENTF_MOVE           = 0x0001  # Mouse movement occurred
    MOUSEEVENTF_ABSOLUTE       = 0x8000  # Map dx/dy to absolute screen coords (0 to 65535)
    MOUSEEVENTF_VIRTUALDESK    = 0x4000  # Map coords to multi-monitor virtual desktop
    MOUSEEVENTF_MOVE_NOCOALESCE= 0x2000  # Do not combine mouse movement messages

    # Primary & Secondary Buttons
    MOUSEEVENTF_LEFTDOWN       = 0x0002  # Left button down
    MOUSEEVENTF_LEFTUP         = 0x0004  # Left button up
    MOUSEEVENTF_RIGHTDOWN      = 0x0008  # Right button down
    MOUSEEVENTF_RIGHTUP        = 0x0010  # Right button up
    MOUSEEVENTF_MIDDLEDOWN     = 0x0020  # Middle button down
    MOUSEEVENTF_MIDDLEUP       = 0x0040  # Middle button up

    # X-Buttons (Side / Extra Buttons)
    MOUSEEVENTF_XDOWN          = 0x0080  # An X-button was pressed
    MOUSEEVENTF_XUP            = 0x0100  # An X-button was released

    # Wheels / Panning
    MOUSEEVENTF_WHEEL          = 0x0800  # Vertical scroll wheel movement
    MOUSEEVENTF_HWHEEL         = 0x1000  # Horizontal scroll wheel movement

    # Keyboard Event Flags
    KEYEVENTF_KEYDOWN = 0x0000
    KEYEVENTF_KEYUP = 0x0002  
    
    # Pen button mapping actions: press/release flag pairs (U70).
    # Any other mapped action runs as a tap on the press edge.
    PEN_BUTTON_FLAGS = {
        "left_click": (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP),
        "right_click": (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP),
        "middle_click": (MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP),
    }

    def __init__(self, state, cfg=None):
        self.state = state
        # Per-connection config instance (U73); None keeps the module config.
        self.config = cfg
        # Inputs this dispatcher currently holds; released on thread exit.
        self.held_keys = set()
        self.left_held = False
        # Pen-held inputs: pressure left-hold and the two pen buttons.
        self.pen_pressure_held = False
        self.pen_button_held = {}
        # Tablet button-held inputs (U71): slots 1..8 express keys, 9 central.
        self.button_held = {}

        # The Dispatch Table
        self.ACTION_DISPATCH = {
            # Mouse actions
            "left_click": lambda: (self.send_mouse_click(self.MOUSEEVENTF_LEFTDOWN), self.send_mouse_click(self.MOUSEEVENTF_LEFTUP)),
            "right_click": lambda: (self.send_mouse_click(self.MOUSEEVENTF_RIGHTDOWN), self.send_mouse_click(self.MOUSEEVENTF_RIGHTUP)),
            "middle_click": lambda: (self.send_mouse_click(self.MOUSEEVENTF_MIDDLEDOWN), self.send_mouse_click(self.MOUSEEVENTF_MIDDLEUP)),
            
            # State-based holds (delegated to custom handlers if needed)
            "left_hold": self.handle_left_hold, 
            "left_hold_release": self.handle_left_hold_release,
            
            # Windows Shortcuts
            "task_view": self.key_tap("lwin", "tab"),
            "show_desktop": self.key_tap("lwin", "d"),
            "window_up": self.key_tap("lwin", "up"),
            "window_down": self.key_tap("lwin", "down"),
            "window_left": self.key_tap("lwin", "left"),  
            "window_right": self.key_tap("lwin", "right"),
            "window_minimize": self.key_tap("lwin", "down"),
            "window_maximize": self.key_tap("lwin", "up"),  
            
            "undo": self.key_tap("ctrl", "z"),
            "redo": self.key_tap("ctrl", "y"),
            "prev": self.key_tap("alt", "left"), 
            "next": self.key_tap("alt", "right"),
            
            # Media Keys
            "media_play_pause": self.key_tap("media_play"),
            "media_next": self.key_tap("media_next"),
            "media_prev": self.key_tap("media_prev"),
            "volume_up": self.key_tap("volume_up"),
            "volume_down": self.key_tap("volume_down"),
            "volume_mute": self.key_tap("volume_mute"),
            
            # Navigation
            "ctrl_alt_tab_initiate": self.key_tap("ctrl", "alt", "tab"),
            "ctrl_alt_tab_next": self.key_tap("right"),
            "ctrl_alt_tab_prev": self.key_tap("left"),
            "ctrl_alt_tab_commit": self.key_tap("enter"),
    
        }

    # ==============================================================================
    # HELPER FUNCTIONS - INPUT CTYPES
    # ==============================================================================
    
    def send_mouse_click(self, dw_flags, data=0):
        extra = ctypes.c_ulong(0)
        ii = INPUT()
        ii.type = self.INPUT_MOUSE
        ii.mi = MOUSEINPUT(0, 0, data, dw_flags, 0, ctypes.pointer(extra))
        ctypes.windll.user32.SendInput(1, ctypes.pointer(ii), ctypes.sizeof(ii))


    def send_mouse_scroll(self, dx, dy):
        """Handles vertical and horizontal scrolling with a fractional accumulator.

        Sub-notch deltas accumulate in state.scroll_acc_x/y instead of being
        truncated to zero, mirroring the cursor accumulator.
        """
        self.state.scroll_acc_x += dx
        self.state.scroll_acc_y += dy

        steps_x = int(self.state.scroll_acc_x)
        steps_y = int(self.state.scroll_acc_y)

        if steps_y != 0:
            # Wheel delta is typically 120 units per notch
            self.send_mouse_click(self.MOUSEEVENTF_WHEEL, steps_y)
            self.state.scroll_acc_y -= steps_y
        if steps_x != 0:
            self.send_mouse_click(self.MOUSEEVENTF_HWHEEL, steps_x)
            self.state.scroll_acc_x -= steps_x
            
    def send_key(self, vk_code, release=False):
        """Sends a key down or key up event using a VK code from vk_codes.py."""
        extra = ctypes.c_ulong(0)
        flags = self.KEYEVENTF_KEYUP if release else self.KEYEVENTF_KEYDOWN
        ii = INPUT()
        ii.type = self.INPUT_KEYBOARD
        ii.ki = KEYBDINPUT(
            vk_code, 0, flags, 0, ctypes.pointer(extra)
        )
        ctypes.windll.user32.SendInput(1, ctypes.pointer(ii), ctypes.sizeof(ii))

    def send_key_shortcut(self, *vk_codes):
        """Taps a combination like Win + Tab or Ctrl + Z."""
        for code in vk_codes:
            self.send_key(code, release=False)
        for code in reversed(vk_codes):
            self.send_key(code, release=True) 
            
    def send_real_mouse_move(self, dx: int, dy: int):
        """Sends a hardware-level mouse move event to Windows, forcing hover states."""
        extra = ctypes.c_ulong(0)
        ii = INPUT()
        ii.type = 0  # INPUT_MOUSE
        ii.mi = MOUSEINPUT(int(dx), int(dy), 0, self.MOUSEEVENTF_MOVE, 0, ctypes.pointer(extra))
        
        ctypes.windll.user32.SendInput(1, ctypes.pointer(ii), ctypes.sizeof(ii))
            
        # # Usage:
        # with hold_key(vk_codes_dict["ctrl"]):
        #     send_mouse_scroll(0, steps)

    # ==============================================================================
    # PEN INPUT HELPERS (U70)
    # ==============================================================================

    def send_pen_absolute_move(self, nx, ny):
        """Absolute pointer move in the 0..65535 virtual-desktop space."""
        extra = ctypes.c_ulong(0)
        ii = INPUT()
        ii.type = self.INPUT_MOUSE
        ii.mi = MOUSEINPUT(int(nx), int(ny), 0,
                           self.MOUSEEVENTF_MOVE | self.MOUSEEVENTF_ABSOLUTE | self.MOUSEEVENTF_VIRTUALDESK,
                           0, ctypes.pointer(extra))
        ctypes.windll.user32.SendInput(1, ctypes.pointer(ii), ctypes.sizeof(ii))

    def press_pen_pressure(self):
        """Pressure above threshold holds the left button like a mouse click."""
        self.send_mouse_click(self.MOUSEEVENTF_LEFTDOWN)
        self.pen_pressure_held = True

    def release_pen_pressure(self):
        if self.pen_pressure_held:
            self.send_mouse_click(self.MOUSEEVENTF_LEFTUP)
            self.pen_pressure_held = False

    def press_pen_button(self, slot, action_name):
        """Press edge of a pen button; mapped mouse buttons hold, others tap."""
        flags = self.PEN_BUTTON_FLAGS.get(action_name)
        if flags:
            self.send_mouse_click(flags[0])
            self.pen_button_held[slot] = action_name
        elif action_name is not None:
            self.execute_mapped_action(action_name)

    def release_pen_button(self, slot):
        """Release edge of a pen button; only held mouse buttons send a UP."""
        action_name = self.pen_button_held.pop(slot, None)
        if action_name:
            self.send_mouse_click(self.PEN_BUTTON_FLAGS[action_name][1])

    # ==============================================================================
    # TOUCH BUTTON HELPERS (U71)
    # ==============================================================================

    def press_tablet_button(self, slot, action_name):
        """Press edge of a tablet button (slots 1..8, 9 = central).

        Mouse-button actions hold on the press edge and release on the release
        edge, like the pen buttons; any other mapped action taps once here.
        """
        flags = self.PEN_BUTTON_FLAGS.get(action_name)
        if flags:
            self.send_mouse_click(flags[0])
            self.button_held[slot] = action_name
        elif action_name is not None:
            self.execute_mapped_action(action_name)

    def release_tablet_button(self, slot):
        """Release edge of a tablet button; only held mouse buttons send a UP."""
        action_name = self.button_held.pop(slot, None)
        if action_name:
            self.send_mouse_click(self.PEN_BUTTON_FLAGS[action_name][1])

    # ==============================================================================
    # HELPER FUNCTIONS - INPUT
    # ==============================================================================  

    
    # Helper to cleanly bind parameters for simple keys
    def key_tap(self, *vk_keys):
        return lambda: self.send_key_shortcut(*(vk_codes_dict[k] for k in vk_keys))

    def press_key(self, vk_code):
        self.send_key(vk_codes_dict[vk_code], release=False)
        self.held_keys.add(vk_code)
        
    def release_key(self, vk_code):
        self.send_key(vk_codes_dict[vk_code], release=True)
        self.held_keys.discard(vk_code)

    def release_held_inputs(self):
        """Releases every modifier key and the left button this dispatcher holds.

        Called on interface-thread exit so a pause or disconnect mid-gesture
        cannot leave Ctrl or the left button pressed system-wide.
        """
        released = []

        for vk_code in list(self.held_keys):
            self.release_key(vk_code)
            released.append(vk_code)

        if self.left_held:
            self.send_mouse_click(self.MOUSEEVENTF_LEFTUP)
            self.left_held = False
            self.state.is_left_held = False
            released.append("left_button")

        if self.pen_pressure_held:
            self.release_pen_pressure()
            released.append("pen_pressure")

        for slot, action_name in list(self.pen_button_held.items()):
            self.send_mouse_click(self.PEN_BUTTON_FLAGS[action_name][1])
            released.append(f"pen_button_{slot}")
        self.pen_button_held.clear()

        for slot, action_name in list(self.button_held.items()):
            self.send_mouse_click(self.PEN_BUTTON_FLAGS[action_name][1])
            released.append(f"button_{slot}")
        self.button_held.clear()

        if released:
            print(f"[Safety] Released held input on thread exit: {released}")

        return released

    def handle_left_hold(self):
        self.send_mouse_click(self.MOUSEEVENTF_LEFTDOWN)
        self.left_held = True
        self.state.is_left_held = True
        self.state.is_dragging_cursor = True
        self.state.active_gesture = None
        
    def handle_left_hold_release(self):
        self.send_mouse_click(self.MOUSEEVENTF_LEFTUP)
        self.left_held = False
        self.state.is_left_held = False
        self.state.active_gesture = None

    def execute_mapped_action(self, action_name):
        if DEBUG:
            print(f"[Gesture] Action triggered: {action_name}")

        action_func = self.ACTION_DISPATCH.get(action_name)
        if action_func:
            action_func()
        elif action_name is not None:
            print(f"[Warning] Unknown action name: {action_name}")

    def dispatch_gesture_event(self, event_type):
        cfg = self.config if self.config is not None else config
        if cfg.feature_toggles.get(event_type, False):
            mapping = cfg.action_mapping.get(event_type, None)
            if callable(mapping):
                action_name = mapping(self.state)
            else:
                action_name = mapping
            
            self.state.active_gesture = event_type
            self.execute_mapped_action(action_name)
             

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
        "dial_step_threshold",
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
    def _error_reference(raw_text, key, item_index):
        """Point at the offending entry: its file line when it has one,
        otherwise its item index inside the section."""
        needle = f'"{key}"'
        for lineno, line in enumerate(raw_text.splitlines(), start=1):
            if needle in line:
                return f"line {lineno}"
        return f"item {item_index + 1}"

    def __init__(self, filepath=None):
        # Resolved load/save lanes; --config/--save-as overwrite these in main().
        self.config_path = filepath or CONFIG_FILE_PATH
        self.save_path = self.config_path
        # Reserved per-device map (c): identity rules are pending upstream research.
        self.devices = {}

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

        # Rotary dial (U71): dial angle units traversed before one CW/CCW step
        # action fires. Recorded sweeps step 1..5 units per packet.
        self.dial_step_threshold = 4.0
        
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
            
            #"0f" : "left_hold_release",
        }
        
        
        # Attempt auto-load on startup
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
        data = {
            "motion_and_thresholds": {key: getattr(self, key) for key in self.MOTION_KEYS},
            "feature_toggles": self.feature_toggles,
            "action_mapping": self.action_mapping,
            "devices": self.devices,
        }
        with open(filepath, "w") as f:
            json.dump(data, f, indent=4)
        print(f"[Config] Saved settings to '{filepath}'")

    def validate_config_data(self, data, raw_text=""):
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
            if section not in self.CONFIG_SECTIONS:
                ref = self._error_reference(raw_text, section, 0)
                warnings.append(f"unknown section '{section}' ({ref}) ignored")

        motion = data.get("motion_and_thresholds", {})
        if not isinstance(motion, dict):
            errors.append("'motion_and_thresholds' must be a JSON object, "
                          f"got {self._describe(motion)}")
        else:
            for index, (key, val) in enumerate(motion.items()):
                ref = self._error_reference(raw_text, key, index)
                if key not in self.MOTION_KEYS:
                    warnings.append(f"unknown motion key '{key}' ({ref}) ignored")
                    continue
                number = self._coerce_number(val)
                if number is None:
                    errors.append(f"'motion_and_thresholds.{key}' needs a number, "
                                  f"got {self._describe(val)} ({ref})")
                else:
                    validated["motion"][key] = number

        toggles = data.get("feature_toggles", {})
        if not isinstance(toggles, dict):
            errors.append("'feature_toggles' must be a JSON object, "
                          f"got {self._describe(toggles)}")
        else:
            for index, (key, val) in enumerate(toggles.items()):
                ref = self._error_reference(raw_text, key, index)
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
                ref = self._error_reference(raw_text, key, index)
                if key not in self.action_mapping:
                    warnings.append(f"unknown gesture '{key}' ({ref}) ignored")
                    continue
                if not isinstance(val, str):
                    errors.append(f"'action_mapping.{key}' needs an action name, "
                                  f"got {self._describe(val)} ({ref})")
                elif val not in self.AVAILABLE_ACTIONS:
                    errors.append(f"'action_mapping.{key}' maps to unknown action "
                                  f"{self._describe(val)} ({ref})")
                else:
                    validated["mapping"][key] = val

        if "devices" in data:
            devices = data["devices"]
            ref = self._error_reference(raw_text, "devices", 0)
            if not isinstance(devices, dict):
                errors.append(f"'devices' must be a JSON object, got {self._describe(devices)} ({ref})")
            else:
                validated_devices = {}
                for index, (alias, entry) in enumerate(devices.items()):
                    entry_ref = self._error_reference(raw_text, str(alias), index)
                    if not isinstance(alias, str) or not isinstance(entry, dict):
                        errors.append(f"'devices' entry {self._describe(alias)} must be a "
                                      f"named object ({entry_ref})")
                        continue
                    validated_devices[alias] = dict(entry)
                validated["devices"] = validated_devices

        return validated, warnings, errors

    def load_from_file(self, filepath=None):
        """Load settings all-or-nothing: validate first, apply only when valid."""
        filepath = filepath or self.config_path
        if not os.path.exists(filepath):
            print(f"[Config] No '{filepath}' found. Using default driver settings.")
            return False

        try:
            with open(filepath, "r") as f:
                raw_text = f.read()
            data = json.loads(raw_text)
        except Exception as e:
            print(f"[Config Error] Failed loading '{filepath}': {e}")
            return False

        validated, warnings, errors = self.validate_config_data(data, raw_text)
        for note in warnings:
            print(f"[Config Warning] {note}")

        if errors:
            for err in errors:
                print(f"[Config Error] Invalid setting in '{filepath}': {err}")
            print(f"[Config Error] Nothing applied from '{filepath}'. "
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

        self.sync_sensitivities()
        print(f"[Config] Successfully loaded settings from '{filepath}'")
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

    def fork_for_device(self, alias=None):
        """Build one independent config instance for a connection.

        The fork carries this config's values; a present devices[alias]
        entry overlays its sections all-or-nothing. An invalid entry prints
        its errors and keeps the global values. None overlays nothing.
        """
        clone = copy.copy(self)
        clone.sensitivities = {name: list(accel)
                               for name, accel in self.sensitivities.items()}
        clone.feature_toggles = dict(self.feature_toggles)
        clone.action_mapping = dict(self.action_mapping)

        if alias is not None and alias in self.devices:
            entry = {key: value for key, value in self.devices[alias].items()
                     if key in DEVICE_SECTION_KEYS}
            validated, warnings, errors = self.validate_config_data(entry, "")
            for note in warnings:
                print(f"[Config Warning] devices '{alias}': {note}")
            if errors:
                for err in errors:
                    print(f"[Config Error] Invalid devices entry '{alias}': {err}")
                print(f"[Config Error] Overlay for '{alias}' not applied; "
                      "global settings stay.")
            else:
                for key, value in validated["motion"].items():
                    setattr(clone, key, value)
                for key, value in validated["toggles"].items():
                    clone.feature_toggles[key] = value
                for key, value in validated["mapping"].items():
                    clone.action_mapping[key] = value

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


config = DriverConfig()

# ==============================================================================
# HID HARDWARE CONSTANTS
# ==============================================================================
# Touch slots the report format carries. The per-contact-count start_time table
# has one extra cell; an over-cap contact count clamps to that last cell.
MAX_TOUCH_CONTACTS = 10

# Poll tick for the silent-device read loop, in seconds.
IDLE_POLL_SECONDS = 0.05
# Shortest idle wait; grows up to the poll tick and resets on every report.
IDLE_BACKOFF_MIN_SECONDS = 0.002

STATUS_TOUCH_DOWN = 0xC0
STATUS_RELEASE_TERMINAL = {0x20}
STATUS_ACTIVE_CONTACTS = {0x80, 0x90, 0x88, 0x98}

# Pen report (10-byte id 0x02): upstream Intuos-V1 field layout, documented
# in agent/research/2026-10-31_u68-otd-pen-layout.md (Wacom PTH-651).
PEN_REPORT_ID = 0x02
PEN_REPORT_LENGTH = 10
PEN_MAX_PRESSURE = 2047
# Status bits are LSB-first (upstream ByteExtensions.IsBitSet semantics).
PEN_STATUS_COORDS = 0x20        # bit 5: report carries coordinates (tablet report)
PEN_STATUS_OUT_OF_RANGE = 0x80  # status byte 0x80: pen left the digitizer range
PEN_TOOL_REPORT_STATUS = 0xC2   # tool report: bytes 2-3 carry serial, no coordinates
PEN_ROTATION_FLAG = 0x0A        # bits 1 and 3 together: rotation report
PEN_BUTTON_1_MASK = 0x02        # status bit 1: pen button 1
PEN_BUTTON_2_MASK = 0x04        # status bit 2: pen button 2
# Absolute moves use the 0..65535 virtual-desktop space (upstream
# WindowsAbsolutePointer projection).
PEN_VIRTUAL_RESOLUTION = 65535

# Digitizer ranges per product id, from upstream Wacom/PTH-651.json. Other
# Intuos Pro models need their own entries (PTH-450/451, PTH-850/851).
PEN_SPECS_BY_PRODUCT = {
    789: {"max_x": 44704, "max_y": 27940, "width_mm": 223.52, "height_mm": 139.7},
}
PEN_SPECS_DEFAULT = PEN_SPECS_BY_PRODUCT[789]

def pen_specs_for_product(product_id):
    """Digitizer ranges for the pen device; unknown ids fall back to defaults."""
    specs = PEN_SPECS_BY_PRODUCT.get(product_id)
    if specs is None:
        print(f"[Pen] No digitizer ranges for product {product_id}; "
              "using Intuos Pro Medium defaults (PTH-651)")
        return PEN_SPECS_DEFAULT
    return specs

def decode_pen_position(report):
    """Intuos-V1 X/Y: big-endian halves in bytes 2-5 plus the low bits of byte 9."""
    x = (report[3] | report[2] << 8) << 1 | ((report[9] >> 1) & 1)
    y = (report[5] | report[4] << 8) << 1 | (report[9] & 1)
    return x, y

def decode_pen_pressure(report):
    """Intuos-V1 pressure: 11 bits from bytes 6-7 plus the status bit 0 LSB."""
    return (report[6] << 3) | ((report[7] & 0xC0) >> 5) | (report[1] & 1)

# Button/dial report (10-byte id 0x03, U71): layout recorded in
# maintainer/draft/WTD_pen_support_probe_data.md lines 10-212 (Intuos Pro
# Medium, product 789). Byte 1 carries the constant presence flag 0x80 in every
# recorded row. Byte 3 carries the central button, byte 4 the press bitmask and
# byte 5 the touch-contact mask; both masks are one bit per express key, so
# buttons 1+2 pressed report 0x03 and buttons 2+3 report 0x06.
BUTTON_REPORT_ID = 0x03
BUTTON_REPORT_LENGTH = 10
BUTTON_STATUS_PRESENT = 0x80   # byte 1 presence flag; other shapes are unknown
BUTTON_CENTRAL_MASK = 0x01     # byte 3 bit 0: central button pressed
BUTTON_PRESS_MASKS = tuple(1 << n for n in range(8))   # byte 4 bits 0..7: buttons 1..8 pressed
BUTTON_TOUCH_MASKS = tuple(1 << n for n in range(8))   # byte 5 bits 0..7: buttons 1..8 in contact
CENTRAL_BUTTON_SLOT = 9        # dispatcher slot id held for the central button

# Dial: byte 2 marks a finger in the rotary field with the top bit and carries
# the angle in the low 7 bits. Recorded sweeps span 0x00..0x46 per revolution,
# so the wrap uses that step count. A decreasing angle is clockwise, an
# increasing one counter-clockwise.
DIAL_ANGLE_ACTIVE = 0x80
DIAL_ANGLE_MASK = 0x7F
DIAL_ANGLE_RANGE = 0x47

# ==============================================================================
# STATE TRACKER
# ==============================================================================
class TabletState:
    def __init__(self):
        self.pen_in_proximity = False
        
        # Active Contacts: slot_id -> (x, y)
        self.active_contacts = {}
        
        # Touch Session Metadata
        self.session_start_time = None
        self.session_duration = 0.0
        self.peak_contact_count = 0
        
        # 1-Finger Tracking
        self.f1_start_pos = None
        self.last_f1_pos = None
        self.max_drift = 0.0
        self.is_dragging_cursor = False
        self.last_f1_tap_time = 0.0
        
        # 1-Finger Double-Tap Drag
        self.drag_candidate = False
        self.is_left_held = False
        
        # Asymmetric & Multi-Finger Tracking
        self.f2_right_click_fired = False
        self.scroll_active = False
        self.last_2finger_centroid = None
        # Fractional scroll accumulator, used by send_mouse_scroll.
        self.scroll_acc_x = 0.0
        self.scroll_acc_y = 0.0
        self.was_moved = False

        # Single Latch State
        self.active_gesture = None
        
        # Multi-Finger Initial Baseline Data
        self.mf_start_time = None
        self.mf_start_centroid = None
        self.mf_start_spread = 0.0
        self.last_pinch_distance = 0.0
        
        # 5 Finger Alt-Tab State
        self.alt_tab_active = False
        self.last_alt_tab_x = 0
        self.f5_counter = 0
        
        self.cursor_acc_x = 0.0
        self.cursor_acc_y = 0.0
        
        # Pen input tracking (U70): last decoded tablet-report values.
        self.pen_position = None
        self.pen_pressure = 0
        self.pen_buttons = (False, False)
        self.pen_cursor_acc_x = 0.0
        self.pen_cursor_acc_y = 0.0

        # Touch button/dial tracking (U71): last-seen byte 4 press bitmask,
        # central-button press state, dial origin angle and step accumulator.
        self.button_press_mask = 0
        self.central_pressed = False
        self.dial_last_angle = None
        self.dial_acc = 0.0
        
        self.last_num_active = 0
        
        # One cell per contact count, from 0 to MAX_TOUCH_CONTACTS inclusive.
        self.start_time = [None] * (MAX_TOUCH_CONTACTS + 1)

# ==============================================================================
# HELPER FUNCTIONS
# ==============================================================================
def clamped_contact_index(count):
    """Index into TabletState.start_time for a contact count, clamped to the table.

    An over-cap report (more contacts than the format carries) must not raise
    inside the report loop; it clamps to the last cell and processing continues.
    """
    return min(max(count, 0), MAX_TOUCH_CONTACTS)


def compute_centroid_and_spread(contacts):
    pts = list(contacts.values())
    n = len(pts)
    if n == 0:
        return (0, 0), 0.0
    
    cx = sum(p[0] for p in pts) / n
    cy = sum(p[1] for p in pts) / n
    
    spread = sum(math.hypot(p[0] - cx, p[1] - cy) for p in pts) / n
    return (cx, cy), spread

def calculate_piecewise_accelerated_delta(dx, dy, config, feature_toggle):

    deadzone, min_sens, max_sens, speed_low, speed_high, sens_x, sens_y = config.sensitivities[feature_toggle]
    if config.feature_toggles.get(feature_toggle, False):
        speed = math.hypot(dx, dy)
        if speed < deadzone:
            return 0.0, 0.0
        
        if speed <= speed_low:
            gain = min_sens
        elif speed >= speed_high:
            gain = max_sens
        else:
            slope = (max_sens - min_sens) / (speed_high - speed_low)
            gain = min_sens + slope * (speed - speed_low)

        return dx * gain * sens_x, dy * gain * sens_y
    else:
        return dx * min_sens * sens_x, dy * min_sens * sens_y


            
# ==============================================================================
# PARSING & TOUCH PROCESSING
# ==============================================================================
def parse_pen_packet(report, state):
    """Proximity gate: marks pen presence so touch stays suppressed."""
    if not report or report[0] != 0x02:
        return
    pen_status = report[1]
    # if pen_status in (0x20, 0xE0, 0xE1, 0xE2):
    if pen_status in (0xE0, 0xE1, 0xE2):
        if not state.pen_in_proximity:
            state.pen_in_proximity = True
            print("[Palm Rejection] Pen detected -> Touch Disabled")
    elif pen_status == 0x80:
        if state.pen_in_proximity:
            state.pen_in_proximity = False
            current_time = time.time()
            state.session_start_time = current_time
            for i in range(len(state.start_time)):
                            state.start_time[i] = current_time
            #reset_tablet_state(full=True)
            
            print("[Palm Rejection] Pen left hover area -> Touch Enabled")

def release_pen_inputs(state, action_dispatch):
    """Release every held pen input and reset the relative origin."""
    ad = action_dispatch
    ad.release_pen_pressure()
    for slot in list(ad.pen_button_held):
        ad.release_pen_button(slot)
    state.pen_position = None
    state.pen_pressure = 0
    state.pen_buttons = (False, False)

def update_pen_cursor(state, action_dispatch, config, pen_specs, x, y):
    """Move the pointer: absolute maps the digitizer to the virtual desktop,
    relative sends scaled deltas with a fractional accumulator."""
    ad = action_dispatch
    absolute = config.feature_toggles.get("pen_absolute_mode", True)

    if absolute:
        nx = min(max(int(x * PEN_VIRTUAL_RESOLUTION / pen_specs["max_x"]), 0),
                 PEN_VIRTUAL_RESOLUTION)
        ny = min(max(int(y * PEN_VIRTUAL_RESOLUTION / pen_specs["max_y"]), 0),
                 PEN_VIRTUAL_RESOLUTION)
        state.pen_position = (x, y)
        ad.send_pen_absolute_move(nx, ny)
        return

    if state.pen_position is None:
        # First report after an origin reset: start point, no delta.
        state.pen_position = (x, y)
        return

    last_x, last_y = state.pen_position
    dx_mm = (x - last_x) * pen_specs["width_mm"] / pen_specs["max_x"]
    dy_mm = (y - last_y) * pen_specs["height_mm"] / pen_specs["max_y"]
    state.pen_position = (x, y)

    state.pen_cursor_acc_x += dx_mm * config.pen_speed
    state.pen_cursor_acc_y += dy_mm * config.pen_speed
    move_x = int(state.pen_cursor_acc_x)
    move_y = int(state.pen_cursor_acc_y)
    if move_x != 0 or move_y != 0:
        ad.send_real_mouse_move(move_x, move_y)
        state.pen_cursor_acc_x -= move_x
        state.pen_cursor_acc_y -= move_y

def update_pen_pressure_button(state, action_dispatch, config):
    """Pressure drives the left button: held at or above threshold, else released."""
    ad = action_dispatch
    pressed = state.pen_pressure >= config.pen_pressure_threshold
    if pressed and not ad.pen_pressure_held:
        ad.press_pen_pressure()
    elif not pressed and ad.pen_pressure_held:
        ad.release_pen_pressure()

def process_pen_report(report, state, action_dispatch, config, pen_specs):
    """Full pen input from the 10-byte 0x02 report (U70).

    The status byte switches the meaning of bytes 6-7: bits 1 and 3 mark a
    rotation report (pressure, tilt and buttons carry over from the previous
    report), bit 5 marks a tablet report (pressure valid). Tilt and hover are
    ignored per the approved ruling; pressure acts as left-click state.
    """
    if not report or report[0] != PEN_REPORT_ID or len(report) < PEN_REPORT_LENGTH:
        return

    status = report[1]
    ad = action_dispatch

    if status == PEN_STATUS_OUT_OF_RANGE:
        # Upstream resets the pointer origin and drops the follow-up report.
        release_pen_inputs(state, ad)
        return

    if status == PEN_TOOL_REPORT_STATUS:
        return

    is_rotation = (status & PEN_ROTATION_FLAG) == PEN_ROTATION_FLAG
    if not is_rotation and (status & PEN_STATUS_COORDS) == 0:
        # No coordinates in this report (tool/unknown variant): skip.
        return

    x, y = decode_pen_position(report)
    if is_rotation:
        # Bytes 6-7 carry rotation, not pressure; values carry over.
        new_buttons = state.pen_buttons
    else:
        state.pen_pressure = decode_pen_pressure(report)
        new_buttons = ((status & PEN_BUTTON_1_MASK) != 0,
                       (status & PEN_BUTTON_2_MASK) != 0)

    for slot, current in ((1, new_buttons[0]), (2, new_buttons[1])):
        if current != state.pen_buttons[slot - 1]:
            action_name = config.action_mapping.get(f"pen_button_{slot}")
            if current:
                ad.press_pen_button(slot, action_name)
            else:
                ad.release_pen_button(slot)
    state.pen_buttons = new_buttons

    update_pen_cursor(state, ad, config, pen_specs, x, y)
    update_pen_pressure_button(state, ad, config)

def decode_button_report(report):
    """Split the 10-byte 0x03 button/dial report into its column values.

    Returns None for a short packet or a shape byte 1 does not mark. The touch
    contact mask (byte 5) is returned but never gates a press: contact alone
    carries no button press.
    """
    if not report or report[0] != BUTTON_REPORT_ID or len(report) < BUTTON_REPORT_LENGTH:
        return None
    if not (report[1] & BUTTON_STATUS_PRESENT):
        return None
    return {
        "press": report[4],
        "touch": report[5],
        "central": bool(report[3] & BUTTON_CENTRAL_MASK),
        "dial": report[2],
    }

def dial_angle_delta(angle, last_angle):
    """Signed dial angle delta, wrapped at the recorded revolution span.

    Positive means counter-clockwise (increasing angle), negative means
    clockwise. The recorded sweeps wrap 0x00 <-> 0x46, so a delta further than
    half the span is the short way round the ring.
    """
    half = DIAL_ANGLE_RANGE // 2
    delta = angle - last_angle
    if delta > half:
        delta -= DIAL_ANGLE_RANGE
    elif delta < -half:
        delta += DIAL_ANGLE_RANGE
    return delta

def process_button_report(report, state, action_dispatch, config):
    """Touch buttons and the rotary dial from the 10-byte 0x03 report (U71).

    Byte 4 is a press bitmask (bit n = button n+1, so buttons 1+2 = 0x03);
    byte 3 bit 0 is the central button. Press and release edges drive the
    mapping table like the pen buttons. Byte 2 carries the dial angle while a
    finger is in the rotary field; deltas accumulate into CW/CCW step actions,
    mirroring the alt-tab step family. Touch contact (byte 5) fires nothing.
    """
    parsed = decode_button_report(report)
    if parsed is None:
        return

    ad = action_dispatch
    press_mask = parsed["press"]

    for slot in range(1, 9):
        bit = BUTTON_PRESS_MASKS[slot - 1]
        pressed = bool(press_mask & bit)
        if pressed == bool(state.button_press_mask & bit):
            continue
        key = f"btn{slot}_press"
        if pressed and config.feature_toggles.get(key, False):
            ad.press_tablet_button(slot, config.action_mapping.get(key))
        elif not pressed:
            ad.release_tablet_button(slot)
    state.button_press_mask = press_mask

    central = parsed["central"]
    if central != state.central_pressed:
        key = "central_press"
        if central and config.feature_toggles.get(key, False):
            ad.press_tablet_button(CENTRAL_BUTTON_SLOT, config.action_mapping.get(key))
        elif not central:
            ad.release_tablet_button(CENTRAL_BUTTON_SLOT)
        state.central_pressed = central

    raw_angle = parsed["dial"]
    if raw_angle & DIAL_ANGLE_ACTIVE:
        angle = raw_angle & DIAL_ANGLE_MASK
        if state.dial_last_angle is None:
            # First packet after a lift: origin only, no step.
            state.dial_last_angle = angle
        else:
            state.dial_acc += dial_angle_delta(angle, state.dial_last_angle)
            threshold = config.dial_step_threshold
            while threshold > 0 and abs(state.dial_acc) >= threshold:
                clockwise = state.dial_acc < 0
                key = "dial_cw" if clockwise else "dial_ccw"
                if config.feature_toggles.get(key, False):
                    ad.execute_mapped_action(config.action_mapping.get(key))
                state.dial_acc += threshold if clockwise else -threshold
        state.dial_last_angle = angle
    else:
        # Finger lifted from the rotary field: drop origin and partial step.
        state.dial_last_angle = None
        state.dial_acc = 0.0

def parse_sub_block(block):
    if len(block) < 5:
        return None
    slot_id, status = block[0], block[1]

    if slot_id == 0x81:
        return ('SYNC', status, None)
    if slot_id == 0:
        return None

    x = (block[2] << 4) | ((block[4] & 0xF0) >> 4)
    y = (block[3] << 4) | (block[4] & 0x0F)
    return (slot_id, status, (x, y))

def process_touch_report(report, state, action_dispatch, cfg=None):
    ad = action_dispatch
    cfg = config if cfg is None else cfg
    
    current_time = time.time()   

    if len(report) < 10 or report[0] != 0x02:
        return
    
    num_reports = report[1]
    parsed_blocks = []
    
    for i in range(num_reports):    
        b1 = parse_sub_block(report[2+(i*8):10+(i*8)])
        if b1: parsed_blocks.append(b1)
        
    for slot_id, status, pos in parsed_blocks:
        if slot_id == 'SYNC':
            if status == 0:
                state.active_contacts.clear()
            continue

        if status == STATUS_TOUCH_DOWN:
            state.active_contacts[slot_id] = pos
            state.start_time[clamped_contact_index(len(state.active_contacts))] = current_time
            state.peak_contact_count = max(state.peak_contact_count, len(state.active_contacts))
            if len(state.active_contacts) == 1 and state.last_num_active == 0:
                state.session_start_time = current_time
                if DEBUG: print(f"Debug: new session triggered")
                time_since_last_tap = current_time - state.last_f1_tap_time
                state.drag_candidate = (time_since_last_tap <= cfg.double_tap_timeout)
            if len(state.active_contacts) == 1:
                state.peak_contact_count = 1
                state.f1_start_pos = pos
                state.last_f1_pos = pos
                state.max_drift = 0.0
                state.is_dragging_cursor = False    
            elif len(state.active_contacts) == 2:
                state.f2_right_click_fired = False
                state.scroll_active = False

        elif status in STATUS_ACTIVE_CONTACTS:
            state.active_contacts[slot_id] = pos
                    
        elif status in STATUS_RELEASE_TERMINAL:
            state.active_contacts.pop(slot_id, None)
            state.start_time[clamped_contact_index(len(state.active_contacts))] = current_time
            if state.max_drift > cfg.tap_max_movement:
                state.was_moved = True

    num_active = len(state.active_contacts)
    contact_index = clamped_contact_index(num_active)
    
    if num_active != 2 and state.active_gesture == "2f_pinch":
        ad.release_key("ctrl")
    
    if state.session_start_time is not None:
        state.session_duration = current_time - state.session_start_time
        #if DEBUG: print(f"Debug: session duration: {session_duration}")
    if state.start_time[contact_index] is not None:
        hold_duration = current_time - state.start_time[contact_index]
    else:
        hold_duration = 0
        
    # reset centoid with every change of active contact to prevent jumping or misfiring of gestures
    if num_active >= 2:
        (cx, cy), spread =  compute_centroid_and_spread(state.active_contacts)
        
        if state.last_num_active != num_active or state.mf_start_centroid is None:
            if DEBUG: print(f"Debug: Reset due to active Contact number change.")
            # if state.mf_start_centroid is None:
            state.mf_start_centroid = (cx, cy)
            state.mf_start_spread = spread
            state.last_pinch_distance = spread
            state.last_alt_tab_x = cx  
    # if DEBUG: print(f"Debug: {num_active} fingers detected with Gesture: state: {state.active_gesture}")

    if not state.pen_in_proximity:
        
        # --- 1-FINGER NAVIGATION ---
        if num_active == 1:
            fid, pos = next(iter(state.active_contacts.items()))

            if state.last_num_active == 0 or (state.last_num_active >=2 and state.active_gesture in [None, "2f_scroll", "2f_pinch", "1f_tap","2f_tap", "2f_press"]):
                state.last_f1_pos = pos
                state.f1_start_pos = pos
                state.is_dragging_cursor = True
                state.last_2finger_centroid = None
                state.start_time[2]= None
                state.active_gesture = None
                state.max_drift = 0.0
                state.start_time[contact_index] = current_time # might be unnecessary

            if state.active_gesture is None:
                if state.f1_start_pos is not None and pos is not None:
                    dx_start = pos[0] - state.f1_start_pos[0]
                    dy_start = pos[1] - state.f1_start_pos[1]
                    drift = math.hypot(dx_start, dy_start)

                    if drift > state.max_drift:
                        state.max_drift = drift

                    if state.is_dragging_cursor and state.last_f1_pos is not None:
                        raw_dx = pos[0] - state.last_f1_pos[0]
                        raw_dy = pos[1] - state.last_f1_pos[1]
                        
                        if raw_dx != 0 or raw_dy != 0:
                            scaled_dx, scaled_dy = calculate_piecewise_accelerated_delta(raw_dx, raw_dy, cfg,"cursor_acceleration")
    
                            state.cursor_acc_x += scaled_dx
                            state.cursor_acc_y += scaled_dy
                            
                            move_x = int(state.cursor_acc_x)
                            move_y = int(state.cursor_acc_y)
                            
                            if move_x != 0 or move_y != 0:
                                ad.send_real_mouse_move(move_x, move_y)
                                state.cursor_acc_x -= move_x
                                state.cursor_acc_y -= move_y

                    if (current_time - state.last_f1_tap_time) < cfg.double_tap_timeout and state.max_drift <= cfg.tap_max_movement:
                        state.is_dragging_cursor = True
                        
                        if (cfg.feature_toggles.get("1f_double_tap", False) 
                                and state.drag_candidate 
                                and not state.is_left_held
                            ):
                            ad.dispatch_gesture_event("1f_double_tap")
                            #ad.execute_mapped_action("left_hold")
                            if DEBUG: print("[Gesture] 1F Double Tap Drag Initiated")

                    state.last_f1_pos = pos

        # --- 2-FINGER ACTIONS ---
        elif num_active == 2:
            # if state.first_time_2finger:
            #     state.first_time_2finger = False
            # else:

            dist_centroid = math.hypot(cx - state.mf_start_centroid[0], cy - state.mf_start_centroid[1])
            delta_spread_total = spread - state.mf_start_spread
            
            hold_duration = current_time - state.start_time[contact_index]

            if state.active_gesture is None:
                if abs(delta_spread_total) >= cfg.pinch_activation_threshold:
                    state.active_gesture = "2f_pinch"
                    ad.press_key("ctrl")
                    state.scroll_active = True
                elif dist_centroid >= cfg.scroll_activation_threshold:
                    state.active_gesture = "2f_scroll"
                    state.scroll_active = True

            if state.active_gesture == "2f_scroll" and cfg.feature_toggles.get("2f_scroll", False):
                if isinstance(state.last_2finger_centroid, tuple):
                    dx = cx - state.last_2finger_centroid[0]
                    dy = cy - state.last_2finger_centroid[1]
                    # print(f"dx,dy: {dx,dy}")    

                    scaled_dx, scaled_dy = calculate_piecewise_accelerated_delta(dx, dy, cfg, "scroll_acceleration")
                    
                    step_x = scaled_dx * -cfg.scroll_sensitivity_x
                    step_y = scaled_dy * cfg.scroll_sensitivity_y
                    
                    if step_x != 0 or step_y != 0:
                        # print(f"step_x,step_y: {step_x,step_y}")  
                        ad.send_mouse_scroll(step_x, step_y)

                state.last_2finger_centroid = (cx, cy)

            elif state.active_gesture == "2f_pinch" and cfg.feature_toggles.get("2f_pinch", False):
                delta_pinch = spread - state.last_pinch_distance
                ad.send_mouse_scroll(0, delta_pinch * cfg.pinch_sensitivity)
                state.last_pinch_distance += delta_pinch 
                
                # if abs(delta_pinch) >= cfg.pinch_continuous_sensitivity:
                #     steps = delta_pinch / cfg.pinch_continuous_sensitivity
                #     if steps != 0:
                #         send_ctrl_scroll(0, steps)
                #         state.last_pinch_distance += delta_pinch     
                

        # --- 3, 4, AND 5-FINGER GESTURES ---
        elif num_active > 2:
            if state.active_gesture in ("2f_scroll", "2f_pinch"):
                state.active_gesture = None
                state.last_f1_pos = None
                state.f1_start_pos = None
                state.scroll_active = False
                
            # if state.first_time_mutlifinger:
            #     state.first_time_mutlifinger = False
            # else:
                    
            state.is_dragging_cursor = False

            dx_total = cx - state.mf_start_centroid[0]
            dy_total = cy - state.mf_start_centroid[1]
            dist_centroid = math.hypot(dx_total, dy_total)
            delta_spread_total = spread - state.mf_start_spread
                
            if state.active_gesture is None and state.session_duration <= cfg.max_gesture_touch_session_duration:
                if num_active >= 5 and cfg.feature_toggles.get("5f_alt_tab", False):
                    state.active_gesture = "5f_alt_tab"
                    state.alt_tab_active = True
                    state.last_alt_tab_x = cx
                    ad.execute_mapped_action("ctrl_alt_tab_initiate")

                else:
                    prefix = f"{num_active}f_"
                    
                    if abs(delta_spread_total) >= cfg.pinch_discrete_threshold:
                        gesture_name = prefix + ("pinch_out" if delta_spread_total > 0 else "pinch_in")
                        ad.dispatch_gesture_event(gesture_name)

                    elif abs(dx_total) >= cfg.swipe_threshold_x and abs(dx_total) > cfg.axis_dominance_ratio * abs(dy_total):
                        gesture_name = prefix + ("swipe_right" if dx_total > 0 else "swipe_left")
                        ad.dispatch_gesture_event(gesture_name)

                    elif abs(dy_total) >= cfg.swipe_threshold_y and abs(dy_total) > cfg.axis_dominance_ratio * abs(dx_total):
                        gesture_name = prefix + ("swipe_down" if dy_total > 0 else "swipe_up")
                        ad.dispatch_gesture_event(gesture_name)
                        
            elif state.active_gesture == "5f_alt_tab" and num_active >= 5:
                dx_step = cx - state.last_alt_tab_x
                if abs(dx_step) >= cfg.alt_tab_step_threshold:
                    if state.f5_counter >= 1 / cfg.alt_tab_step_sensitivity: # to slow down the windows selection change
                        state.f5_counter = 0
                        if dx_step > 0:
                            ad.execute_mapped_action("ctrl_alt_tab_next")
                        else:
                            ad.execute_mapped_action("ctrl_alt_tab_prev")
                        state.last_alt_tab_x = cx
                    else:
                        state.f5_counter += 1
                else:
                    state.f5_counter = 0 # to make sure it does not change to fast after holding still
            
        # --- TAPS + RESET / RELEASE ---
        elif num_active == 0:
            if state.alt_tab_active:
                ad.execute_mapped_action("ctrl_alt_tab_commit")
                state.alt_tab_active = False
                state.last_alt_tab_x = 0
                
            elif state.is_left_held:
                ad.execute_mapped_action("left_hold_release")

            elif state.session_start_time is not None and state.active_gesture is None:

                #if DEBUG: print(f"session_duration: {session_duration}, cfg.max_tap_duratio: {cfg.max_tap_duration}, sd<max_dur: {session_duration <= cfg.max_tap_duration}")
                if state.session_duration <= cfg.max_tap_duration and not state.scroll_active:
                    if state.peak_contact_count == 1:
                        if not state.was_moved or not state.is_dragging_cursor:
                            ad.dispatch_gesture_event("1f_tap")
                            state.last_f1_tap_time = current_time

                    elif state.peak_contact_count == 2:
                        ad.dispatch_gesture_event("2f_tap")

                    elif state.peak_contact_count == 3:
                        ad.dispatch_gesture_event("3f_tap")

                    elif state.peak_contact_count == 4:
                        ad.dispatch_gesture_event("4f_tap")

                    elif state.peak_contact_count >= 5:
                        ad.dispatch_gesture_event("5f_tap")
                        
            state.session_start_time = None
            state.session_duration = 0.0
            
            state.peak_contact_count = 0
            for i in range(len(state.start_time)):
                state.start_time[i] = None

            state.f1_start_pos = None
            state.last_f1_pos = None
            
            state.is_dragging_cursor = False
            state.drag_candidate = False
            state.f2_right_click_fired = False
            state.scroll_active = False
            state.last_2finger_centroid = None
            state.was_moved = False
            state.is_left_held = False
            
            state.active_gesture = None
            state.mf_start_centroid = None
            state.mf_start_spread = 0.0
        
        
        # --- PRESS HANDLING ---
        if state.active_gesture is None and hold_duration >= cfg.press_hold_duration and not state.was_moved:
            # hold_duration = current_time - state.start_time[num_active]
            if num_active == 1 and cfg.feature_toggles.get("1f_press", False):
                if (not state.is_left_held 
                        and state.max_drift <= cfg.tap_max_movement
                        and not state.scroll_active
                        ):
                    ad.dispatch_gesture_event("1f_press")
                    if DEBUG: print("[Gesture] 1F Press & Hold Drag Initiated")
                    
            elif num_active >= 2 and cfg.feature_toggles.get(f"{num_active}f_press", False):    
                if (hold_duration >= cfg.press_hold_duration 
                        and dist_centroid <= cfg.tap_max_movement
                        and abs(delta_spread_total) <= cfg.pinch_discrete_threshold
                        ):
                    if DEBUG: print(f"[Gesture] Press hold for {num_active} Fingers triggered")
                    gesture_name = f"{num_active}f_press"
                    ad.dispatch_gesture_event(gesture_name)                    
        
    state.last_num_active = num_active

# ==============================================================================
# SIMPLIFIED INTERFACE WORKER THREAD
# ==============================================================================

def run_interface(interface_type, device_info, state, action_dispatch, touch_enabled_event, stop_event, idle_poll=IDLE_POLL_SECONDS, cfg=None):
    """
    Simplified worker thread for 'Pen', 'Touch' or the optional 'Buttons' input.
    Exits as soon as an OSError/Exception is thrown or stop_event is set or touch_enabled_event not set.
    Reads wait for reports at device cadence: a timed read is used when the HID
    binding offers read_timeout, otherwise non-blocking reads with an idle
    backoff that never sleeps between reports. Pause/stop still take effect
    within one poll tick while the device is silent.
    """
    interface_num = device_info.get('interface_number', -1)
    path = device_info.get('path')
    header = f"[{interface_type} Thread | Interface: {interface_num}]"
    cfg = config if cfg is None else cfg

    dev = hid.device()

    try:
        dev.open_path(path)
        dev.set_nonblocking(True)
        print(f"{header} Started successfully.")

        pen_specs = None
        if interface_type == "Pen":
            pen_specs = pen_specs_for_product(device_info.get("product_id"))
            print(f"{header} Init report not sent: upstream sends FeatureInitReport "
                  "'02 08' at device open; requirement unknown (pen data reads without it).")

        # Timed reads exist only on some HID bindings; the Windows hidapi wheel
        # exposes plain read() only, so the non-blocking fallback backs off from
        # IDLE_BACKOFF_MIN_SECONDS up to the poll tick and resets on every report.
        timed_read = getattr(dev, "read_timeout", None)
        idle_backoff = IDLE_BACKOFF_MIN_SECONDS

        while not stop_event.is_set():
            # Check pause state requirement
            if not touch_enabled_event.is_set():
                print(f"{header} Interrupted by touch disabled. Exiting interface thread.")
                break

            # Read and process packets according to input type
            if interface_type == "Touch":
                read_size = 66
            elif interface_type == "Pen":
                read_size = PEN_REPORT_LENGTH
            elif interface_type == "Buttons":
                read_size = BUTTON_REPORT_LENGTH
            else:
                read_size = 0

            if read_size == 0:
                report = []
            elif timed_read is not None:
                # Blocks up to one poll tick; returns the moment a report arrives.
                report = timed_read(read_size, int(idle_poll * 1000))
            else:
                report = dev.read(read_size)

            if report:
                if interface_type == "Touch":
                    process_touch_report(report, state, action_dispatch, cfg)
                elif interface_type == "Pen":
                    parse_pen_packet(report, state)
                    process_pen_report(report, state, action_dispatch, cfg, pen_specs)
                elif interface_type == "Buttons":
                    process_button_report(report, state, action_dispatch, cfg)
                # A report arrived: sample again immediately, no idle sleep.
                idle_backoff = IDLE_BACKOFF_MIN_SECONDS
            elif timed_read is not None:
                # The timed read already waited one poll tick for a report.
                pass
            else:
                # Silent device: bounded sleep that stop_event can wake early.
                idle_backoff = min(idle_backoff * 2, idle_poll)
                stop_event.wait(idle_backoff)

    except OSError as e:
        print(f"{header} Read Error (Disconnected/OSError): {e}")
    except Exception as e:
        print(f"{header} Unexpected Error: {e}")
        if DEBUG:
            raise e
    finally:
        try:
            action_dispatch.release_held_inputs()
        except Exception as e:
            print(f"{header} Held-input release failed: {e}")
        try:
            dev.close()
        except Exception:
            pass
        print(f"{header} Interface thread closed.")

# ==============================================================================
# MASTER DEVICE MANAGEMENT THREAD
# ==============================================================================

def select_active_device_pairs(paired_devices, prefer_wireless):
    """Choose the device pairs to activate and the ones to hold back.

    When a wireless and a wired pair are both present, one class wins:
    wireless when ``prefer_wireless`` is set, wired otherwise. A setup with
    only one class activates every pair. Returns (active, skipped).
    """
    wireless = [pair for pair in paired_devices if pair.get("wireless")]
    wired = [pair for pair in paired_devices if not pair.get("wireless")]

    if not wireless or not wired:
        return list(paired_devices), []

    if prefer_wireless:
        return wireless, wired
    return wired, wireless


class DeviceManagerThread(threading.Thread):
    VENDOR_ID = 0x056A  # Wacom Co., Ltd.
    PRODUCT_IDS = [132, 791, 789, 788]
    WIRELESS_PRODUCT_STRING = "Wacom Wireless Receiver"

    def __init__(self, enabled_event, stop_event, prefer_wireless=False, poll_interval=3.0, base_config=None):
        super().__init__(daemon=True)
        self.touch_enabled_event = enabled_event
        self.stop_event = stop_event
        self.prefer_wireless = prefer_wireless
        self.poll_interval = poll_interval
        self.lock = threading.Lock()
        # Global settings source; connections fork their own instance from it.
        self.base_config = base_config if base_config is not None else config
        
        # State tracking: path -> info dict
        # Structure per connected pair: 
        # {
        #   'device_path': str (key),
        #   'product_id': int,
        #   'product_string': str,
        #   'alias': str or None (settings alias, U73),
        #   'config': DriverConfig (per-connection fork),
        #   'state': TabletState,
        #   'action': ActionDispatcher,
        #   'threads': {'Pen': Thread, 'Touch': Thread, 'Buttons': Thread}
        # }
        # The 'Buttons' entry exists only when the device offers a third interface.
        self.active_connections = {}
        # Dedup for the connection-class skip notice; ids tuple or None.
        self._skipped_notice = None

    def run(self):
        print("[Manager Thread] Started Master Management Thread.")
        
        while not self.stop_event.is_set():
            # Wait until touch is ENABLED. If already enabled, wait(timeout) acts as a normal sleep delay.
            # If disabled (clear), it blocks indefinitely until touch_enabled_event.set() is called from GUI.                
            
            self.manage_connections()
                
            if self.touch_enabled_event.is_set():
                self.stop_event.wait(timeout=self.poll_interval)
            else:
                self.touch_enabled_event.wait()

        print("[Manager Thread] Master Management Thread stopped.")



    def get_device_parent_id(self, path_bytes):
        """Extracts base hardware instance ID by stripping interface (MI_xx) suffixes."""
        path_str = path_bytes.decode('utf-8', errors='ignore')
        return re.sub(r'&MI_\d+.*$', '', path_str)

    def enumerate_wacom_devices(self):
        # Store lists of interfaces per parent ID to support multiple identical tablets
        found_pen = {}    # parent_id -> list of pen dev dicts
        found_touch = {}  # parent_id -> list of touch dev dicts
        # Optional third interface carrying the 0x03 button/dial reports (U71).
        # Which interface number that is stays unrecorded until the maintainer's
        # interactive probe answers it, so any extra interface is offered as one.
        found_buttons = {}

        for dev in hid.enumerate():
            v_id = dev.get('vendor_id')
            p_id = dev.get('product_id')
            p_str = dev.get('product_string', '')

            if v_id == self.VENDOR_ID and p_id in self.PRODUCT_IDS:
                interface_num = dev.get('interface_number', -1)
                wireless = (p_str == self.WIRELESS_PRODUCT_STRING)
                
                pen_iface = 1 if wireless else 0
                touch_iface = 2 if wireless else 1

                parent_id = self.get_device_parent_id(dev['path'])

                if interface_num == pen_iface:
                    found_pen.setdefault(parent_id, []).append(dev)
                elif interface_num == touch_iface:
                    found_touch.setdefault(parent_id, []).append(dev)
                else:
                    found_buttons.setdefault(parent_id, []).append(dev)

        paired_devices = []

        # Pair pen & touch interfaces index-by-index for each unique physical device
        for parent_id, pen_list in found_pen.items():
            if parent_id in found_touch:
                touch_list = found_touch[parent_id]
                buttons_list = found_buttons.get(parent_id, [])
                
                # Pair corresponding interface instances (handles duplicate devices gracefully)
                for idx, pen_dev in enumerate(pen_list):
                    if idx < len(touch_list):
                        touch_dev = touch_list[idx]
                        buttons_dev = buttons_list[idx] if idx < len(buttons_list) else None
                        
                        # Create a truly unique device key using interface paths
                        unique_dev_id = f"{parent_id}_instance_{idx}"

                        paired_devices.append({
                            'id': unique_dev_id,
                            'product_id': pen_dev['product_id'],
                            'product_string': pen_dev['product_string'],
                            'wireless': pen_dev['product_string'] == self.WIRELESS_PRODUCT_STRING,
                            'pen_device': pen_dev,
                            'touch_device': touch_dev,
                            'buttons_device': buttons_dev
                        })

        return paired_devices

    def manage_connections(self):
        with self.lock:
            # 1. Clean up stale or exited threads
            dead_keys = []
            for dev_id, conn in self.active_connections.items():
                pen_alive = conn['threads']['Pen'].is_alive()
                touch_alive = conn['threads']['Touch'].is_alive()
                buttons = conn['threads'].get('Buttons')
                buttons_alive = buttons.is_alive() if buttons is not None else True

                # If any thread died (e.g. from OSError or paused), clean up state
                if not pen_alive or not touch_alive or not buttons_alive:
                    print(f"[Manager] Device disconnected/stopped: {conn['product_string']} ({dev_id})")
                    dead_keys.append(dev_id)

            for dev_id in dead_keys:
                del self.active_connections[dev_id]

            # 2. Update available devices and spawn new interface threads
            available_devices = self.enumerate_wacom_devices()
            active_devices, skipped_devices = select_active_device_pairs(
                available_devices, self.prefer_wireless)

            # Announce a held-back connection class once per change, not per poll.
            skipped_ids = tuple(pair['id'] for pair in skipped_devices)
            if skipped_ids != self._skipped_notice:
                self._skipped_notice = skipped_ids
                held_back = "wired" if self.prefer_wireless else "wireless"
                for pair in skipped_devices:
                    print(f"[Manager] Holding back {held_back} device "
                          f"{pair['product_string']} ({pair['id']})")

            for dev_pair in active_devices:
                dev_id = dev_pair['id']

                if dev_id not in self.active_connections:
                    alias = resolve_device_alias(
                        dev_pair['product_id'], dev_pair['wireless'],
                        self.base_config.device_bindings())
                    conn_config = self.base_config.fork_for_device(alias)
                    print(f"\n[Manager] New Device Detected! Initializing: {dev_pair['product_string']} "
                          f"(settings alias: {alias or 'none - global sections only'})")
                    
                    # Create unique state & dispatcher context per device
                    t_state = TabletState()
                    a_dispatch = ActionDispatcher(t_state, conn_config)

                    pen_dev = dev_pair['pen_device']
                    touch_dev = dev_pair['touch_device']
                    buttons_dev = dev_pair.get('buttons_device')

                    t_pen = threading.Thread(
                        target=run_interface,
                        args=("Pen", pen_dev, t_state, a_dispatch, self.touch_enabled_event, self.stop_event),
                        daemon=True
                    )
                    t_touch = threading.Thread(
                        target=run_interface,
                        args=("Touch", touch_dev, t_state, a_dispatch, self.touch_enabled_event, self.stop_event),
                        daemon=True
                    )

                    threads = {
                        'Pen': t_pen,
                        'Touch': t_touch
                    }

                    self.active_connections[dev_id] = {
                        'product_id': dev_pair['product_id'],
                        'product_string': dev_pair['product_string'],
                        'alias': alias,
                        'config': conn_config,
                        'state': t_state,
                        'action': a_dispatch,
                        'threads': threads
                    }

                    t_pen.start()
                    t_touch.start()
                    print(f"[Manager] Spawned Pen (Interface: {pen_dev.get('interface_number')}) "
                          f"& Touch (Interface: {touch_dev.get('interface_number')}) threads for {dev_id}")

                    if buttons_dev is not None:
                        # Third interface next to Pen/Touch (U71): 0x03 button/dial
                        # reports. Absent interface means the pair stays as before.
                        t_buttons = threading.Thread(
                            target=run_interface,
                            args=("Buttons", buttons_dev, t_state, a_dispatch,
                                  self.touch_enabled_event, self.stop_event),
                            daemon=True
                        )
                        threads['Buttons'] = t_buttons
                        t_buttons.start()
                        print(f"[Manager] Spawned Buttons thread "
                              f"(Interface: {buttons_dev.get('interface_number')}) for {dev_id}")

    def get_primary_state_and_action(self):
        """Helper to pass active state and dispatcher references to the GUI."""
        with self.lock:
            if self.active_connections:
                first_conn = next(iter(self.active_connections.values()))
                return first_conn['state'], first_conn['action']
            return None, None

    def get_primary_connection(self):
        """The first active connection dict, alias and config included (U73)."""
        with self.lock:
            if self.active_connections:
                return next(iter(self.active_connections.values()))
            return None


# ==============================================================================
# MAIN ENTRY POINT
# ==============================================================================

def resolve_config_paths(args):
    """Resolve the --config/--save-as start arguments from a raw argv slice.

    '--config <path>' sets the load path; '--save-as <path>' the save path;
    '--save-as' omitted means the save path follows '--config'. Neither option
    present keeps both lanes on CONFIG_FILE_PATH. Accepts '--config=<path>'
    too, and treats '-config'/'-save-as' as the same options.
    Returns (config_path, save_path, notes, other_args); ``other_args`` are the
    tokens left for the flag parser, so a path value never reads as a flag.
    """
    config_path = CONFIG_FILE_PATH
    save_path = None
    notes = []
    other_args = []

    index = 0
    while index < len(args):
        arg = args[index]
        name, _, inline_value = arg.partition("=")
        option = name.lower()

        if option not in ("--config", "-config", "--save-as", "-save-as"):
            other_args.append(arg)
            index += 1
            continue

        value = inline_value
        if not value and index + 1 < len(args):
            value = args[index + 1]
            index += 1
        if not value:
            notes.append(f"[Start Parameter] {name} needs a path - ignored")
            index += 1
            continue

        if option.endswith("config"):
            config_path = value
        else:
            save_path = value
        index += 1

    if save_path is None:
        save_path = config_path
    return config_path, save_path, notes, other_args


def main():
    global DEBUG
    global config
    prefer_wireless = False

    config_path, save_path, path_notes, other_args = resolve_config_paths(sys.argv[1:])
    for note in path_notes:
        print(note)

    for arg in other_args:
        if "-DEBUG" in arg.upper():
            DEBUG = True
            print("[Start Parameter] DEBUG active")
        elif "-wireless" in arg:
            prefer_wireless = True
            print("[Start Parameter] Wireless preference active")

    if config_path != CONFIG_FILE_PATH:
        print(f"[Start Parameter] Config file: '{config_path}'")
        # Rebuild from defaults so only the named file shapes the settings.
        config = DriverConfig(config_path)
    if save_path != config_path:
        print(f"[Start Parameter] Save file: '{save_path}'")
    config.save_path = save_path

    print("\n=== Initializing Wacom Device Manager Thread ===")

    touch_enabled_event = threading.Event()
    touch_enabled_event.set()

    stop_event = threading.Event()
    
    # Initialize and start the master management thread
    manager = DeviceManagerThread(touch_enabled_event, stop_event, prefer_wireless=prefer_wireless,
                                  poll_interval=5.0, base_config=config)
    manager.start()

    # Wait for the manager to attempt the initial connection
    time.sleep(1.0)

    # Fetch active state reference for GUI bindings (if available)
    state, action_dispatch = manager.get_primary_state_and_action()
    if not state:
        state = TabletState()  # Fallback empty state for GUI

    if action_dispatch:
        config.set_available_actions(list(action_dispatch.ACTION_DISPATCH.keys()))

    print("\n=== Wacom Touch Driver Running ===")

    try:
        from gui import launch_gui
        launch_gui(state, config, touch_enabled_event, stop_event)
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
        print("\n=== Wacom Touch Driver successfully stopped ===")

if __name__ == '__main__':
    main()