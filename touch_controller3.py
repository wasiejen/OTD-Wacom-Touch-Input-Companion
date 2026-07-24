import hid
import time
import math
import threading
from pynput.mouse import Button, Controller as MouseController
from pynput.keyboard import Key, Controller as KeyboardController

# ==============================================================================
# CONFIGURATION CLASS (Easy to expose to a future GUI)
# ==============================================================================
class DriverConfig:
    def __init__(self):
        # Motion Sensitivity
        self.cursor_sensitivity = 1.0
        self.scroll_sensitivity_x = -0.02   # Positive = Traditional, Negative = Natural
        self.scroll_sensitivity_y = 0.03  # Natural Vertical Scrolling
        
        # Gesture Thresholds
        self.max_tap_duration = 0.300       # Seconds
        self.double_tap_timeout = 0.200     # Seconds for 1F drag
        self.tap_max_movement = 30          # Coordinate drift limit
        
        # Feature Toggles
        self.enable_1f_tap = True
        self.enable_1f_double_tap_drag = True
        self.enable_2f_simultaneous_tap = True
        self.enable_3f_tap = True
        self.enable_4f_tap = True
        self.enable_2f_scroll = True
        
        # 1-Finger Press-and-Hold Drag Config
        self.enable_1f_press_drag = True
        self.press_hold_duration = 0.250    # Seconds required to convert static touch into left-click hold
        
        # Mappable Actions: 'left_click', 'right_click', 'middle_click', 'task_view'
        self.actions = {
            "left_click": "left_click",
            "right_click": "right_click",
            "middle_click": "middle_click",
            "2f_tap": "right_click",
            "3f_tap": "middle_click",
            "4f_tap": "task_view"
        }

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
        self.mouse = MouseController()
        self.keyboard = KeyboardController()
        self.pen_in_proximity = False
        
        # Active Contacts: slot_id -> (x, y)
        self.active_contacts = {}
        
        # Touch Session Metadata
        self.session_start_time = None
        self.peak_contact_count = 0
        
        # 1-Finger Tracking
        self.f1_start_time = None
        self.f1_start_pos = None
        self.last_f1_pos = None
        self.max_drift = 0.0
        self.is_dragging_cursor = False
        self.last_f1_release_time = 0.0
        
        # 1-Finger Double-Tap Drag
        self.drag_candidate = False
        self.is_left_held = False
        
        # Asymmetric & Multi-Finger Tracking
        self.f2_start_time = None
        self.f2_start_pos = None
        self.f2_right_click_fired = False
        self.scroll_active = False
        self.last_2finger_centroid = None
        self.scroll_acc_x = 0.0
        self.scroll_acc_y = 0.0
        self.was_dualtouch = False
        self.was_multitouch = False
        self.was_moved = False
        self.press_hold_fired = False
           

state = TabletState()

# ==============================================================================
# ACTION DISPATCHER
# ==============================================================================
def execute_mapped_action(action_key):
    action = config.actions.get(action_key)
    if not action:
        return

    if action == "left_click":
        state.mouse.click(Button.left, 1)
    elif action == "right_click":
        state.mouse.click(Button.right, 1)
    elif action == "middle_click":
        state.mouse.click(Button.middle, 1)
    elif action == "task_view":
        # Emulate Win/Super key press for Task View / Overview
        #state.keyboard.tap(Key.cmd)
        with state.keyboard.pressed(Key.cmd):
            state.keyboard.press(Key.tab)
            state.keyboard.release(Key.tab)


def dispatch_gesture_event(event_type):
    if event_type == "1f_tap" and config.enable_1f_tap:
        execute_mapped_action("left_click")

    # elif event_type == "asymmetric_f2_tap" and config.enable_asymmetric_f2_actions:
    #     execute_mapped_action("left_click")

    # elif event_type == "asymmetric_f2_hold" and config.enable_asymmetric_f2_actions:
    #     execute_mapped_action("right_click")

    elif event_type == "2f_tap" and config.enable_2f_simultaneous_tap:
        execute_mapped_action("2f_tap")

    elif event_type == "3f_tap" and config.enable_3f_tap:
        execute_mapped_action("3f_tap")

    elif event_type == "4f_tap" and config.enable_4f_tap:
        execute_mapped_action("4f_tap")

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


def process_touch_batch(reports):
    
    if state.pen_in_proximity:
        return

    
    current_time = time.time()
    
    for report in reports:
        if len(report) < 10 or report[0] != 0x02:
            continue
        num_reports = report[1]
        parsed_blocks = []
        
        for i in range(num_reports):    
            b1 = parse_sub_block(report[2+(i*8):10+(i*8)]) # report has 66 Bytes, each sub-block is 8 bytes long and 7 max sub-blocks seen until now (might need to be increased for 10 fingers - but who uses that many fingers on a drawing tablet anyway)
            if b1: parsed_blocks.append(b1)
            
        for slot_id, status, pos in parsed_blocks:
            if slot_id == 'SYNC':
                if status == 0:
                    state.active_contacts.clear()
                continue

            if status == STATUS_TOUCH_DOWN:
                state.active_contacts[slot_id] = pos
                
                # Start of a touch session
                if len(state.active_contacts) == 1:
                    state.session_start_time = current_time
                    state.peak_contact_count = 1
                    state.f1_start_time = current_time
                    state.f1_start_pos = pos
                    state.last_f1_pos = pos
                    state.max_drift = 0.0
                    state.is_dragging_cursor = False
                    
                    # Check for 1-Finger Double-Tap Drag Candidate
                    time_since_last_tap = current_time - state.last_f1_release_time
                    state.drag_candidate = (time_since_last_tap <= config.double_tap_timeout)

                else:
                    # Track peak contact count for multi-finger tap detection
                    state.peak_contact_count = max(state.peak_contact_count, len(state.active_contacts))

                    if len(state.active_contacts) == 2:
                        state.f2_start_time = current_time
                        state.f2_start_pos = pos
                        state.f2_right_click_fired = False
                        state.scroll_active = False
                        state.press_hold_fired = False

            elif status in STATUS_ACTIVE_CONTACTS:
                state.active_contacts[slot_id] = pos
                        
            elif status in STATUS_RELEASE_TERMINAL:
                state.active_contacts.pop(slot_id, None)
                if state.max_drift > config.tap_max_movement:
                    state.was_moved = True

    num_active = len(state.active_contacts)
    
    #print(f"num_active={num_active}, active_contacts={state.active_contacts}, was_multitouch={state.was_multitouch}, was_dualtouch={state.was_dualtouch}")

    # ==========================================================================
    # GESTURE EVALUATION
    # ==========================================================================
    
    # --------------------------------------------------------------------------
    # 1-FINGER NAVIGATION & PRESS-AND-HOLD DRAG
    # --------------------------------------------------------------------------
    if num_active == 1:
        fid, pos = next(iter(state.active_contacts.items()))

        if state.was_multitouch or state.was_dualtouch:
            state.last_f1_pos = pos
            state.f1_start_pos = pos
            state.was_dualtouch = False
            state.is_dragging_cursor = True

        state.last_2finger_centroid = None
        state.f2_start_time = None

        if not state.was_multitouch:
            if state.f1_start_pos is not None and pos is not None:
                dx_start = pos[0] - state.f1_start_pos[0]
                dy_start = pos[1] - state.f1_start_pos[1]
                drift = math.hypot(dx_start, dy_start)

                if drift > state.max_drift:
                    state.max_drift = drift

                # === INJECTED: 1-Finger Press-and-Hold Drag Logic ===
                hold_duration = current_time - state.f1_start_time
                if (config.enable_1f_press_drag 
                        and not state.is_left_held 
                        and not state.press_hold_fired
                        and hold_duration >= config.press_hold_duration 
                        and state.max_drift <= config.tap_max_movement
                        and not state.scroll_active):
                    
                    state.mouse.press(Button.left)
                    state.is_left_held = True
                    state.press_hold_fired = True
                    state.is_dragging_cursor = True
                    print("[Gesture] 1F Press & Hold Drag Initiated")
                # ====================================================

                # Move cursor if enabled
                if state.is_dragging_cursor and state.last_f1_pos is not None:
                    dx = pos[0] - state.last_f1_pos[0]
                    dy = pos[1] - state.last_f1_pos[1]
                    if dx != 0 or dy != 0:
                        state.mouse.move(int(dx * config.cursor_sensitivity), int(dy * config.cursor_sensitivity))

                if state.max_drift > config.tap_max_movement:
                    state.is_dragging_cursor = True
                    
                    if config.enable_1f_double_tap_drag and state.drag_candidate and not state.is_left_held:
                        state.mouse.press(Button.left)
                        state.is_left_held = True
                        print("Debug: drag double tap initiated")

                state.last_f1_pos = pos

    # --------------------------------------------------------------------------
    # 2-FINGER ACTIONS & SCROLLING
    # --------------------------------------------------------------------------
    elif num_active == 2:

        state.was_dualtouch = True
        if not state.was_multitouch:
            
            state.is_dragging_cursor = True
            
            pts = list(state.active_contacts.values())
            p1, p2 = pts[0], pts[1]
            
            if p1 is not None and p2 is not None and config.enable_2f_scroll:
                centroid = ((p1[0] + p2[0]) / 2.0, (p1[1] + p2[1]) / 2.0)

                if isinstance(state.last_2finger_centroid, tuple) and len(state.last_2finger_centroid) == 2:
                    dx = centroid[0] - state.last_2finger_centroid[0]
                    dy = centroid[1] - state.last_2finger_centroid[1]
                    dist = math.hypot(dx, dy)

                    if dist > 15.0 or state.scroll_active:
                        state.scroll_active = True

                        state.scroll_acc_x += dx * config.scroll_sensitivity_x
                        state.scroll_acc_y += dy * config.scroll_sensitivity_y

                        step_x = int(state.scroll_acc_x)
                        step_y = int(state.scroll_acc_y)

                        if step_x != 0 or step_y != 0:
                            state.mouse.scroll(step_x, step_y)
                            state.scroll_acc_x -= step_x
                            state.scroll_acc_y -= step_y
                            #print ("Debug: scroll 2 finger")

                state.last_2finger_centroid = centroid

    # --------------------------------------------------------------------------
    # 3-FINGER OR MORE - BASIC SETTINGS
    # --------------------------------------------------------------------------

    elif num_active > 2:
        state.was_multitouch = True
        state.was_dualtouch = False
        state.centroid = None
        state.last_2finger_centroid = None
        state.scroll_active = False
        state.is_dragging_cursor = False

    # --------------------------------------------------------------------------
    # ALL FINGERS LIFTED -> TAP DISPATCH & CLEANUP
    # --------------------------------------------------------------------------
        
    elif num_active == 0:
        if state.is_left_held:
            state.mouse.release(Button.left)
            state.is_left_held = False

        if state.session_start_time is not None:
            session_duration = current_time - state.session_start_time
            
            #print(f"debug; session_duration={session_duration:.3f}s, peak_contacts={state.peak_contact_count}, is_dragging={state.is_dragging_cursor}, scroll_active={state.scroll_active}")
            
            # Evaluate simultaneous taps if no scrolling/dragging occurred
            if session_duration <= config.max_tap_duration and not state.scroll_active:
                
                if state.peak_contact_count == 1:
                    if not state.was_moved or not state.is_dragging_cursor:
                        dispatch_gesture_event("1f_tap")
                        state.last_f1_release_time = current_time
                        #print("Debug: 1F Tap Detected")
                        

                elif state.peak_contact_count == 2:
                    dispatch_gesture_event("2f_tap")

                elif state.peak_contact_count == 3:
                    dispatch_gesture_event("3f_tap")

                elif state.peak_contact_count >= 4:
                    dispatch_gesture_event("4f_tap")

        # Full reset
        state.session_start_time = None
        state.peak_contact_count = 0
        state.f1_start_time = None
        state.f1_start_pos = None
        state.last_f1_pos = None
        state.f2_start_time = None
        state.f2_start_pos = None
        state.is_dragging_cursor = False
        state.drag_candidate = False
        state.f2_right_click_fired = False
        state.scroll_active = False
        state.last_2finger_centroid = None
        state.was_dualtouch = False
        state.was_multitouch = False
        state.was_moved = False
        state.press_hold_fired = False

# ==============================================================================
# THREAD LOOPS & MAIN
# ==============================================================================
def run_pen_interface(device_info):
    dev = hid.device()
    try:
        dev.open_path(device_info['path'])
        dev.set_nonblocking(True)
        print(f"[Thread Started] Pen Interface ({device_info.get('interface_number', 0)})")
        while True:
            report = dev.read(2)
            if report:
                parse_pen_packet(report)
            else:
                time.sleep(0.1)
    except Exception as e:
        print(f"[Pen Error] {e}")
    finally:
        dev.close()


def contains_touch_down(report):
    # Quick check if report contains STATUS_TOUCH_DOWN (0xC0)
    return STATUS_TOUCH_DOWN in [report[3], report[10]] 

def run_touch_interface(device_info):
    dev = hid.device()
    try:
        dev.open_path(device_info['path'])
        dev.set_nonblocking(False)
        
        batch = []
        batch_start_time = None
        window_duration = 0.010  # Base 10ms

        while True:
            report = dev.read(66) # max 7 blocks of 8 bytes + 2 header bytes = 66
            current_time = time.time()

            if report:
                if not batch:
                    batch_start_time = current_time
                    window_duration = 0.005

                # Adaptive extension: extend window if a new finger lands mid-batch
                if contains_touch_down(report):
                    batch_start_time = time.time() 
                    window_duration = 0.020

                batch.append(report)

            if batch and (current_time - batch_start_time >= window_duration):
                process_touch_batch(batch)
                batch.clear()

    except Exception as e:
        print(f"[Touch Error] {e}")
    finally:
        dev.close()


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

    print("\n===  Wacom Touch Driver Running ===")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nShutting down driver...")

if __name__ == '__main__':
    main()