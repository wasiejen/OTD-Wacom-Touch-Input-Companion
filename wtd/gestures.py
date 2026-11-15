"""TabletState and the touch/button gesture cascade moved from
touch_controller."""

import time
import math
from wtd.config import (DEBUG)
from wtd.protocol import (BUTTON_PRESS_MASKS, BUTTON_TOUCH_MASKS, CENTRAL_BUTTON_SLOT, DIAL_ANGLE_ACTIVE, DIAL_ANGLE_MASK, MAX_TOUCH_CONTACTS, STATUS_ACTIVE_CONTACTS, STATUS_RELEASE_TERMINAL, STATUS_TOUCH_DOWN, decode_button_report, dial_angle_delta, parse_sub_block)
from wtd.mapping import (TOUCH_ROTATION_CENTER, apply_orientation)
from wtd._log import logger as _log  # shared wtd logger (see wtd/_log.py)



# ==============================================================================
# STATE TRACKER
# ==============================================================================
class TabletState:
    """Mutable gesture/session state one connection's threads share.

    Threading rule (unit A4 / review Item 7): the fields here stay deliberately
    LOCK-FREE with a single writer each. Every field has exactly one owner thread
    by design - the interface reader thread that decodes the reports writes the
    contact, gesture, proximity and tracking fields; the GUI and the shutdown
    path only read them. A single write per field from its owner is atomic enough
    for the CPython runtime this driver targets, so no field carries a lock and
    no read path pays for one.

    Exceptions, stated so the rule stays checkable:
    - is_left_held: the one flag with a genuine cross-thread writer. The reader
      thread that owns the gesture sets it; ActionDispatcher's
      release_held_inputs drain clears it from the shutdown/sweep thread. That is
      why the held INPUT itself lives in ActionDispatcher, under
      ActionDispatcher._held_lock, and not here.
    - active_gesture / is_dragging_cursor: written only by the reader thread that
      owns the gesture (handle_left_hold sets them, handle_left_hold_release
      clears them), so they stay single-writer like every field above.
    - The dispatcher's held-input registry (held_keys, left_held,
      pen_pressure_held and the touch/pen/button held dicts) is the ONE place
      two threads may meet; it is guarded there, never here. Its drain in
      release_held_inputs is the only cross-thread mutation, and it is atomic.
    """

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
        # U344 autoscale lane: the largest decoded pen coordinate per axis
        # this session (raw, pre-orientation) plus the one-shot flag for
        # the named autoscale line.
        self.pen_obs_max_x = 0
        self.pen_obs_max_y = 0
        self.pen_extents_scaled_logged = False
        # U376 diagnostic lane: the wired product id of this connection and
        # the one-shot flag for the named mapping-snapshot line.
        self.pen_product_id = None
        # Identity key of the connected pen interface (U414 / #406): the
        # pen_area_map lane lookup key, set once per pen connection.
        self.pen_identity = None
        self.pen_abs_map_logged = False

        # Touch button/dial tracking (U71): last-seen byte 4 press bitmask,
        # central-button press state, dial origin angle and step accumulator.
        # U381 adds the byte 5 contact mask, so a touch part sees its own
        # contact edges independently of the press edges.
        self.button_press_mask = 0
        self.button_touch_mask = 0
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

def process_button_report(report, state, action_dispatch, config):
    """Touch buttons and the rotary dial from the 10-byte 0x03 report (U71).

    Byte 4 is a press bitmask (bit n = button n+1, so buttons 1+2 = 0x03);
    byte 3 bit 0 is the central button. Press and release edges drive the
    mapping table like the pen buttons. Byte 2 carries the dial angle while a
    finger is in the rotary field; deltas accumulate into CW/CCW step actions,
    mirroring the alt-tab step family. Byte 5 is the touch-contact mask: its
    edges drive the per-button touch part (U381 / #381: hold the configured
    modifier while the contact lasts, disabled by default) and never gate a
    press -- contact alone carries no press, and a press stays live while the
    finger keeps contact.
    """
    parsed = decode_button_report(report)
    if parsed is None:
        return

    ad = action_dispatch
    press_mask = parsed["press"]

    # Touch part before the press edges (U381 / #381): a press that arrives in
    # the same report as a fresh contact runs with that contact's modifier
    # already held down (recorded press row carries both bits).
    touch_mask = parsed["touch"]
    for slot in range(1, 9):
        bit = BUTTON_TOUCH_MASKS[slot - 1]
        present = bool(touch_mask & bit)
        if present == bool(state.button_touch_mask & bit):
            continue
        key = f"btn{slot}_touch"
        if present:
            if config.feature_toggles.get(key, False):
                ad.press_touch_modifier(slot, config.action_mapping.get(key))
        else:
            ad.release_touch_modifier(slot)
    state.button_touch_mask = touch_mask

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

def process_touch_report(report, state, action_dispatch, cfg):
    """Decode one touch report against ONE named config.

    Unit B2 removed the module-global fallback: the caller states which config
    this lane reads, so a thread can no longer inherit whatever the import-time
    global happened to hold (review Item 3, C1-enabler half).
    """
    ad = action_dispatch
    
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

        # Orientation (U74): rotate every contact through the shared path
        # before the gesture/cursor processing sees it.
        pos = apply_orientation(pos, cfg, TOUCH_ROTATION_CENTER)

        if status == STATUS_TOUCH_DOWN:
            state.active_contacts[slot_id] = pos
            state.start_time[clamped_contact_index(len(state.active_contacts))] = current_time
            state.peak_contact_count = max(state.peak_contact_count, len(state.active_contacts))
            if len(state.active_contacts) == 1 and state.last_num_active == 0:
                state.session_start_time = current_time
                if DEBUG: _log.info(f"Debug: new session triggered")
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
        #if DEBUG: _log.info(f"Debug: session duration: {session_duration}")
    if state.start_time[contact_index] is not None:
        hold_duration = current_time - state.start_time[contact_index]
    else:
        hold_duration = 0
        
    # reset centoid with every change of active contact to prevent jumping or misfiring of gestures
    if num_active >= 2:
        (cx, cy), spread =  compute_centroid_and_spread(state.active_contacts)
        
        if state.last_num_active != num_active or state.mf_start_centroid is None:
            if DEBUG: _log.info(f"Debug: Reset due to active Contact number change.")
            # if state.mf_start_centroid is None:
            state.mf_start_centroid = (cx, cy)
            state.mf_start_spread = spread
            state.last_pinch_distance = spread
            state.last_alt_tab_x = cx  
    # if DEBUG: _log.info(f"Debug: {num_active} fingers detected with Gesture: state: {state.active_gesture}")

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
                            if DEBUG: _log.info("[Gesture] 1F Double Tap Drag Initiated")

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
                    # _log.info(f"dx,dy: {dx,dy}")    

                    scaled_dx, scaled_dy = calculate_piecewise_accelerated_delta(dx, dy, cfg, "scroll_acceleration")
                    
                    step_x = scaled_dx * -cfg.scroll_sensitivity_x
                    step_y = scaled_dy * cfg.scroll_sensitivity_y
                    
                    if step_x != 0 or step_y != 0:
                        # _log.info(f"step_x,step_y: {step_x,step_y}")  
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

                #if DEBUG: _log.info(f"session_duration: {session_duration}, cfg.max_tap_duratio: {cfg.max_tap_duration}, sd<max_dur: {session_duration <= cfg.max_tap_duration}")
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
                    if DEBUG: _log.info("[Gesture] 1F Press & Hold Drag Initiated")
                    
            elif num_active >= 2 and cfg.feature_toggles.get(f"{num_active}f_press", False):    
                if (hold_duration >= cfg.press_hold_duration 
                        and dist_centroid <= cfg.tap_max_movement
                        and abs(delta_spread_total) <= cfg.pinch_discrete_threshold
                        ):
                    if DEBUG: _log.info(f"[Gesture] Press hold for {num_active} Fingers triggered")
                    gesture_name = f"{num_active}f_press"
                    ad.dispatch_gesture_event(gesture_name)                    
        
    state.last_num_active = num_active
