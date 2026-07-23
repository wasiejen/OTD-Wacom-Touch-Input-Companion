import hid
import time
import math
import threading
from pynput.mouse import Button, Controller

# ==============================================================================
# CONFIGURATION
# ==============================================================================
VENDOR_ID = 0x056A  # Wacom Co., Ltd.
PRODUCT_ID = None   # Auto-detect or set PID integer

# Motion Tuning
CURSOR_SENSITIVITY = 0.8
SCROLL_SENSITIVITY_X = -0.05   # Positive = Traditional, Negative = Natural
SCROLL_SENSITIVITY_Y = 0.05  # Natural Vertical Scrolling

# Gesture Thresholds
MAX_TAP_DURATION = 0.200       # Max duration (s) for a quick tap
DOUBLE_TAP_TIMEOUT = 0.250     # Max window (s) between taps for double-tap drag
HOLD_RIGHT_CLICK_TIME = 0.300  # Duration (s) Finger 2 must be held for Right Click
TAP_MAX_MOVEMENT = 30          # Coordinate drift limit before declaring motion

# Status Byte Constants (Byte 3 when Byte 2 != 0x81)
STATUS_TOUCH_DOWN = 0xC0
STATUS_RELEASE_TERMINAL = {0x20}
STATUS_ACTIVE_CONTACTS = {0x80, 0x90, 0x88, 0x98}

# ==============================================================================
# STATE TRACKER
# ==============================================================================
class TabletState:
    def __init__(self):
        self.mouse = Controller()
        self.pen_in_proximity = False
        
        # Active Contacts: slot_id -> (x, y)
        self.active_contacts = {}
        
        # 1-Finger State
        self.f1_start_time = None
        self.f1_start_pos = None
        self.last_f1_pos = None
        self.max_drift = 0.0
        self.is_dragging_cursor = False
        self.last_f1_release_time = 0.0
        
        # 1-Finger Double-Tap Drag State
        self.drag_candidate = False
        self.is_left_held = False
        
        # Asymmetric 2-Finger State
        self.f2_start_time = None
        self.f2_start_pos = None
        self.f2_right_click_fired = False
        self.scroll_active = False
        self.last_centroid = None
        self.scroll_acc_x = 0.0
        self.scroll_acc_y = 0.0
        
        # Transition & Safety Flags
        self.was_multitouch = False

state = TabletState()

# ==============================================================================
# PARSING LOGIC
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

    byte2 = block[0]
    byte3 = block[1]

    if byte2 == 0x81:
        return ('SYNC', byte3, None)

    slot_id = byte2
    if slot_id == 0:
        return None

    x = (block[2] << 4) | ((block[4] & 0xF0) >> 4)
    y = (block[3] << 4) | (block[4] & 0x0F)

    return (slot_id, byte3, (x, y))


def process_touch_frame(report):
    if state.pen_in_proximity or len(report) < 10 or report[0] != 0x02:
        return

    current_time = time.time()
    num_reports = report[1]

    parsed_blocks = []
    
    if num_reports >= 1 and len(report) >= 7:
        b1 = parse_sub_block(report[2:10])
        if b1: parsed_blocks.append(b1)

    if num_reports >= 2 and len(report) >= 15:
        b2 = parse_sub_block(report[10:18])
        if b2: parsed_blocks.append(b2)

    # Process packet blocks
    for slot_id, status, pos in parsed_blocks:
        
        if slot_id == 'SYNC':
            if status == 0:
                state.active_contacts.clear()
            continue

        # ----------------------------------------------------------------------
        # TOUCH DOWN (0xC0)
        # ----------------------------------------------------------------------
        if status == STATUS_TOUCH_DOWN:
            state.active_contacts[slot_id] = pos
            
            # First Finger Touch Down
            if len(state.active_contacts) == 1:
                state.f1_start_time = current_time
                state.f1_start_pos = pos
                state.last_f1_pos = pos
                state.max_drift = 0.0
                state.is_dragging_cursor = False
                
                # Check for 1-Finger Double-Tap Drag Candidate
                time_since_last_tap = current_time - state.last_f1_release_time
                if time_since_last_tap <= DOUBLE_TAP_TIMEOUT:
                    state.drag_candidate = True
                else:
                    state.drag_candidate = False

            # Second Finger Touch Down
            elif len(state.active_contacts) == 2:
                state.f2_start_time = current_time
                state.f2_start_pos = pos
                state.f2_right_click_fired = False
                state.scroll_active = False

        # ----------------------------------------------------------------------
        # ACTIVE CONTACT (0x80, 0x90, 0x88, 0x98)
        # ----------------------------------------------------------------------
        elif status in STATUS_ACTIVE_CONTACTS:
            state.active_contacts[slot_id] = pos

        # ----------------------------------------------------------------------
        # TERMINAL RELEASE (0x20)
        # ----------------------------------------------------------------------
        elif status in STATUS_RELEASE_TERMINAL:
            # Handle Finger 2 Tap Release Action BEFORE popping contact
            if len(state.active_contacts) == 2 and state.f2_start_time is not None:
                f2_duration = current_time - state.f2_start_time
                
                # If released quickly AND scroll was never engaged -> INSTANT LEFT CLICK
                if f2_duration <= MAX_TAP_DURATION and not state.scroll_active and not state.f2_right_click_fired:
                    state.mouse.click(Button.left, 1)

            state.active_contacts.pop(slot_id, None)

    num_active = len(state.active_contacts)

    # ==========================================================================
    # GESTURE EVALUATION
    # ==========================================================================
    
    # --------------------------------------------------------------------------
    # 1-FINGER NAVIGATION & DOUBLE-TAP DRAG
    # --------------------------------------------------------------------------
    if num_active == 1:
        fid, pos = next(iter(state.active_contacts.items()))

        # Transition smoothing off multi-touch
        if state.was_multitouch:
            state.last_f1_pos = pos
            state.f1_start_pos = pos
            state.was_multitouch = False
            state.is_dragging_cursor = True

        state.last_centroid = None  # Reset scroll centroid
        state.f2_start_time = None  # Reset F2 tracking

        if state.f1_start_pos is not None and pos is not None:
            dx_start = pos[0] - state.f1_start_pos[0]
            dy_start = pos[1] - state.f1_start_pos[1]
            drift = math.hypot(dx_start, dy_start)

            if drift > state.max_drift:
                state.max_drift = drift

            if state.max_drift > TAP_MAX_MOVEMENT:
                state.is_dragging_cursor = True
                
                # Engage Left Drag if this was a valid double-tap sequence
                if state.drag_candidate and not state.is_left_held:
                    state.mouse.press(Button.left)
                    state.is_left_held = True

            # Move Cursor
            if state.is_dragging_cursor and state.last_f1_pos is not None:
                dx = pos[0] - state.last_f1_pos[0]
                dy = pos[1] - state.last_f1_pos[1]
                if dx != 0 or dy != 0:
                    state.mouse.move(int(dx * CURSOR_SENSITIVITY), int(dy * CURSOR_SENSITIVITY))

            state.last_f1_pos = pos

    # --------------------------------------------------------------------------
    # 2-FINGER ASYMMETRIC ACTIONS & CENTROID SCROLLING
    # --------------------------------------------------------------------------
    elif num_active >= 2:
        state.was_multitouch = True
        state.is_dragging_cursor = True

        pts = list(state.active_contacts.values())
        p1, p2 = pts[0], pts[1]
        
        if p1 is not None and p2 is not None:
            centroid = ((p1[0] + p2[0]) / 2.0, (p1[1] + p2[1]) / 2.0)

            if isinstance(state.last_centroid, tuple) and len(state.last_centroid) == 2:
                dx = centroid[0] - state.last_centroid[0]
                dy = centroid[1] - state.last_centroid[1]
                dist = math.hypot(dx, dy)

                # Check if movement qualifies as multi-finger scrolling
                if dist > 3.0 or state.scroll_active:
                    state.scroll_active = True  # PERMANENTLY LOCKOUT RIGHT CLICK FOR THIS TOUCH

                    state.scroll_acc_x += dx * SCROLL_SENSITIVITY_X
                    state.scroll_acc_y += dy * SCROLL_SENSITIVITY_Y

                    step_x = int(state.scroll_acc_x)
                    step_y = int(state.scroll_acc_y)

                    if step_x != 0 or step_y != 0:
                        state.mouse.scroll(step_x, step_y)
                        state.scroll_acc_x -= step_x
                        state.scroll_acc_y -= step_y

            state.last_centroid = centroid

            # Evaluate Tap-and-Hold Right Click ONLY IF scrolling has NEVER occurred
            if state.f2_start_time is not None and not state.scroll_active and not state.f2_right_click_fired:
                f2_duration = current_time - state.f2_start_time
                if f2_duration >= HOLD_RIGHT_CLICK_TIME:
                    state.mouse.click(Button.right, 1)
                    state.f2_right_click_fired = True

    # --------------------------------------------------------------------------
    # ALL FINGERS LIFTED -> CLEANUP & TAP RECORDING
    # --------------------------------------------------------------------------
    elif num_active == 0:
        state.was_multitouch = False

        # Release mouse button if we were in an active drag
        if state.is_left_held:
            state.mouse.release(Button.left)
            state.is_left_held = False

        # 1-FINGER TAP EVALUATION (RESTORED)
        if state.f1_start_time is not None:
            duration = current_time - state.f1_start_time
            if duration <= MAX_TAP_DURATION and not state.is_dragging_cursor:
                # Emit single left click
                state.mouse.click(Button.left, 1)
                state.last_f1_release_time = current_time

        # Full state reset
        state.f1_start_time = None
        state.f1_start_pos = None
        state.last_f1_pos = None
        state.f2_start_time = None
        state.f2_start_pos = None
        state.is_dragging_cursor = False
        state.drag_candidate = False
        state.f2_right_click_fired = False
        state.scroll_active = False
        state.last_centroid = None

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
            report = dev.read(64)
            if report:
                parse_pen_packet(report)
            else:
                time.sleep(0.005)
    except Exception as e:
        print(f"[Pen Error] {e}")
    finally:
        dev.close()


def run_touch_interface(device_info):
    dev = hid.device()
    try:
        dev.open_path(device_info['path'])
        dev.set_nonblocking(True)
        print(f"[Thread Started] Touch Interface ({device_info.get('interface_number', 1)})")
        while True:
            report = dev.read(64)
            if report:
                process_touch_frame(report)
            else:
                time.sleep(0.005)
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

    print("\n=== Driver Running ===")
    print(" - Industry Standard Double-Tap Drag active.")
    print(" - Instant Finger-2 Tap = Left Click.")
    print(" - Finger-2 Tap & Hold = Right Click (Scroll-Guarded!).")
    print(" - Cursor freeze during 2-finger taps active.")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nShutting down driver...")

if __name__ == '__main__':
    main()