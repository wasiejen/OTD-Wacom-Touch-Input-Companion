import hid
import time
import math
import threading
from pynput.mouse import Button, Controller as MouseController
from pynput.keyboard import Key, Controller as KeyboardController

import json
import os

CONFIG_FILE_PATH = "user.cfg"

# DEBUG = False 
DEBUG = True  

# ==============================================================================
# CONFIGURATION CLASS
# ==============================================================================
class DriverConfig:
    def __init__(self):
        
        # Motion Parameters
        self.cursor_deadzone = 0.0           # Distance (in px/packet) to discard as noise/jitter
        
        # Piecewise Linear Acceleration Settings
        self.cursor_min_sens = 0.4          # Flat base multiplier for precision work
        self.cursor_max_sens = 2.5           # Hard cap multiplier (e.g., 2.5x to 3.0x base speed)
        self.speed_low = 5.0                 # Upper bound of flat precision zone (px/packet)
        self.speed_high = 50.0               # Speed at which max acceleration ceiling is reached
        
        # Scroll Sensitivity (Positive = Traditional, Negative = Natural)
        self.scroll_sensitivity_x = 0.015    # Positive = Natural, Negative = Traditional
        self.scroll_sensitivity_y = 0.03     # Natural Vertical Scrolling
        
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
        self.pinch_continuous_sensitivity = 10.0 # Pixels per continuous zoom step
        self.pinch_discrete_threshold = 50.0    # Distance change needed for 3F/4F pinch trigger
        
        # 5-Finger Alt-Tab Configuration
        self.alt_tab_activation_threshold = 40.0  # px distance to initiate Alt-Tab overlay
        self.alt_tab_step_threshold = 30.0        # px distance per window switch step 
        self.alt_tab_step_sensitivity = 0.2         # packets needed to move one step
        
        # Available Feature Toggles (True = Enabled, False = Disabled)        
        self.feature_toggles = {
            "cursor_acceleration": True,
            "scrolling_acceleration": True,
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
                "pinch_continuous_sensitivity": self.pinch_continuous_sensitivity,
                "pinch_discrete_threshold": self.pinch_discrete_threshold,
                "scroll_activation_threshold": self.scroll_activation_threshold,
                "pinch_activation_threshold": self.pinch_activation_threshold,
                "alt_tab_activation_threshold": self.alt_tab_activation_threshold,
                "alt_tab_step_threshold": self.alt_tab_step_threshold,
                "alt_tab_step_sensitivity": self.alt_tab_step_sensitivity,
                "cursor_deadzone": self.cursor_deadzone,
                "cursor_min_sens": self.cursor_min_sens,
                "cursor_max_sens": self.cursor_max_sens,
                "speed_low": self.speed_low,
                "speed_high": self.speed_high,
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
        self.mouse = MouseController()
        self.keyboard = KeyboardController()
        self.pen_in_proximity = False
        
        # Active Contacts: slot_id -> (x, y)
        self.active_contacts = {}
        
        # Touch Session Metadata
        self.session_start_time = None
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

state = TabletState()

# ==============================================================================
# ACTION DISPATCHER
# ==============================================================================
def execute_mapped_action(action_name):
    if DEBUG: print(f"[Gesture] Action triggered: {action_name}")
        
    if action_name is None:
        print("[Action] No action mapped for this gesture.")
    elif action_name == "left_click":
        state.mouse.click(Button.left, 1)
    elif action_name == "left_hold":
        state.mouse.press(Button.left)
        state.is_left_held = True
        state.is_dragging_cursor = True
        state.active_gesture = None
    elif action_name == "left_hold_release":
        state.mouse.release(Button.left)
        state.is_left_held = False
        state.active_gesture = None
    elif action_name == "right_click":
        state.mouse.click(Button.right, 1)
    elif action_name == "middle_click":
        state.mouse.click(Button.middle, 1)
    elif action_name == "task_view":
        with state.keyboard.pressed(Key.cmd):
            state.keyboard.tap(Key.tab)
    elif action_name == "show_desktop":
        with state.keyboard.pressed(Key.cmd):
            state.keyboard.tap('d')
    elif action_name == "window_up":
        with state.keyboard.pressed(Key.cmd):
            state.keyboard.tap(Key.up)
    elif action_name == "window_down":
        with state.keyboard.pressed(Key.cmd):
            state.keyboard.tap(Key.down)
    elif action_name == "window_left":
        with state.keyboard.pressed(Key.cmd):
            state.keyboard.tap(Key.left)
    elif action_name == "window_right":
        with state.keyboard.pressed(Key.cmd):
            state.keyboard.tap(Key.right)
    elif action_name == "window_maximize":
        with state.keyboard.pressed(Key.cmd):
            state.keyboard.tap(Key.up)
    elif action_name == "window_minimize":
        with state.keyboard.pressed(Key.cmd):
            state.keyboard.tap(Key.down)
    elif action_name == "desktop_left":
        with state.keyboard.pressed(Key.cmd), state.keyboard.pressed(Key.ctrl):
            state.keyboard.tap(Key.left)
    elif action_name == "desktop_right":
        with state.keyboard.pressed(Key.cmd), state.keyboard.pressed(Key.ctrl):
            state.keyboard.tap(Key.right)
    elif action_name == "next_window":
        with state.keyboard.pressed(Key.alt):
            state.keyboard.tap(Key.tab)
    elif action_name == "prev_window":
        with state.keyboard.pressed(Key.alt), state.keyboard.pressed(Key.shift):
            state.keyboard.tap(Key.tab)
    elif action_name == "next":
        with state.keyboard.pressed(Key.alt):
            state.keyboard.tap(Key.right)
    elif action_name == "prev":
        with state.keyboard.pressed(Key.alt):
            state.keyboard.tap(Key.left)
    elif action_name == "undo":
        with state.keyboard.pressed(Key.ctrl):
            state.keyboard.tap("z")
    elif action_name == "redo":
        with state.keyboard.pressed(Key.ctrl):
            state.keyboard.tap("y")
    elif action_name == "ctrl_alt_tab_initiate":
        with state.keyboard.pressed(Key.ctrl), state.keyboard.pressed(Key.alt):
            state.keyboard.tap(Key.tab)
    elif action_name == "ctrl_alt_tab_next":
        state.keyboard.tap(Key.right)
    elif action_name == "ctrl_alt_tab_prev":
        state.keyboard.tap(Key.left)
    elif action_name == "ctrl_alt_tab_commit":
        state.keyboard.tap(Key.enter)

def dispatch_gesture_event(event_type):
    if config.feature_toggles.get(event_type, False):
        mapping = config.action_mapping.get(event_type, None)
        if callable(mapping):
            action_name = mapping(state)
        else:
            action_name = mapping
        
        state.active_gesture = event_type
        execute_mapped_action(action_name)

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

    if config.feature_toggles.get(feature_toggle, False):
        speed = math.hypot(dx, dy)

        if speed < config.cursor_deadzone:
            return 0.0, 0.0

        if speed <= config.speed_low:
            gain = config.cursor_min_sens
        elif speed >= config.speed_high:
            gain = config.cursor_max_sens
        else:
            slope = (config.cursor_max_sens - config.cursor_min_sens) / (config.speed_high - config.speed_low)
            gain = config.cursor_min_sens + slope * (speed - config.speed_low)

        return dx * gain, dy * gain
    else:
        return dx * config.cursor_min_sens, dy * config.cursor_min_sens
        

# ==============================================================================
# PARSING & TOUCH PROCESSING
# ==============================================================================
def parse_pen_packet(report):
    if not report or report[0] != 0x02:
        return
    pen_status = report[1]
    if pen_status in (0x20, 0xE0, 0xE1, 0xE2):
        if not state.pen_in_proximity:
            state.pen_in_proximity = True
            print("[Palm Rejection] Pen detected -> Touch Disabled")
    elif pen_status == 0x80:
        if state.pen_in_proximity:
            state.pen_in_proximity = False
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

def process_touch_report(report):
    if state.pen_in_proximity or state.touch_paused:
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
    
    if state.session_start_time is not None:
        session_duration = current_time - state.session_start_time
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
                            state.mouse.move(move_x, move_y)
                            state.cursor_acc_x -= move_x
                            state.cursor_acc_y -= move_y

                if (current_time - state.last_f1_tap_time) < config.double_tap_timeout and state.max_drift <= config.tap_max_movement:
                    state.is_dragging_cursor = True
                    
                    if (config.feature_toggles.get("1f_double_tap", False) 
                            and state.drag_candidate 
                            and not state.is_left_held
                        ):
                        dispatch_gesture_event("1f_double_tap")
                        #execute_mapped_action("left_hold")
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
                state.scroll_active = True
            elif dist_centroid >= config.scroll_activation_threshold:
                state.active_gesture = "2F_SCROLL"
                state.scroll_active = True

        if state.active_gesture == "2F_SCROLL" and config.feature_toggles.get("2f_scroll", False):
            if isinstance(state.last_2finger_centroid, tuple):
                dx = cx - state.last_2finger_centroid[0]
                dy = cy - state.last_2finger_centroid[1]
                # print(f"dx,dy: {dx,dy}")    

                scaled_dx, scaled_dy = calculate_piecewise_accelerated_delta(dx, dy, config, "scrolling_acceleration")
                
                step_x = scaled_dx * -config.scroll_sensitivity_x
                step_y = scaled_dy * config.scroll_sensitivity_y
                
                if step_x != 0 or step_y != 0:
                    # print(f"step_x,step_y: {step_x,step_y}")  
                    state.mouse.scroll(step_x, step_y)

            state.last_2finger_centroid = (cx, cy)

        elif state.active_gesture == "2F_PINCH" and config.feature_toggles.get("2f_pinch", False):
            delta_pinch = spread - state.last_pinch_distance
            if abs(delta_pinch) >= config.pinch_continuous_sensitivity:
                steps = delta_pinch / config.pinch_continuous_sensitivity
                if steps != 0:
                    with state.keyboard.pressed(Key.ctrl):
                        state.mouse.scroll(0, steps)
                    state.last_pinch_distance += delta_pinch     
               

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
              
        if state.active_gesture is None and session_duration <= config.max_gesture_touch_session_duration:
            if num_active >= 5 and config.feature_toggles.get("5f_alt_tab", False):
                state.active_gesture = "5F_ALT_TAB"
                state.alt_tab_active = True
                state.last_alt_tab_x = cx
                execute_mapped_action("ctrl_alt_tab_initiate")

            else:
                prefix = f"{num_active}f_"
                
                if abs(delta_spread_total) >= config.pinch_discrete_threshold:
                    gesture_name = prefix + ("pinch_out" if delta_spread_total > 0 else "pinch_in")
                    dispatch_gesture_event(gesture_name)

                elif abs(dx_total) >= config.swipe_threshold_x and abs(dx_total) > config.axis_dominance_ratio * abs(dy_total):
                    gesture_name = prefix + ("swipe_right" if dx_total > 0 else "swipe_left")
                    dispatch_gesture_event(gesture_name)

                elif abs(dy_total) >= config.swipe_threshold_y and abs(dy_total) > config.axis_dominance_ratio * abs(dx_total):
                    gesture_name = prefix + ("swipe_down" if dy_total > 0 else "swipe_up")
                    dispatch_gesture_event(gesture_name)
                    
        elif state.active_gesture == "5F_ALT_TAB" and num_active >= 5:
            dx_step = cx - state.last_alt_tab_x
            if abs(dx_step) >= config.alt_tab_step_threshold:
                if state.f5_counter >= 1 / config.alt_tab_step_sensitivity: # to slow down the windows selection change
                    state.f5_counter = 0
                    if dx_step > 0:
                        execute_mapped_action("ctrl_alt_tab_next")
                    else:
                        execute_mapped_action("ctrl_alt_tab_prev")
                    state.last_alt_tab_x = cx
                else:
                    state.f5_counter += 1
            else:
                state.f5_counter = 0 # to make sure it does not change to fast after holding still
        
    # --- TAPS + RESET / RELEASE ---
    elif num_active == 0:
        if state.alt_tab_active:
            execute_mapped_action("ctrl_alt_tab_commit")
            state.alt_tab_active = False
            state.last_alt_tab_x = 0
            
        elif state.is_left_held:
            execute_mapped_action("left_hold_release")

        elif state.session_start_time is not None and state.active_gesture is None:

            #if DEBUG: print(f"session_duration: {session_duration}, config.max_tap_duratio: {config.max_tap_duration}, sd<max_dur: {session_duration <= config.max_tap_duration}")
            if session_duration <= config.max_tap_duration and not state.scroll_active:
                if state.peak_contact_count == 1:
                    if not state.was_moved or not state.is_dragging_cursor:
                        dispatch_gesture_event("1f_tap")
                        state.last_f1_tap_time = current_time

                elif state.peak_contact_count == 2:
                    dispatch_gesture_event("2f_tap")

                elif state.peak_contact_count == 3:
                    dispatch_gesture_event("3f_tap")

                elif state.peak_contact_count == 4:
                    dispatch_gesture_event("4f_tap")

                elif state.peak_contact_count >= 5:
                    dispatch_gesture_event("5f_tap")
                    
        state.session_start_time = None
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
                dispatch_gesture_event("1f_press")
                if DEBUG: print("[Gesture] 1F Press & Hold Drag Initiated")
                
        elif num_active >= 2 and config.feature_toggles.get(f"{num_active}f_press", False):    
            if (hold_duration >= config.press_hold_duration 
                    and dist_centroid <= config.tap_max_movement
                    and abs(delta_spread_total) <= config.pinch_discrete_threshold
                    ):
                if DEBUG: print(f"[Gesture] Press hold for {num_active} Fingers triggered")
                gesture_name = f"{num_active}f_press"
                dispatch_gesture_event(gesture_name)                    
        
    state.last_num_active = num_active

# ==============================================================================
# THREAD LOOPS
# ==============================================================================
def run_pen_interface(device_info):
    dev = hid.device()
    oserror_count = 0
    
    while state.running:
        if state.touch_paused:
            time.sleep(1)
            continue
        try:
            dev.open_path(device_info['path'])
            dev.set_nonblocking(True)
            print(f"[Pen Interface][Thread Started] on Interface ({device_info.get('interface_number', 0)})")
            while state.running:
                if state.touch_paused:
                    time.sleep(0.5)
                    continue
                report = dev.read(2)
                if oserror_count != 0: oserror_count = 0
                if report:
                    parse_pen_packet(report)
                else:
                    time.sleep(0.1)
        except OSError:
            if oserror_count == 0:
                print(f"[Pen Interface][Pen Error] read error thrown")
            oserror_count += 1
            pass    
        
        except Exception as e:
            print(f"[Pen Interface][Pen Error] {e}")
        finally:
            dev.close()
            
        if oserror_count > 60:
            oserror_count = 0
            print(f"[Pen Interface] failed to reconnect to Interface ({device_info.get('interface_number', 0)}) \n --> [Pen Interface] Pen Hover Control now disabled. \n [Info] Reenable via context menu to try again. ")
            state.touch_paused = True
        else:
            time.sleep(5)
            print(f"[Pen Interface] trying to reconnect to Interface ({device_info.get('interface_number', 0)}) Attempt:{oserror_count}")

def contains_touch_down(report):
    return STATUS_TOUCH_DOWN in [report[3], report[10]] 

def run_touch_interface(device_info):
    dev = hid.device()
    oserror_count = 0
    pause_messaged = False
    
    while state.running:
        if state.touch_paused:
            time.sleep(1)
            continue
        try:
            dev.open_path(device_info['path'])
            dev.set_nonblocking(False)
            print(f"[Touch Interface][Thread Started] on Interface ({device_info.get('interface_number', 0)})")
            
            while state.running:
                report = dev.read(66)
                if state.touch_paused:
                    if not pause_messaged:
                        print("[Touch Interface][Touch Paused] Skipping touch processing...")
                        pause_messaged = True
                    # Instantly clear active touch references on pause
                    if state.active_contacts:
                        state.active_contacts.clear()
                    time.sleep(0.5)
                else:
                    if pause_messaged : pause_messaged = False
                    if report:
                        process_touch_report(report)
                    elif not report:
                        time.sleep(0.002)
        except OSError:
            if oserror_count == 0:
                print(f"[Toucn Interface][Touch Error] read error thrown")
            oserror_count += 1
            pass 
        except Exception as e:
            print(f"[Touch Interface][Touch Error] {e}")
            oserror_count += 1
            if DEBUG: raise e
        finally:
            dev.close()
        
        if oserror_count > 60:
            oserror_count = 0
            print(f"[Touch Interface] failed to reconnect to Interface ({device_info.get('interface_number', 0)}) \n --> [Touch Interface] Touch input now disabled. \n [Info] Reenable via context menu to try again. ")
            state.touch_paused = True
        else:
            time.sleep(5)
            print(f"[Touch Interface] trying to reconnect to Interface ({device_info.get('interface_number', 0)}) Attempt:{oserror_count}")

def main():
    print("Enumerating HID devices for Wacom Tablet...")
    pen_device, touch_device = None, None

    for dev in hid.enumerate():
        vendor_match = (VENDOR_ID is None) or (dev['vendor_id'] == VENDOR_ID)
        product_match = (PRODUCT_ID is None) or (dev['product_id'] == PRODUCT_ID)

        if vendor_match and product_match:
            interface_num = dev.get('interface_number', -1)
            if interface_num == 0 and pen_device is None:
                pen_device = dev
            elif interface_num == 1 and touch_device is None:
                touch_device = dev

    if not pen_device or not touch_device:
        print("Error: Could not locate both Pen (Int 0) and Touch (Int 1) interfaces.")
        return

    pen_thread = threading.Thread(target=run_pen_interface, args=(pen_device,), daemon=True)
    touch_thread = threading.Thread(target=run_touch_interface, args=(touch_device,), daemon=True)

    pen_thread.start()
    touch_thread.start()
    time.sleep(0.2) # Allow threads to initialize
    print("\n=== Wacom Touch Driver Running ===")

    try:
        from gui import launch_gui
        launch_gui(state, config)
    except SystemExit:
        pass
    finally:
        print("\n=== Wacom Touch Driver successfully stopped ===")

if __name__ == '__main__':
    main()