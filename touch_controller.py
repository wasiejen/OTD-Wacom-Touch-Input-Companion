import hid
import time
import math
import threading
import json
import os
import ctypes
from contextlib import contextmanager
import sys
import re

from vk_codes import vk_codes_dict  # Import vk_code dictionary

CONFIG_FILE_PATH = "user.cfg"

DEBUG = False 
# DEBUG = True  
running = True
interface_groups = []
used_interfaces = []



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
    
    def __init__(self, state):
        self.state = state

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
        """Handles vertical and horizontal scrolling."""
        if dy != 0:
            # Wheel delta is typically 120 units per notch
            self.send_mouse_click(self.MOUSEEVENTF_WHEEL, int(dy))
        if dx != 0:
            self.send_mouse_click(self.MOUSEEVENTF_HWHEEL, int(dx))
            
    def _send_wheel_event(self, flags, delta):
        """Direct low-level injector with correct signed DWORD bitmasking."""
        extra = ctypes.c_ulong(0)
        
        # Pack signed 32-bit int safely into DWORD bit-pattern
        dw_data = ctypes.c_ulong(delta & 0xFFFFFFFF)

        ii = INPUT()
        ii.type = self.INPUT_MOUSE
        ii.mi = MOUSEINPUT(0, 0, dw_data, flags, 0, ctypes.pointer(extra))
        
        ctypes.windll.user32.SendInput(1, ctypes.pointer(ii), ctypes.sizeof(ii))
            
    def send_ctrl_scroll(self, dx, dy):
        """Sends Ctrl + Scroll event via ctypes."""
        ctrl_vk = vk_codes_dict["ctrl"]
        
        print(f"dx: {dx}, dy: {dy}")
        # 1. Press Ctrl down
        self.send_key(ctrl_vk, release=False)
        # 2. Inject scroll delta
        self.send_mouse_scroll(dx, dy)
        # 3. Release Ctrl
        self.send_key(ctrl_vk, release=True)

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
            
    @contextmanager
    def hold_key(self, vk_code):
        self.send_key(vk_code, release=False)
        try:
            yield
        finally:
            self.send_key(vk_code, release=True)

        # # Usage:
        # with hold_key(vk_codes_dict["ctrl"]):
        #     send_mouse_scroll(0, steps)
        
    # ==============================================================================
    # HELPER FUNCTIONS - INPUT
    # ==============================================================================  

    
    # Helper to cleanly bind parameters for simple keys
    def key_tap(self, *vk_keys):
        return lambda: self.send_key_shortcut(*(vk_codes_dict[k] for k in vk_keys))

    def press_key(self, vk_code):
        self.send_key(vk_codes_dict[vk_code], release=False)
        
    def release_key(self, vk_code):
        self.send_key(vk_codes_dict[vk_code], release=True)

    def handle_left_hold(self):
        self.send_mouse_click(self.MOUSEEVENTF_LEFTDOWN)
        self.state.is_left_held = True
        self.state.is_dragging_cursor = True
        self.state.active_gesture = None
        
    def handle_left_hold_release(self):
        self.send_mouse_click(self.MOUSEEVENTF_LEFTUP)
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
        if config.feature_toggles.get(event_type, False):
            mapping = config.action_mapping.get(event_type, None)
            if callable(mapping):
                action_name = mapping(self.state)
            else:
                action_name = mapping
            
            self.state.active_gesture = event_type
            self.execute_mapped_action(action_name)
             

# ==============================================================================
# CONFIGURATION CLASS
# ==============================================================================
class DriverConfig:
    def __init__(self):
        
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
        
        # Gesture Thresholds
        self.max_tap_duration = 0.250       # Seconds
        self.double_tap_timeout = 0.250     # Seconds for 1F drag
        self.tap_max_movement = 10          # Coordinate drift 
        self.max_gesture_touch_session_duration = 0.300       # Secondslimit
        
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
            "4f_swipe_left": "prev",     # Win + Ctrl + Left
            "4f_swipe_right": "next",     # Win + Ctrl + Right
            "4f_pinch_in": "window_minimize",
            "4f_pinch_out": "window_maximize",

            # 5-Finger Swipes & Pinches     # overwritten/replaced by 5f_alt_tab
            "5f_swipe_up": "undo",     
            "5f_swipe_down": "redo",   
            "5f_swipe_left": "prev",   
            "5f_swipe_right": "next",  
            "5f_pinch_in": "window_minimize",
            "5f_pinch_out": "window_maximize",
            
            #"0f" : "left_hold_release",
        }
        
        
        # Attempt auto-load on startup
        self.load_from_file()
        
    def set_available_actions(self, actions):
        self.AVAILABLE_ACTIONS = actions
        
    def save_to_file(self, filepath=CONFIG_FILE_PATH):
        data = {
            "motion_and_thresholds": {
                "scroll_sensitivity_x": self.scroll_sensitivity_x,
                "scroll_sensitivity_y": self.scroll_sensitivity_y,
                "max_tap_duration": self.max_tap_duration,
                "double_tap_timeout": self.double_tap_timeout,
                "tap_max_movement": self.tap_max_movement,
                "max_gesture_touch_session_duration" : self.max_gesture_touch_session_duration,
                "press_hold_duration": self.press_hold_duration,
                "swipe_threshold_x": self.swipe_threshold_x,
                "swipe_threshold_y": self.swipe_threshold_y,
                "axis_dominance_ratio": self.axis_dominance_ratio,
                "pinch_sensitivity": self.pinch_sensitivity,
                "pinch_discrete_threshold": self.pinch_discrete_threshold,
                "scroll_activation_threshold": self.scroll_activation_threshold,
                "pinch_activation_threshold": self.pinch_activation_threshold,
                "alt_tab_activation_threshold": self.alt_tab_activation_threshold,
                "alt_tab_step_threshold": self.alt_tab_step_threshold,
                "alt_tab_step_sensitivity": self.alt_tab_step_sensitivity,
                "cursor_deadzone": self.cursor_deadzone,
                "cursor_min_sens": self.cursor_min_sens,
                "cursor_max_sens": self.cursor_max_sens,
                "speed_low": self.cursor_speed_low,
                "speed_high": self.cursor_speed_high,
            },
            "feature_toggles": self.feature_toggles,
            "action_mapping": self.action_mapping
        }
        with open(filepath, "w") as f:
            json.dump(data, f, indent=4)
        print(f"[Config] Saved settings to '{filepath}'")

    def load_from_file(self, filepath=CONFIG_FILE_PATH):
        if not os.path.exists(filepath):
            print(f"[Config] No '{filepath}' found. Using default driver settings.")
            return

        try:
            with open(filepath, "r") as f:
                data = json.load(f)

            motion = data.get("motion_and_thresholds", {})
            for key, val in motion.items():
                if hasattr(self, key):
                    setattr(self, key, float(val))

            toggles = data.get("feature_toggles", {})
            for key, val in toggles.items():
                if key in self.feature_toggles:
                    self.feature_toggles[key] = bool(val)

            mappings = data.get("action_mapping", {})
            for key, val in mappings.items():
                if key in self.action_mapping:
                    self.action_mapping[key] = str(val)

            print(f"[Config] Successfully loaded settings from '{filepath}'")
        except Exception as e:
            print(f"[Config Error] Failed loading '{filepath}': {e}")
config = DriverConfig()

# ==============================================================================
# HID HARDWARE CONSTANTS
# ==============================================================================
VENDOR_ID = 0x056A  # Wacom Co., Ltd.
PRODUCT_ID = None   # Auto-detect or integer PID

STATUS_TOUCH_DOWN = 0xC0
STATUS_RELEASE_TERMINAL = {0x20}
STATUS_ACTIVE_CONTACTS = {0x80, 0x90, 0x88, 0x98}

# ==============================================================================
# STATE TRACKER
# ==============================================================================
class TabletState:
    def __init__(self):
        self.touch_paused = False
        self.running = True
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
        self.f2_start_pos = None
        self.f2_right_click_fired = False
        self.scroll_active = False
        self.pinch_active = False
        self.first_time_2finger = True
        self.first_time_mutlifinger = True
        self.last_2finger_centroid = None
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
        
        self.last_num_active = 0
        
        self.start_time = []
        for i in range(11):
            self.start_time.append(None)
            
    def set_running_global(self, var):
        global running
        running = var

# state = TabletState()


# ==============================================================================
# HELPER FUNCTIONS
# ==============================================================================
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

def parse_sub_block(block):
    if len(block) < 5:
        return None
    byte2, byte3 = block[0], block[1]

    if byte2 == 0x81:
        return ('SYNC', byte3, None)
    if byte2 == 0:
        return None

    x = (block[2] << 4) | ((block[4] & 0xF0) >> 4)
    y = (block[3] << 4) | (block[4] & 0x0F)
    return (byte2, byte3, (x, y))

def process_touch_report(report, state, action_dispatch):
    ad = action_dispatch
    #if DEBUG: print(f"report: {report}")
    if state.touch_paused:
        return

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
            state.start_time[len(state.active_contacts)] = current_time
            state.peak_contact_count = max(state.peak_contact_count, len(state.active_contacts))
            if len(state.active_contacts) == 1 and state.last_num_active == 0:
                state.session_start_time = current_time
                if DEBUG: print(f"Debug: new session triggered")
                time_since_last_tap = current_time - state.last_f1_tap_time
                state.drag_candidate = (time_since_last_tap <= config.double_tap_timeout)
            if len(state.active_contacts) == 1:
                state.peak_contact_count = 1
                state.f1_start_pos = pos
                state.last_f1_pos = pos
                state.max_drift = 0.0
                state.is_dragging_cursor = False    
            elif len(state.active_contacts) == 2:
                state.f2_start_pos = pos
                state.f2_right_click_fired = False
                state.scroll_active = False

        elif status in STATUS_ACTIVE_CONTACTS:
            state.active_contacts[slot_id] = pos
                    
        elif status in STATUS_RELEASE_TERMINAL:
            state.active_contacts.pop(slot_id, None)
            state.start_time[len(state.active_contacts)] = current_time
            if state.max_drift > config.tap_max_movement:
                state.was_moved = True

    num_active = len(state.active_contacts)
    
    if num_active != 2 and state.active_gesture == "2F_PINCH":
        ad.release_key("ctrl")
    
    if state.session_start_time is not None:
        state.session_duration = current_time - state.session_start_time
        #if DEBUG: print(f"Debug: session duration: {session_duration}")
    if state.start_time[num_active] is not None:
        hold_duration = current_time - state.start_time[num_active]
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
        if num_active > 2:
            state.first_time_2finger = True
        if num_active == 2:
            state.first_time_multifinger = True

    # if DEBUG: print(f"Debug: {num_active} fingers detected with Gesture: state: {state.active_gesture}")

    if not state.pen_in_proximity:
        
        # --- 1-FINGER NAVIGATION ---
        if num_active == 1:
            fid, pos = next(iter(state.active_contacts.items()))

            if state.last_num_active == 0 or (state.last_num_active >=2 and state.active_gesture in [None, "2F_SCROLL", "2F_PINCH", "1f_tap","2f_tap", "2f_press"]):
                state.last_f1_pos = pos
                state.f1_start_pos = pos
                state.is_dragging_cursor = True
                state.last_2finger_centroid = None
                state.start_time[2]= None
                state.active_gesture = None
                state.max_drift = 0.0
                state.start_time[num_active] = current_time # might be unnecessary

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
                            scaled_dx, scaled_dy = calculate_piecewise_accelerated_delta(raw_dx, raw_dy, config,"cursor_acceleration")
    
                            state.cursor_acc_x += scaled_dx
                            state.cursor_acc_y += scaled_dy
                            
                            move_x = int(state.cursor_acc_x)
                            move_y = int(state.cursor_acc_y)
                            
                            if move_x != 0 or move_y != 0:
                                ad.send_real_mouse_move(move_x, move_y)
                                state.cursor_acc_x -= move_x
                                state.cursor_acc_y -= move_y

                    if (current_time - state.last_f1_tap_time) < config.double_tap_timeout and state.max_drift <= config.tap_max_movement:
                        state.is_dragging_cursor = True
                        
                        if (config.feature_toggles.get("1f_double_tap", False) 
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
            
            hold_duration = current_time - state.start_time[num_active]

            if state.active_gesture is None:
                if abs(delta_spread_total) >= config.pinch_activation_threshold:
                    state.active_gesture = "2F_PINCH"
                    ad.press_key("ctrl")
                    state.scroll_active = True
                elif dist_centroid >= config.scroll_activation_threshold:
                    state.active_gesture = "2F_SCROLL"
                    state.scroll_active = True

            if state.active_gesture == "2F_SCROLL" and config.feature_toggles.get("2f_scroll", False):
                if isinstance(state.last_2finger_centroid, tuple):
                    dx = cx - state.last_2finger_centroid[0]
                    dy = cy - state.last_2finger_centroid[1]
                    # print(f"dx,dy: {dx,dy}")    

                    scaled_dx, scaled_dy = calculate_piecewise_accelerated_delta(dx, dy, config, "scroll_acceleration")
                    
                    step_x = scaled_dx * -config.scroll_sensitivity_x
                    step_y = scaled_dy * config.scroll_sensitivity_y
                    
                    if step_x != 0 or step_y != 0:
                        # print(f"step_x,step_y: {step_x,step_y}")  
                        ad.send_mouse_scroll(step_x, step_y)

                state.last_2finger_centroid = (cx, cy)

            elif state.active_gesture == "2F_PINCH" and config.feature_toggles.get("2f_pinch", False):
                delta_pinch = spread - state.last_pinch_distance
                ad.send_mouse_scroll(0, delta_pinch * config.pinch_sensitivity)
                state.last_pinch_distance += delta_pinch 
                
                # if abs(delta_pinch) >= config.pinch_continuous_sensitivity:
                #     steps = delta_pinch / config.pinch_continuous_sensitivity
                #     if steps != 0:
                #         send_ctrl_scroll(0, steps)
                #         state.last_pinch_distance += delta_pinch     
                

        # --- 3, 4, AND 5-FINGER GESTURES ---
        elif num_active > 2:
            if state.active_gesture in ("2F_SCROLL", "2F_PINCH"):
                state.active_gesture = None
                state.last_f1_pos = None
                state.f1_start_pos = None
                state.scroll_active = False
                state.pinch_active = False
                
            # if state.first_time_mutlifinger:
            #     state.first_time_mutlifinger = False
            # else:
                    
            state.is_dragging_cursor = False

            dx_total = cx - state.mf_start_centroid[0]
            dy_total = cy - state.mf_start_centroid[1]
            dist_centroid = math.hypot(dx_total, dy_total)
            delta_spread_total = spread - state.mf_start_spread
                
            if state.active_gesture is None and state.session_duration <= config.max_gesture_touch_session_duration:
                if num_active >= 5 and config.feature_toggles.get("5f_alt_tab", False):
                    state.active_gesture = "5F_ALT_TAB"
                    state.alt_tab_active = True
                    state.last_alt_tab_x = cx
                    ad.execute_mapped_action("ctrl_alt_tab_initiate")

                else:
                    prefix = f"{num_active}f_"
                    
                    if abs(delta_spread_total) >= config.pinch_discrete_threshold:
                        gesture_name = prefix + ("pinch_out" if delta_spread_total > 0 else "pinch_in")
                        ad.dispatch_gesture_event(gesture_name)

                    elif abs(dx_total) >= config.swipe_threshold_x and abs(dx_total) > config.axis_dominance_ratio * abs(dy_total):
                        gesture_name = prefix + ("swipe_right" if dx_total > 0 else "swipe_left")
                        ad.dispatch_gesture_event(gesture_name)

                    elif abs(dy_total) >= config.swipe_threshold_y and abs(dy_total) > config.axis_dominance_ratio * abs(dx_total):
                        gesture_name = prefix + ("swipe_down" if dy_total > 0 else "swipe_up")
                        ad.dispatch_gesture_event(gesture_name)
                        
            elif state.active_gesture == "5F_ALT_TAB" and num_active >= 5:
                dx_step = cx - state.last_alt_tab_x
                if abs(dx_step) >= config.alt_tab_step_threshold:
                    if state.f5_counter >= 1 / config.alt_tab_step_sensitivity: # to slow down the windows selection change
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

                #if DEBUG: print(f"session_duration: {session_duration}, config.max_tap_duratio: {config.max_tap_duration}, sd<max_dur: {session_duration <= config.max_tap_duration}")
                if state.session_duration <= config.max_tap_duration and not state.scroll_active:
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
            
            state.f2_start_pos = None
            state.is_dragging_cursor = False
            state.drag_candidate = False
            state.f2_right_click_fired = False
            state.scroll_active = False
            state.pinch_active = False
            state.last_2finger_centroid = None
            state.was_moved = False
            state.first_time_2finger = True
            state.first_time_multifinger = True
            state.is_left_held = False
            
            state.active_gesture = None
            state.mf_start_centroid = None
            state.mf_start_spread = 0.0
        
        
        # --- PRESS HANDLING ---
        if state.active_gesture is None and hold_duration >= config.press_hold_duration and not state.was_moved:
            # hold_duration = current_time - state.start_time[num_active]
            if num_active == 1 and config.feature_toggles.get("1f_press", False):
                if (not state.is_left_held 
                        and state.max_drift <= config.tap_max_movement
                        and not state.scroll_active
                        ):
                    ad.dispatch_gesture_event("1f_press")
                    if DEBUG: print("[Gesture] 1F Press & Hold Drag Initiated")
                    
            elif num_active >= 2 and config.feature_toggles.get(f"{num_active}f_press", False):    
                if (hold_duration >= config.press_hold_duration 
                        and dist_centroid <= config.tap_max_movement
                        and abs(delta_spread_total) <= config.pinch_discrete_threshold
                        ):
                    if DEBUG: print(f"[Gesture] Press hold for {num_active} Fingers triggered")
                    gesture_name = f"{num_active}f_press"
                    ad.dispatch_gesture_event(gesture_name)                    
        
    state.last_num_active = num_active
# ==============================================================================
# INTERFACE MANAGER
# ==============================================================================
#

# class InterfaceManager():
    
#     # ==============================================================================
#     # HID HARDWARE CONSTANTS
#     # ==============================================================================
#     VENDOR_ID = 0x056A  # Wacom Co., Ltd.
#     PRODUCT_ID = [132, 791, 789, 788]   
    
#     def __init__(self, prefer_wireless):
#         self.prefer_wireless = prefer_wireless
#         self.interface_groups = []
#         self.used_interfaces = []
#         self.last_update = time.time() - 60
    
#     def update_interfaces(self):
#         current_time = time.time()
#         if (current_time - self.last_update) > 10:
#             self.last_update = current_time
#             self.interface_groups = self.fetch_interfaces()
#             if self.interface_groups is None:
#                 print("[Interfaces] No Valid Interfaces Found")
#                 return 1
            
#             if self.prefer_wireless:
#                 self.interface_groups.sort(key=lambda group: group['product_string'], reverse=True)
#             else: 
#                 self.interface_groups.sort(key=lambda group: group['product_string'])
            
#             self.used_interfaces = []
#             for i in self.interface_groups:
#                 self.used_interfaces.append([False, False])
            
#             print(f"[Interfaces] Updated Interfaces - found: {len(self.interface_groups)}")
#             for group in self.interface_groups:
#                 print(f"[Interfaces] Device available: {group['product_string']} {group['product_id']}")
#             print(f"[Interfaces] =============================================")
#             return 0
#         else:
#             #print("[Interfaces] Already updated in last 10 seconds")
#             return 0


#     def fetch_interfaces(self):
#         self.interface_groups = []
#         pen_device, touch_device = None, None
#         for dev in hid.enumerate():
#             fetched_vendor_id = dev['vendor_id'] 
#             fetched_product_string = dev['product_string'] 
#                 # 'product_string': 'Wacom Wireless Receiver' 'product_id': 132
#                 # 'product_string': 'Intuos5 touch L' 'product_id': 791
#                 # 'product_string': 'Intuos5 touch M' 'product_id': 789
#                 # 'product_string': 'Intuos5 touch S' 'product_id': 788
#             fetched_product_id = dev['product_id']
            
#             vendor_match = dev['vendor_id'] == self.VENDOR_ID
#             product_match = fetched_product_id in self.PRODUCT_ID
#             if vendor_match and product_match:
#                 interface_num = dev.get('interface_number', -1)
#                 wireless = fetched_product_string == 'Wacom Wireless Receiver'
#                 if interface_num == (1 if wireless else 0):
#                     pen_device = dev
#                 elif interface_num == (2 if wireless else 1):
#                     touch_device = dev
    
#             if pen_device != None and touch_device != None:
#                 # interface_groups.append([fetched_product_id, fetched_product_string, pen_device, touch_device])
#                 self.interface_groups.append({
#                     'product_id': fetched_product_id, 
#                     'product_string': fetched_product_string, 
#                     'pen_device': pen_device, 
#                     'touch_device': touch_device})
#                 pen_device, touch_device = None, None

#         if len(self.interface_groups) == 0:       
#             return None
#         return self.interface_groups

#     def run_interface(self, type, number, state, action):               
#         global running
        
#         header = f"[{number}: {type} Interface]"
#         if type == "Touch":
#             touch = True
#             device_type = 'touch_device'
#             place = 1
            
#         elif type == "Pen":
#             touch = False
#             device_type = 'pen_device'
#             place = 0
            
#         try:  
#             self.used_interfaces[number][place] = number
                
#             device_info = self.interface_groups[number][device_type]
#             dev = hid.device()

#             oserror_count = 0
#             pause_messaged = False
            
#             while running:
#                 if state.touch_paused:
#                     time.sleep(1)
#                     continue
#                 try:
#                     dev.open_path(device_info['path'])
#                     dev.set_nonblocking(False)
#                     print(f"{header}[Thread Started] on Interface ({device_info.get('interface_number', 0)})")
                    
#                     while running:
#                         # Touch Input
#                         self.used_interfaces[number][place] = number
#                         if touch:
#                             report = dev.read(66)
#                             if state.touch_paused:
#                                 if not pause_messaged:
#                                     print("{header}[Touch Paused] Skipping touch processing...")
#                                     pause_messaged = True
#                                     # Instantly clear active touch references on pause
#                                 if state.active_contacts:
#                                     state.active_contacts.clear()
#                                 time.sleep(0.5)
#                             else:
#                                 if pause_messaged : pause_messaged = False
#                                 process_touch_report(report, state, action)
                        
#                         # Pen Input
#                         else:
#                             if state.touch_paused:
#                                 time.sleep(0.5)
#                                 continue
#                             report = dev.read(2)
#                             if report:
#                                 parse_pen_packet(report, state)
#                         if oserror_count != 0: oserror_count = 0
#                 except OSError:
#                     if oserror_count == 0:
#                         print(f"{header}[Touch Error] read error")
#                     oserror_count += 1
#                     pass 
#                 except Exception as e:
#                     print(f"{header}[Touch Error] {e}")
#                     oserror_count += 1
                    
#                     if DEBUG: raise e
#                 finally:
#                     dev.close()
                
#                 if oserror_count > 30:
#                     oserror_count = 0
#                     print(f"{header} failed to reconnect to Interface ({device_info.get('interface_number', 0)}) \n --> [{type} Interface] {type} input now disabled. \n [Info] Reenable via context menu to try again. ")
#                     state.touch_paused = True
#                 else:
#                     time.sleep(5)
#                     code = self.update_interfaces()
#                     time.sleep(0.5+number) # to stagger the different threads and thus to prevent crossing of pads in interfaces
#                     if code == 1:
#                         print(f"[{type} Thread {number}] Ending Thread")
#                         running = False
#                         sys.exit("[Interfaces] No Interfaces found")
#                         break
#                     device = None                        
#                     for i, group in enumerate(self.interface_groups):
#                         #print(type, self.used_interfaces)
                        
#                         if self.used_interfaces[i][place] is False:
#                             device = group[device_type]
#                             self.used_interfaces[i][place] = number
#                             #print(type, self.used_interfaces)
#                             break
#                     if device is not None:
#                         device_info = device
#                         print(f"{header} new Interface detected: {device_info.get('manufacturer_string')} {device_info.get('product_string')}")
#                     else:
#                         print(f"[{number} {type} Thread ] All Interfaces already in use -  closing {type} Thread {number}")
#                         # running = False
#                         # sys.exit("[Interfaces] All Interfaces in use")
#                         break

#                     print(f"{header} trying to reconnect to Interface ({device_info.get('interface_number', 0)}) Attempt:{oserror_count}")
#         except IndexError as ie:
#             print(f"{header} More Threads than Devices found - closing Thread")
            


# def main():
#     prefer_wireless = False
#     connect_all = False
#     for arg in sys.argv[1:]:
#         if "-DEBUG" in arg or "-debug" in arg or "-Debug" in arg:
#             global DEBUG
#             DEBUG = True
#             print("[Start Paramater] DEBUG active")
#         elif "-wireless" in arg:
#             prefer_wireless = True
#             print("[Start Paramater] Wireless preference active")
#         elif "-connectall" in arg:
#             connect_all = True
#             print("[Start Paramater] Connect all active")
#         else:
#             print(f"[Start Paramater] Unknown parameter: {arg}")
    
#     print("[Interfaces] Enumerating HID devices for Wacom Tablet...")
    

#     im = InterfaceManager(prefer_wireless)
    
#     code = im.update_interfaces()
#     if code == 1:
#         print("[Interfaces] Ending Program")
#         sys.exit("[Interfaces] No Interfaces found")
    
#     # print(interface_groups)
               
#     for number, group in enumerate(im.interface_groups if connect_all else im.interface_groups[0:1]):
        
#         tablet_state = TabletState()
#         action_dispatch = ActionDispatcher(tablet_state)
        
#         pen_thread = threading.Thread(target=im.run_interface, args=("Pen", number, tablet_state, action_dispatch,), daemon=True)
#         touch_thread = threading.Thread(target=im.run_interface, args=("Touch", number, tablet_state, action_dispatch,), daemon=True)
        
#         print(f"[Interfaces] Connecting to Device: {group['product_string']} {group['product_id']}")
#         print(f"[Interfaces] Connecting via: {"USB Cable" if group['product_string'] != 'Wacom Wireless Receiver' else "Wireless Receiver"}")
        
#         pen_thread.start()
#         touch_thread.start()  
        
#         time.sleep(0.2) # Allow threads to initialize

    
#     config.set_available_actions(list(action_dispatch.ACTION_DISPATCH.keys()))
#     print("\n=== Wacom Touch Driver Running ===")

#     try:
#         from gui import launch_gui
#         launch_gui(tablet_state, config)
#     except SystemExit:
#         pass
#     finally:
#         print("\n=== Wacom Touch Driver successfully stopped ===")

# if __name__ == '__main__':
#     main()
    


# ==============================================================================
# SIMPLIFIED INTERFACE WORKER THREAD
# ==============================================================================

def run_interface(interface_type, device_info, state, action_dispatch):
    """
    Simplified worker thread for either 'Pen' or 'Touch' input.
    Exits as soon as an OSError/Exception is thrown or state.touch_paused is set.
    """
    global running
    interface_num = device_info.get('interface_number', -1)
    path = device_info.get('path')
    header = f"[{interface_type} Thread | Interface: {interface_num}]"

    dev = hid.device()

    try:
        dev.open_path(path)
        dev.set_nonblocking(False)
        print(f"{header} Started successfully.")

        while running:
            # Check pause state requirement
            if state.touch_paused:
                print(f"{header} Interrupted by touch_paused state. Exiting interface thread.")
                if interface_type == "Touch":
                    state.active_contacts.clear()
                break

            # Read and process packets according to input type
            if interface_type == "Touch":
                report = dev.read(66)
                if report:
                    process_touch_report(report, state, action_dispatch)
            elif interface_type == "Pen":
                report = dev.read(2)
                if report:
                    parse_pen_packet(report, state)

    except OSError as e:
        print(f"{header} Read Error (Disconnected/OSError): {e}")
    except Exception as e:
        print(f"{header} Unexpected Error: {e}")
        if DEBUG:
            raise e
    finally:
        try:
            dev.close()
        except Exception:
            pass
        print(f"{header} Interface thread closed.")


# ==============================================================================
# MASTER DEVICE MANAGEMENT THREAD
# ==============================================================================

class DeviceManagerThread(threading.Thread):
    VENDOR_ID = 0x056A  # Wacom Co., Ltd.
    PRODUCT_IDS = [132, 791, 789, 788]

    def __init__(self, prefer_wireless=False, poll_interval=3.0):
        super().__init__(daemon=True)
        self.prefer_wireless = prefer_wireless
        self.poll_interval = poll_interval
        self.lock = threading.Lock()
        
        # State tracking: path -> info dict
        # Structure per connected pair: 
        # {
        #   'device_path': str (key),
        #   'product_id': int,
        #   'product_string': str,
        #   'state': TabletState,
        #   'action': ActionDispatcher,
        #   'threads': {'Pen': Thread, 'Touch': Thread}
        # }
        self.active_connections = {}

    def run(self):
        global running
        print("[Manager Thread] Started Master Management Thread.")
        
        while running:
            self.manage_connections()
            time.sleep(self.poll_interval)

        print("[Manager Thread] Master Management Thread stopped.")



    def get_device_parent_id(self, path_bytes):
        """Extracts base hardware instance ID by stripping interface (MI_xx) suffixes."""
        path_str = path_bytes.decode('utf-8', errors='ignore')
        return re.sub(r'&MI_\d+.*$', '', path_str)

    def enumerate_wacom_devices(self):
        # Store lists of interfaces per parent ID to support multiple identical tablets
        found_pen = {}    # parent_id -> list of pen dev dicts
        found_touch = {}  # parent_id -> list of touch dev dicts

        for dev in hid.enumerate():
            v_id = dev.get('vendor_id')
            p_id = dev.get('product_id')
            p_str = dev.get('product_string', '')

            if v_id == self.VENDOR_ID and p_id in self.PRODUCT_IDS:
                interface_num = dev.get('interface_number', -1)
                wireless = (p_str == 'Wacom Wireless Receiver')
                
                pen_iface = 1 if wireless else 0
                touch_iface = 2 if wireless else 1

                parent_id = self.get_device_parent_id(dev['path'])

                if interface_num == pen_iface:
                    found_pen.setdefault(parent_id, []).append(dev)
                elif interface_num == touch_iface:
                    found_touch.setdefault(parent_id, []).append(dev)

        paired_devices = []

        # Pair pen & touch interfaces index-by-index for each unique physical device
        for parent_id, pen_list in found_pen.items():
            if parent_id in found_touch:
                touch_list = found_touch[parent_id]
                
                # Pair corresponding interface instances (handles duplicate devices gracefully)
                for idx, pen_dev in enumerate(pen_list):
                    if idx < len(touch_list):
                        touch_dev = touch_list[idx]
                        
                        # Create a truly unique device key using interface paths
                        unique_dev_id = f"{parent_id}_instance_{idx}"

                        paired_devices.append({
                            'id': unique_dev_id,
                            'product_id': pen_dev['product_id'],
                            'product_string': pen_dev['product_string'],
                            'pen_device': pen_dev,
                            'touch_device': touch_dev
                        })

        return paired_devices

    def manage_connections(self):
        with self.lock:
            # 1. Clean up stale or exited threads
            dead_keys = []
            for dev_id, conn in self.active_connections.items():
                pen_alive = conn['threads']['Pen'].is_alive()
                touch_alive = conn['threads']['Touch'].is_alive()

                # If either thread died (e.g. from OSError or paused), clean up state
                if not pen_alive or not touch_alive:
                    print(f"[Manager] Device disconnected/stopped: {conn['product_string']} ({dev_id})")
                    dead_keys.append(dev_id)

            for dev_id in dead_keys:
                del self.active_connections[dev_id]

            # 2. Update available devices and spawn new interface threads
            available_devices = self.enumerate_wacom_devices()

            for dev_pair in available_devices:
                dev_id = dev_pair['id']

                if dev_id not in self.active_connections:
                    print(f"\n[Manager] New Device Detected! Initializing: {dev_pair['product_string']}")
                    
                    # Create unique state & dispatcher context per device
                    t_state = TabletState()
                    a_dispatch = ActionDispatcher(t_state)

                    pen_dev = dev_pair['pen_device']
                    touch_dev = dev_pair['touch_device']

                    t_pen = threading.Thread(
                        target=run_interface,
                        args=("Pen", pen_dev, t_state, a_dispatch),
                        daemon=True
                    )
                    t_touch = threading.Thread(
                        target=run_interface,
                        args=("Touch", touch_dev, t_state, a_dispatch),
                        daemon=True
                    )

                    self.active_connections[dev_id] = {
                        'product_id': dev_pair['product_id'],
                        'product_string': dev_pair['product_string'],
                        'state': t_state,
                        'action': a_dispatch,
                        'threads': {
                            'Pen': t_pen,
                            'Touch': t_touch
                        }
                    }

                    t_pen.start()
                    t_touch.start()
                    print(f"[Manager] Spawned Pen (Interface: {pen_dev.get('interface_number')}) "
                          f"& Touch (Interface: {touch_dev.get('interface_number')}) threads for {dev_id}")

    def get_primary_state_and_action(self):
        """Helper to pass active state and dispatcher references to the GUI."""
        with self.lock:
            if self.active_connections:
                first_conn = next(iter(self.active_connections.values()))
                return first_conn['state'], first_conn['action']
            return None, None


# ==============================================================================
# MAIN ENTRY POINT
# ==============================================================================

def main():
    global DEBUG, running
    prefer_wireless = False

    for arg in sys.argv[1:]:
        if "-DEBUG" in arg.upper():
            DEBUG = True
            print("[Start Parameter] DEBUG active")
        elif "-wireless" in arg:
            prefer_wireless = True
            print("[Start Parameter] Wireless preference active")

    print("\n=== Initializing Wacom Device Manager Thread ===")

    # Initialize and start the master management thread
    manager = DeviceManagerThread(prefer_wireless=prefer_wireless, poll_interval=5.0)
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
        launch_gui(state, config)
    except SystemExit:
        pass
    except ImportError:
        # Fallback loop if GUI isn't installed in the environment
        try:
            while running:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
    finally:
        running = False
        print("\n=== Wacom Touch Driver successfully stopped ===")

if __name__ == '__main__':
    main()