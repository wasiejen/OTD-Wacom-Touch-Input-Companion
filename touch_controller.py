import hid
import time
from pynput.mouse import Controller

VENDOR_ID = 0x056A
PRODUCT_ID = 0x0317
TOUCH_INTERFACE = 1

# --- TUNING ---
SCROLL_SPEED_X = -0.04   # Custom horizontal scroll speed
SCROLL_SPEED_Y = -0.04   # Vertical scroll speed
MOUSE_SPEED = 1      # 1-Finger trackpad tracking speed multiplier
DEADZONE = 0           # Noise suppression boundary

mouse = Controller()



def extract_active_contacts(report):
    """
    Parses Wacom 12-bit coordinates with corrected nibble assignments:
    - Byte 4: X High (bits 11:4)
    - Byte 5: Y High (bits 11:4)
    - Byte 6: X Low Nibble (top 4 bits: 0xF0) | Y Low Nibble (bottom 4 bits: 0x0F)
    """
    contacts = {}
    
    if len(report) < 15 or report[0] != 0x02:
        return contacts

    blocks_in_packet = report[1]
    
    # --- Finger 1 Block (Bytes 2-6) ---
    f1_status = report[2]
    f1_id = f1_status & 0x0F
    
    if f1_status != 0x81 and f1_id > 0:
        # Swap Nibble Assignment:
        # X gets top 4 bits of Byte 6
        x1 = (report[4] << 4) | ((report[6] & 0xF0) >> 4)
        
        # Y gets bottom 4 bits of Byte 6
        y1 = (report[5] << 4) | (report[6] & 0x0F)
        
        contacts[f1_id] = (x1, y1)

    # --- Finger 2 Block (Bytes 10-14) ---
    if blocks_in_packet >= 2:
        f2_status = report[10]
        f2_id = f2_status & 0x0F
        
        if f2_status != 0x81 and f2_id > 0:
            x2 = (report[12] << 4) | ((report[14] & 0xF0) >> 4)
            y2 = (report[13] << 4) | (report[14] & 0x0F)
            
            contacts[f2_id] = (x2, y2)

    return contacts

def run_touch_controller():
    device_path = None
    for dev in hid.enumerate(VENDOR_ID, PRODUCT_ID):
        if dev['interface_number'] == TOUCH_INTERFACE:
            device_path = dev['path']
            break

    if not device_path:
        print("Touch interface not found.")
        return

    tablet = hid.device()
    tablet.open_path(device_path)
    tablet.set_nonblocking(True)

    print("--- Wacom Touch Controller Active (Y-Mask Applied) ---")
    
    active_fingers = {}
    last_center = None
    last_single_pos = None

    try:
        while True:
            report = tablet.read(64)
            if report and report[0] == 0x02:
                # Finger lift signal (0x81) -> clear all state to prevent jump-deltas
                if report[2] == 0x81:
                    active_fingers.clear()
                    last_single_pos = None
                    last_center = None
                    continue

                new_contacts = extract_active_contacts(report)
                for fid, pos in new_contacts.items():
                    active_fingers[fid] = pos

                count = len(active_fingers)

                # --- 1 FINGER: TRACKPAD CURSOR MOVEMENT ---
                if count == 1:
                    pos = list(active_fingers.values())[0]
                    if last_single_pos is not None:
                        dx = pos[0] - last_single_pos[0]
                        dy = pos[1] - last_single_pos[1]
                        # Only move if movement exceeds deadzone jitter threshold
                        if abs(dx) > DEADZONE or abs(dy) > DEADZONE:
                            mouse.move(int(dx * MOUSE_SPEED), int(dy * MOUSE_SPEED))
                            
                    last_single_pos = pos
                    last_center = None

                # --- 2 FINGERS: ATTENUATED SCROLLING ---
                elif count == 2:
                    pts = list(active_fingers.values())
                    center_x = (pts[0][0] + pts[1][0]) / 2.0
                    center_y = (pts[0][1] + pts[1][1]) / 2.0

                    if last_center is not None:
                        dx = center_x - last_center[0]
                        dy = center_y - last_center[1]

                        sx = int(dx * SCROLL_SPEED_X) if abs(dx) > DEADZONE else 0
                        sy = int(-dy * SCROLL_SPEED_Y) if abs(dy) > DEADZONE else 0

                        if sx != 0 or sy != 0:
                            mouse.scroll(sx, sy)

                    last_center = (center_x, center_y)
                    last_single_pos = None

                else:
                    last_single_pos = None
                    last_center = None

            time.sleep(0.004) # ~250Hz check rate

    except KeyboardInterrupt:
        print("\nStopping Touch Controller.")
    finally:
        tablet.close()

if __name__ == "__main__":
    run_touch_controller()