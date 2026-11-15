"""HID report constants, pen spec tables, and the state-free
report decoders moved from touch_controller."""

import time
from wtd.config import (WIRELESS_RECEIVER_PRODUCT_ID, WIRELESS_TABLET_SIZES, WIRELESS_TABLET_SIZE_TO_PRODUCT)


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

# Owned-shutdown join budget (Item 1 / A2), in seconds, for ONE whole shutdown
# (not per thread). Readers stay daemon threads on purpose: a wedged HID read
# can then cost at most this much, never a hang. Whatever is still running
# afterwards gets its dispatcher force-released by shutdown_readers().
SHUTDOWN_JOIN_BUDGET_SECONDS = 1.0

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

# Digitizer ranges per product id: one explicit per-model entry keyed by the
# wired product id. All three rows are VERBATIM from the OpenTabletDriver
# Digitizer block at pinned commit
# https://raw.githubusercontent.com/OpenTabletDriver/OpenTabletDriver/2c4b9a5649eb34cd74bc8b1b1041dbb0356ae762/OpenTabletDriver.Configurations/Configurations/Wacom/PTH-851.json
# (same commit, same folder: PTH-451.json for 788, PTH-651.json for 789):
#   788 = Wacom PTH-451 (Intuos Pro Small)  157.48 x  98.425 mm, 16:10
#   789 = Wacom PTH-651 (Intuos Pro Medium) 223.52 x 139.7   mm, 16:10
#   791 = Wacom PTH-851 (Intuos Pro Large)  325.12 x 203.2   mm, 16:10
# Every row is exactly 200.0 counts/mm on both axes. The OTD Touch
# sub-block (4095/4095) is the TOUCH digitizer, NOT the pen: it never
# feeds this table (U370 ruling). Pre-U370 the 788/791 rows carried
# probe-era fills (4096/4096 square @ 215.9 mm width); the maintainer's
# 2026-11-04 hardware findings (791 edges unreachable ~100 px, diagonal
# shallower than 1:1) came from that square box skewing the 1:1 lane, not
# from the hardware. The U344 autoscale lane (pen_effective_specs,
# pen_extents_autoscale line) stays as the safety net: grow-only, it can
# never shrink below these declared extents (pinned).
# Ids with no entry (unknown ids, the wireless receiver id 132) resolve
# through the named fallback line below.
PEN_SPECS_BY_PRODUCT = {
    # OTD PTH-651.json @ 2c4b9a56, Digitizer block:
    789: {"max_x": 44704, "max_y": 27940, "width_mm": 223.52, "height_mm": 139.7},
    # OTD PTH-451.json @ 2c4b9a56, Digitizer block:
    788: {"max_x": 31496, "max_y": 19685, "width_mm": 157.48, "height_mm": 98.425},
    # OTD PTH-851.json @ 2c4b9a56, Digitizer block:
    791: {"max_x": 65024, "max_y": 40640, "width_mm": 325.12, "height_mm": 203.2},
}
PEN_SPECS_DEFAULT = PEN_SPECS_BY_PRODUCT[789]

def pen_specs_for_product(product_id, cfg=None):
    """Digitizer ranges for the pen device.

    A product with a table entry routes to its own model. The wireless
    receiver (id 132) has no table entry: its paired tablet size is not
    detectable, so the user-config size names the model (named routing
    line). U386 / TODO #385: NO silent default -- with the size unset
    (or no config passed at all) a LOUD notice names the three choices,
    the config lane, and the GUI lane, and labels the medium extents a
    FALLBACK, not a default. Any other id with no entry prints ONE
    byte-stable named fallback line and uses the Medium defaults.
    """
    specs = PEN_SPECS_BY_PRODUCT.get(product_id)
    if specs is None:
        if product_id == WIRELESS_RECEIVER_PRODUCT_ID:
            size = getattr(cfg, "wireless_tablet_size", None) if cfg is not None else None
            if size in WIRELESS_TABLET_SIZES:
                model = WIRELESS_TABLET_SIZE_TO_PRODUCT[size]
                print(f"pen_specs= wireless_tablet_size={size} product={product_id} using={model}")
                return PEN_SPECS_BY_PRODUCT[model]
            print("!! WIRELESS TABLET SIZE NOT SET: receiver id 132 cannot detect its paired tablet.\n"
                  "!! choose one: small (788) | medium (789) | large (791) - GUI Pen tab "
                  "\"Wireless Tablet Size\", or \"motion_and_thresholds.wireless_tablet_size\" "
                  "in the config file.\n"
                  "!! using MEDIUM extents (789) until a size is chosen: FALLBACK, not a default.\n"
                  "pen_specs= wireless_tablet_size=unset product=132 using=789 fallback=not_chosen")
            return PEN_SPECS_DEFAULT
        print(f"pen_specs= no-table product={product_id} using=medium")
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
