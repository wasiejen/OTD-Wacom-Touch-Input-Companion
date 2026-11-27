"""Win32 ctypes input structures and the ActionDispatcher that
emits them, moved from touch_controller."""

import threading
import time
import ctypes
from vk_codes import vk_codes_dict  # Import vk_code dictionary
from wtd import config as wtd_config
from wtd.config import (TOUCH_MODIFIER_KEYS, TOUCH_NO_ACTION)
from wtd._log import logger as _log  # shared wtd logger (see wtd/_log.py)




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

    def __init__(self, state, cfg, touch_enabled_event=None):
        self.state = state
        # Per-connection config instance (U73); None keeps the module config.
        self.config = cfg
        # TRAY unit (#448): the ONE touch-output flag, shared (never forked)
        # with the tray menu and the DeviceManagerThread. A set event = touch
        # output on; cleared = touch gestures/cursor/scroll/hold output off
        # while pen coordinate output and tablet button/dial emissions stay
        # live. None (no tray/manager, e.g. probe and tests) = always on.
        # Reading the event per emission is the single source of truth - the
        # tray icon and the touch_toggle action flip THE SAME object.
        self.touch_enabled_event = touch_enabled_event
        # Inputs this dispatcher currently holds; released on thread exit.
        # A4 (review Item 7): one lock per dispatcher over this held-input
        # registry (held_keys, left_held, pen_pressure_held, and the drain in
        # release_held_inputs). It guards the bookkeeping only - never an
        # injection - so the gesture and decode paths pay a single lock around a
        # set op and nothing else.
        self._held_lock = threading.Lock()
        self.held_keys = set()
        self.left_held = False
        # Pen-held inputs: pressure left-hold and the two pen buttons.
        self.pen_pressure_held = False
        self.pen_button_held = {}
        # Tablet button-held inputs (U71): slots 1..8 express keys, 9 central.
        self.button_held = {}
        # Touch-part holds (U381 / #381): slots 1..8 while byte 5 keeps a
        # finger in contact; the modifier is released on the full release.
        self.touch_held = {}
        # Move coalescing (WTD-BATCH): depth-1 latest-wins slot for the
        # absolute pen lane. The dispatcher is shared by the connection's
        # reader threads (Pen/Touch/optional Buttons): only the Pen thread
        # queues the slot, but a flush may run on any reader thread of this
        # connection (stateful emissions happen on all of them, and
        # SendInput injection is OS-serialized). The slot operations are
        # atomic under self._move_lock so two reader threads cannot emit
        # the same or an inverted pair; the shutdown sweep
        # (release_held_inputs, a foreign thread) only drops the slot, so
        # no flush runs off any of the connection's reader threads.
        self.pending_move = None
        self._move_lock = threading.Lock()
        # Monotonic clock of the last move emission or gate arming; the
        # reader-loop time gate emits a pending move at most once per flush
        # interval (config pen_move_flush_hz). Written under _move_lock.
        self._move_gate_at = None

        # The Dispatch Table
        self.ACTION_DISPATCH = {
            # Mouse actions
            "left_click": lambda: (self.send_mouse_click(self.MOUSEEVENTF_LEFTDOWN), self.send_mouse_click(self.MOUSEEVENTF_LEFTUP)),
            "right_click": lambda: (self.send_mouse_click(self.MOUSEEVENTF_RIGHTDOWN), self.send_mouse_click(self.MOUSEEVENTF_RIGHTUP)),
            "middle_click": lambda: (self.send_mouse_click(self.MOUSEEVENTF_MIDDLEDOWN), self.send_mouse_click(self.MOUSEEVENTF_MIDDLEUP)),
            
            # State-based holds (delegated to custom handlers if needed)
            "left_hold": self.handle_left_hold, 
            "left_hold_release": self.handle_left_hold_release,

            # TRAY unit (#448): assignable touch on/off toggle; stateful,
            # single-flip (like the hold pair it sits by, it drives state,
            # not a key tap). Control lane: never itself gated.
            "touch_toggle": self.handle_touch_toggle,
            
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

            # #472: clipboard trio, session lock and Esc - shortcut taps in
            # the undo/redo shape, so every lane that runs
            # execute_mapped_action (gesture, pen button, tablet button, dial)
            # fires them. The lock is the session API handler below, not a
            # chord.
            "copy": self.key_tap("ctrl", "c"),
            "cut": self.key_tap("ctrl", "x"),
            "paste": self.key_tap("ctrl", "v"),
            "lock_workstation": self.handle_lock_workstation,
            "escape": self.key_tap("esc"),
            
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
        # WTD-BATCH flush-before-stateful: a parked positional move must
        # never leapfrog this button/wheel/edge event, so it goes out first.
        self.flush_pending_move()
        extra = ctypes.c_ulong(0)
        ii = INPUT()
        ii.type = self.INPUT_MOUSE
        ii.mi = MOUSEINPUT(0, 0, data, dw_flags, 0, ctypes.pointer(extra))
        ctypes.windll.user32.SendInput(1, ctypes.pointer(ii), ctypes.sizeof(ii))


    def send_mouse_scroll(self, dx, dy):
        """Handles vertical and horizontal scrolling with a fractional accumulator.

        Sub-notch deltas accumulate in state.scroll_acc_x/y instead of being
        truncated to zero, mirroring the cursor accumulator.

        TRAY unit (#448) choke point: touch scroll/pinch is this method's
        ONLY caller (verified by grep: the dial drives mapped actions, not
        this), so the gate here is touch-scoped by construction.
        """
        if self.touch_stopped():
            return
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
        # WTD-BATCH flush-before-stateful: keyboard events are commands;
        # a parked move is injected before them, never after.
        self.flush_pending_move()
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
        """Sends a hardware-level mouse move event to Windows, forcing hover states.

        Relative-lane emitter: deliberately never flushes the absolute slot
        (in relative mode the slot stays empty - the lanes are mutually
        exclusive per report; one live absolute->relative mode switch can
        let the last parked absolute move emit on the next gate/stateful
        flush behind the first relative deltas - self-correcting).
        """
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
        """Absolute pointer move in the 0..65535 virtual-desktop space.

        Raw emitter: the coalescer (queue/flush below) and direct callers
        use it; it never touches the slot.
        """
        extra = ctypes.c_ulong(0)
        ii = INPUT()
        ii.type = self.INPUT_MOUSE
        ii.mi = MOUSEINPUT(int(nx), int(ny), 0,
                           self.MOUSEEVENTF_MOVE | self.MOUSEEVENTF_ABSOLUTE | self.MOUSEEVENTF_VIRTUALDESK,
                           0, ctypes.pointer(extra))
        ctypes.windll.user32.SendInput(1, ctypes.pointer(ii), ctypes.sizeof(ii))

    # ==============================================================================
    # MOVE COALESCING (WTD-BATCH)
    # ==============================================================================

    def queue_pen_absolute_move(self, nx, ny):
        """Park the latest absolute position in the slot (latest wins).

        The absolute lane queues instead of injecting: a drain burst
        overwrites the slot, so only the newest position can ever be
        emitted. Report decoding, the grow-only autoscale tracking, and the
        pressure state machine keep running per report - only the move
        EMISSION is collapsed. Atomic against a concurrent flush, so a
        queued position can never be lost in a read-clear interleaving.
        """
        move = (int(nx), int(ny))
        with self._move_lock:
            self.pending_move = move

    def flush_pending_move(self):
        """Inject the parked move NOW: flush-before-stateful + OOR path.

        Every stateful choke (send_mouse_click, send_key) and the pen
        out-of-range release path call this first, so a positional move
        never leapfrogs a button/wheel/key event and the final position
        lands on every pen lift/leave. Not rate-capped: ordering
        correctness outranks the emission cap.
        """
        if self.pending_move is None:
            return
        self._emit_pending_move(time.monotonic())

    def time_gated_flush(self, now):
        """Reader-loop rate cap: at most one move emission per flush interval.

        `now` is the caller's monotonic time (the test seam: a scripted
        clock, no sleeps). A slot sighting arms the gate; emission then
        requires a whole flush interval since the arm or the last emission,
        so an idle reader with an empty slot emits nothing extra (no
        spin-flush on the non-blocking binding) and a superseded slot never
        emits the older position.
        """
        with self._move_lock:
            if self.pending_move is None:
                return
            if self._move_gate_at is None:
                self._move_gate_at = now
                return
            if now - self._move_gate_at < self._move_flush_interval():
                return
        self._emit_pending_move(now)

    def _move_flush_interval(self):
        """Seconds between time-gated move emissions, from the config knob.

        Falls back to the shipped default when the fork carries no value or
        a junk one, so a stale live-mutation cannot divide by zero on the
        reader thread.
        """
        cfg = self.config if self.config is not None else wtd_config.config
        hz = getattr(cfg, "pen_move_flush_hz", None)
        if not isinstance(hz, (int, float)) or isinstance(hz, bool) or hz <= 0:
            hz = wtd_config.PEN_MOVE_FLUSH_HZ_DEFAULT
        return 1.0 / float(hz)

    def _emit_pending_move(self, at):
        """Single emission choke for the slot; `at` refreshes the gate clock.

        Read-and-clear is atomic under _move_lock (two reader threads of a
        shared dispatcher can flush concurrently); the injection itself
        happens OUTSIDE the lock (the held-input lock doctrine: locks never
        wrap an injection).
        """
        with self._move_lock:
            move = self.pending_move
            if move is None:
                return
            self.pending_move = None
            self._move_gate_at = at
        self.send_pen_absolute_move(move[0], move[1])

    def press_pen_pressure(self):
        """Pressure above threshold holds the left button like a mouse click."""
        self.send_mouse_click(self.MOUSEEVENTF_LEFTDOWN)
        with self._held_lock:
            self.pen_pressure_held = True

    def release_pen_pressure(self):
        # The claim is the lock's point: whoever clears the flag is the one that
        # releases, so a second caller in the same window injects nothing
        # (Item 7). The injection itself happens outside the lock.
        with self._held_lock:
            held = self.pen_pressure_held
            self.pen_pressure_held = False
        if held:
            self.send_mouse_click(self.MOUSEEVENTF_LEFTUP)

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
    # TOUCH-PART HELPERS (U381 / #381)
    # ==============================================================================

    def press_touch_modifier(self, slot, modifier):
        """Touch contact edge: hold the configured modifier while the finger stays.

        "none" (the default) and an unset value do nothing and say nothing;
        any other unknown name is ignored instead of raising, because the
        value comes from a user-editable config file and one bad entry must
        not take the reader thread down.

        TRAY unit (#448) choke point: the PRESS edge is touch output and
        gates here; release_touch_modifier deliberately never gates, so a
        hold that outlives a stop still gets its key back (no stuck input).
        """
        if self.touch_stopped():
            return
        if modifier is None or modifier == TOUCH_NO_ACTION:
            return
        if modifier not in TOUCH_MODIFIER_KEYS:
            _log.info(f"[Warning] Unknown touch modifier: {modifier}")
            return
        if modifier in self.touch_held.values():
            # Another contact already holds this modifier down: record the
            # slot only, so the key-down/up pair stays balanced.
            self.touch_held[slot] = modifier
            return
        self.press_key(modifier)
        self.touch_held[slot] = modifier

    def release_touch_modifier(self, slot):
        """Full release of one contact: drop that slot's hold.

        A modifier two buttons both hold stays down until the last slot using
        it releases, so lifting one finger cannot un-modify the other.
        """
        modifier = self.touch_held.pop(slot, None)
        if modifier is not None and modifier not in self.touch_held.values():
            self.release_key(modifier)

    # ==============================================================================
    # HELPER FUNCTIONS - INPUT
    # ==============================================================================  

    
    # Helper to cleanly bind parameters for simple keys
    def key_tap(self, *vk_keys):
        return lambda: self.send_key_shortcut(*(vk_codes_dict[k] for k in vk_keys))

    def press_key(self, vk_code):
        self.send_key(vk_codes_dict[vk_code], release=False)
        with self._held_lock:
            self.held_keys.add(vk_code)
        
    def release_key(self, vk_code):
        self.send_key(vk_codes_dict[vk_code], release=True)
        with self._held_lock:
            self.held_keys.discard(vk_code)

    def release_held_inputs(self):
        """Releases every modifier key and the left button this dispatcher holds.

        Called on interface-thread exit so a pause or disconnect mid-gesture
        cannot leave Ctrl or the left button pressed system-wide, and also by
        the shutdown sweep (unit A2) - so two callers can be inside this method
        at the same moment. The whole held-input registry is therefore drained
        under self._held_lock first, and every injection happens after the
        drain: for the keys, touch slots, pen buttons and tablet buttons,
        whichever caller drained a held input is the only one that can release
        it - the duplicate key-up Item 7 reports. Injection order and the printed
        names are unchanged. The left button is the exception that stays outside
        that guarantee: handle_left_hold_release still injects its key-up
        unconditionally instead of claiming the flag, so a drain and a concurrent
        left-release can each send one (benign, filed to the estate todo_inbox).
        """
        with self._held_lock:
            # WTD-BATCH: DROP the parked absolute move, never flush it: this
            # drain also runs on the shutdown sweep thread, where a flush
            # would inject off the connection's reader threads, and a held
            # release must never wait on a positional event. A lost
            # positional sample strands no state (unlike a dropped button).
            # Known residual (reviewer P2, accepted): a straggler reader
            # outside the join budget can re-queue a slot after this drop,
            # and the sweep's own flush hook then injects that one move
            # between release pairs - worst case one stray cursor step at
            # shutdown; blocking it needs a drained flag that would survive
            # a pause/resume of the same dispatcher.
            with self._move_lock:
                self.pending_move = None
            touch_held = dict(self.touch_held)
            self.touch_held.clear()
            # The touch pass below releases these modifiers, so the generic pass
            # must not pick them up from the snapshot either - that is the same
            # second key-up the live drain used to avoid by discarding first.
            # Scope of the dedup: the held_keys overlap only. Two touch slots
            # sharing one modifier still take two release paths here (pre-existing,
            # estate todo_inbox), because the per-slot release has no claim.
            touch_modifiers = set(touch_held.values())
            keys = [vk_code for vk_code in self.held_keys
                    if vk_code not in touch_modifiers]
            self.held_keys.clear()
            left_held = self.left_held
            self.left_held = False
            pressure_held = self.pen_pressure_held
            self.pen_pressure_held = False
            pen_buttons_held = dict(self.pen_button_held)
            self.pen_button_held.clear()
            buttons_held = dict(self.button_held)
            self.button_held.clear()

        released = []

        # Touch holds first: their modifier names also sit in held_keys, and
        # releasing them here keeps the generic pass below from sending a
        # second key-up for the same modifier (U381 / #381).
        for slot, modifier in touch_held.items():
            self.release_key(modifier)
            released.append(f"touch_{modifier}_{slot}")

        for vk_code in keys:
            self.release_key(vk_code)
            released.append(vk_code)

        if left_held:
            self.send_mouse_click(self.MOUSEEVENTF_LEFTUP)
            self.state.is_left_held = False
            released.append("left_button")

        if pressure_held:
            self.send_mouse_click(self.MOUSEEVENTF_LEFTUP)
            released.append("pen_pressure")

        for slot, action_name in pen_buttons_held.items():
            self.send_mouse_click(self.PEN_BUTTON_FLAGS[action_name][1])
            released.append(f"pen_button_{slot}")

        for slot, action_name in buttons_held.items():
            self.send_mouse_click(self.PEN_BUTTON_FLAGS[action_name][1])
            released.append(f"button_{slot}")

        if released:
            _log.info(f"[Safety] Released held input on thread exit: {released}")

        return released

    def handle_left_hold(self):
        self.send_mouse_click(self.MOUSEEVENTF_LEFTDOWN)
        with self._held_lock:
            self.left_held = True
        self.state.is_left_held = True
        self.state.is_dragging_cursor = True
        self.state.active_gesture = None
        
    def handle_left_hold_release(self):
        self.send_mouse_click(self.MOUSEEVENTF_LEFTUP)
        with self._held_lock:
            self.left_held = False
        self.state.is_left_held = False
        self.state.active_gesture = None

    def touch_stopped(self):
        """TRAY unit (#448): True when the shared touch-output flag is
        cleared. None flag (no tray/manager caller) = never stopped."""
        return (self.touch_enabled_event is not None
                and not self.touch_enabled_event.is_set())

    def handle_touch_toggle(self):
        """Assignable touch on/off toggle (TRAY unit / TODO #448).

        Flips the SHARED threading.Event - the same object the tray menu
        item and the icon read - so a mapped tablet button and the tray
        never disagree. Mirrors the hold-pair convention: state flip here,
        status surfaces (icon, log) react off the flag itself. Control
        lane: works in every touch state, and the dispatch choke points
        gate only touch OUTPUT, never this action.
        """
        if self.touch_enabled_event is None:
            _log.info("[Toggle] touch_toggle mapped but no touch state "
                      "exists in this run (no tray/manager) - ignored")
            return
        if self.touch_enabled_event.is_set():
            self.touch_enabled_event.clear()
            _log.info("[Toggle] Touch output OFF (pen and tablet buttons "
                      "stay live)")
        else:
            self.touch_enabled_event.set()
            _log.info("[Toggle] Touch output ON")

    def handle_lock_workstation(self):
        """Assignable session lock (#472): Windows LockWorkStation.

        The Win+L key combo is deliberately NOT injected: Windows reads the
        physical Win/L state for that hotkey, so an injected Win+L does not
        lock the session (documented shell behaviour, re-verified in #472) -
        the mapped button would tap into the void. LockWorkStation is the same
        call the Start-menu Lock entry makes, so it always works from a
        tablet button. A failure is logged, never raised: an action must not
        take the reader thread down with it.
        """
        # Resolved BEFORE the call: the lazy kernel32 lookup of GetLastError
        # would otherwise sit between the failed call and the error read and
        # could report a stale code (reviewer P2). LockWorkStation itself stays
        # resolved per call so the test seam can shadow it.
        get_last_error = ctypes.windll.kernel32.GetLastError
        if not ctypes.windll.user32.LockWorkStation():
            _log.info("[Warning] LockWorkStation failed (Win32 error %d)"
                      % get_last_error())

    def execute_mapped_action(self, action_name):
        if wtd_config.DEBUG:
            _log.info(f"[Gesture] Action triggered: {action_name}")

        action_func = self.ACTION_DISPATCH.get(action_name)
        if action_func:
            action_func()
        elif action_name is not None:
            _log.info(f"[Warning] Unknown action name: {action_name}")

    def dispatch_gesture_event(self, event_type):
        # TRAY unit (#448) choke point: every touch tap/press/gesture runs
        # through here (grep: touch gestures only - pen buttons and the
        # tablet buttons/dial drive press_*_button/execute_mapped_action
        # directly), so one gate covers the whole gesture-output lane and
        # nothing outside it.
        if self.touch_stopped():
            return
        cfg = self.config  # C1: cfg is required - no global fallback
        if cfg.feature_toggles.get(event_type, False):
            mapping = cfg.action_mapping.get(event_type, None)
            if callable(mapping):
                action_name = mapping(self.state)
            else:
                action_name = mapping
            
            self.state.active_gesture = event_type
            self.execute_mapped_action(action_name)
