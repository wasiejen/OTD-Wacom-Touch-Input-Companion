"""Reader lane: run_interface, the device manager thread, report
dispatch, and shutdown join, moved from touch_controller."""

import hid
import time
import threading
import re
from wtd import config as wtd_config
from wtd.config import (device_identity_hash, resolve_device_alias)
from wtd.protocol import (BUTTON_REPORT_ID, BUTTON_REPORT_LENGTH, IDLE_BACKOFF_MIN_SECONDS, IDLE_POLL_SECONDS, PEN_REPORT_LENGTH, SHUTDOWN_JOIN_BUDGET_SECONDS, parse_pen_packet, pen_specs_for_product)
from wtd.sender import (ActionDispatcher)
from wtd.mapping import (process_pen_report)
from wtd.gestures import (TabletState, process_button_report, process_touch_report)
from wtd._log import logger as _log  # shared wtd logger (see wtd/_log.py)



def handle_report_by_id(report, state, action_dispatch, cfg):
    """Consume any report whose id names its own handler (U381 / #381, #382).

    Returns True when this function owned the report. The recorded wired
    capture (maintainer/draft/WTD_pen_support_probe_data.md: one interface
    constant for the whole file) reads pen id 0x02 and button/dial id 0x03
    rows from the SAME interface, so a reader must not assume its interface
    carries a single report id: without this route a 0x03 press reached the
    pen or touch handler, hit that handler's own id filter, and was dropped.
    """
    if report and report[0] == BUTTON_REPORT_ID:
        process_button_report(report, state, action_dispatch, cfg)
        return True
    return False

# ==============================================================================
# SIMPLIFIED INTERFACE WORKER THREAD
# ==============================================================================

def run_interface(interface_type, device_info, state, action_dispatch, stop_event, cfg, idle_poll=IDLE_POLL_SECONDS):
    """
    Simplified worker thread for 'Pen', 'Touch' or the optional 'Buttons' input.
    Exits as soon as an OSError/Exception is thrown or stop_event is set.
    TRAY unit (#448): the retired touch-pause thread-kill is gone - the
    shared touch flag gates OUTPUT inside ActionDispatcher's choke points,
    so every reader thread stays alive in every touch state (pen, the
    0x03 button/dial reports routed on this interface, and the touch
    STATE machine all keep running; only touch emissions are skipped).
    Reads wait for reports at device cadence: a timed read is used when the HID
    binding offers read_timeout, otherwise non-blocking reads with an idle
    backoff that never sleeps between reports. Stop still takes effect
    within one poll tick while the device is silent (TRAY unit #448: a
    touch stop no longer affects this thread at all).

    Config-fork refresh contract (U386 / TODO #386): a manager spawn passes
    the connection's own config fork as cfg. The GUI "Apply Live" mutates
    that same object in place (apply_settings_to_config), so every per-report
    read of cfg here sees the applied values without a restart. The
    module-global fallback below serves only a no-manager caller (probe and
    tests). Known connect-time read: the Pen extents table (pen_specs) is
    resolved once at thread start, so a wireless size change needs a device
    reconnect (GUI notice states it; U386 leg 2).
    """
    interface_num = device_info.get('interface_number', -1)
    path = device_info.get('path')
    header = f"[{interface_type} Thread | Interface: {interface_num}]"

    dev = hid.device()

    try:
        dev.open_path(path)
        dev.set_nonblocking(True)
        _log.info(f"{header} Started successfully.")

        pen_specs = None
        if interface_type == "Pen":
            product_id = device_info.get("product_id")
            pen_specs = pen_specs_for_product(product_id, cfg)
            # U376: the named mapping diagnostic names the product it resolved.
            state.pen_product_id = product_id
            # U414 / #406: pen_area_map lane key for this interface (same
            # inputs as the manager's device_identity_hash sites).
            state.pen_identity = device_identity_hash(
                device_info.get("path") or b"",
                device_info.get("serial_number") or "")
            _log.info(f"{header} Init report not sent: upstream sends FeatureInitReport "
                  "'02 08' at device open; requirement unknown (pen data reads without it).")

        # Timed reads exist only on some HID bindings; the Windows hidapi wheel
        # exposes plain read() only, so the non-blocking fallback backs off from
        # IDLE_BACKOFF_MIN_SECONDS up to the poll tick and resets on every report.
        timed_read = getattr(dev, "read_timeout", None)
        idle_backoff = IDLE_BACKOFF_MIN_SECONDS

        while not stop_event.is_set():
            # TRAY unit (#448): NO touch-pause check here (blast-radius
            # cut): the old `break` killed the Pen and Buttons readers
            # too. Touch output gating lives in the dispatcher choke
            # points; this loop reads and state-processes in every state.
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
                if handle_report_by_id(report, state, action_dispatch, cfg):
                    # U381: the 0x03 button/dial report shares the pen or the
                    # touch interface on the recorded device; the id route
                    # owns it on every interface, the third interface too.
                    pass
                elif interface_type == "Touch":
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

            # WTD-BATCH: time-gated flush of the absolute lane's latest-wins
            # move slot (rate cap, config pen_move_flush_hz). Ticks every
            # loop pass, so the last position lands shortly after the stream
            # stops; with an empty slot the gate stays untouched (the
            # non-blocking spin leg emits nothing).
            action_dispatch.time_gated_flush(time.monotonic())

    except OSError as e:
        _log.info(f"{header} Read Error (Disconnected/OSError): {e}")
    except Exception as e:
        _log.info(f"{header} Unexpected Error: {e}")
        if wtd_config.DEBUG:
            raise e
    finally:
        try:
            action_dispatch.release_held_inputs()
        except Exception as e:
            _log.info(f"{header} Held-input release failed: {e}")
        try:
            dev.close()
        except Exception:
            pass
        _log.info(f"{header} Interface thread closed.")

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

    def __init__(self, enabled_event, stop_event, base_config, prefer_wireless=False, poll_interval=3.0):
        super().__init__(daemon=True)
        self.touch_enabled_event = enabled_event
        self.stop_event = stop_event
        self.prefer_wireless = prefer_wireless
        self.poll_interval = poll_interval
        self.lock = threading.Lock()
        # Global settings source; connections fork their own instance from it.
        self.base_config = base_config  # C1: required - no global fallback
        
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
        _log.info("[Manager Thread] Started Master Management Thread.")
        
        while not self.stop_event.is_set():
            # TRAY unit (#448): the retired touch-pause block - the old
            # wait froze enumeration/spawn/reconnect for EVERY interface
            # (pen and tablet buttons included) while touch was stopped.
            # The manager now always polls; the flag only gates emissions.
            self.manage_connections()

            self.stop_event.wait(timeout=self.poll_interval)

        _log.info("[Manager Thread] Master Management Thread stopped.")



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
                    _log.info(f"[Manager] Device disconnected/stopped: {conn['product_string']} ({dev_id})")
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
                    _log.info(f"[Manager] Holding back {held_back} device "
                          f"{pair['product_string']} ({pair['id']})")

            for dev_pair in active_devices:
                dev_id = dev_pair['id']

                if dev_id not in self.active_connections:
                    identity = device_identity_hash(
                        dev_pair['pen_device'].get('path'),
                        dev_pair['pen_device'].get('serial_number', ''))
                    # A stored user alias for this identity wins over the
                    # model-class rule (U87 a); the identity key is never the
                    # bare interface index.
                    alias = self.base_config.alias_for_identity(identity)
                    if alias is None:
                        alias = resolve_device_alias(
                            dev_pair['product_id'], dev_pair['wireless'],
                            self.base_config.device_bindings())
                    conn_config = self.base_config.fork_for_device(alias, identity)
                    _log.info(f"\n[Manager] New Device Detected! Initializing: {dev_pair['product_string']} "
                          f"(settings alias: {alias or 'none - global sections only'})")
                    
                    # Create unique state & dispatcher context per device
                    t_state = TabletState()
                    # TRAY unit (#448): the dispatcher shares the ONE touch
                    # flag (never forked) so the tray, the touch_toggle
                    # action, and the emission gates read one source.
                    a_dispatch = ActionDispatcher(t_state, conn_config,
                                                  touch_enabled_event=self.touch_enabled_event)

                    pen_dev = dev_pair['pen_device']
                    touch_dev = dev_pair['touch_device']
                    buttons_dev = dev_pair.get('buttons_device')

                    t_pen = threading.Thread(
                        target=run_interface,
                        args=("Pen", pen_dev, t_state, a_dispatch, self.stop_event),
                        kwargs={"cfg": conn_config},
                        daemon=True
                    )
                    t_touch = threading.Thread(
                        target=run_interface,
                        args=("Touch", touch_dev, t_state, a_dispatch, self.stop_event),
                        kwargs={"cfg": conn_config},
                        daemon=True
                    )

                    threads = {
                        'Pen': t_pen,
                        'Touch': t_touch
                    }

                    self.active_connections[dev_id] = {
                        'id': dev_id,
                        'identity': identity,
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
                    _log.info(f"[Manager] Spawned Pen (Interface: {pen_dev.get('interface_number')}) "
                          f"& Touch (Interface: {touch_dev.get('interface_number')}) threads for {dev_id}")

                    if buttons_dev is not None:
                        # Third interface next to Pen/Touch (U71): 0x03 button/dial
                        # reports. Absent interface means the pair stays as before.
                        t_buttons = threading.Thread(
                            target=run_interface,
                            args=("Buttons", buttons_dev, t_state, a_dispatch,
                                  self.stop_event),
                            kwargs={"cfg": conn_config},
                            daemon=True
                        )
                        threads['Buttons'] = t_buttons
                        t_buttons.start()
                        _log.info(f"[Manager] Spawned Buttons thread "
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

    def list_connections(self):
        """The live connection dicts (U87 a): one entry per active device.

        The GUI feeds its focused-device combo from this list; the entries
        are the live dicts, so a GUI alias assign reaches the running device.
        """
        with self.lock:
            return list(self.active_connections.values())

    def live_reader_threads(self):
        """Reader handles this manager recorded, live ones only (Item 1 / A2).

        These are the per-connection 'threads' dicts the staleness scan already
        keeps, so shutdown joins exactly the threads it knows by name instead of
        guessing from threading.enumerate().
        """
        with self.lock:
            threads = [t for conn in self.active_connections.values()
                       for t in conn['threads'].values()]
        return [t for t in threads if t.is_alive()]

    def dispatchers(self):
        """The live per-connection dispatchers, for the last-resort sweep.

        One per connection, matching live_reader_threads() by ownership: a
        reader that never returned cannot run its own release lane, so this is
        the handle the shutdown path force-releases through.
        """
        with self.lock:
            return [conn['action'] for conn in self.active_connections.values()]


def shutdown_readers(manager, budget=SHUTDOWN_JOIN_BUDGET_SECONDS):
    """Owned shutdown (Item 1 / A2): join the recorded readers, then sweep.

    Call after stop_event is set. Every reader handle the manager recorded is
    joined inside ONE total budget (not per thread), so a wedged HID read costs
    at most the budget and never a hang - readers stay daemon threads on
    purpose. Anything still running afterwards can no longer be trusted to run
    its own finally (the release lane at the end of run_interface), so every
    live connection's dispatcher is swept once here: that force-releases the
    modifier or button a stuck reader was holding, which is the stuck-Ctrl after
    quit that Item 1 reports. Since A4 the two release lanes cannot both claim
    the same held input: ActionDispatcher.release_held_inputs drains its registry
    under the per-dispatcher lock, so whichever caller drained a held key is the
    only one that injects its release, and a reader that exited normally
    contributes nothing to the sweep.
    """
    deadline = time.monotonic() + max(budget, 0.0)
    for thread in manager.live_reader_threads():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        thread.join(remaining)

    # Accepted window: a wedged handle at the head can spend the whole budget, so
    # a later reader may get no join time at all. Its held input is still swept
    # here; only input it presses AFTER the sweep can outlive it, and that reader
    # is a daemon the interpreter is free to cut off at exit.
    stragglers = manager.live_reader_threads()
    swept = []
    for dispatcher in manager.dispatchers():
        try:
            released = dispatcher.release_held_inputs()
        except Exception as e:
            _log.info(f"[Shutdown] Dispatcher sweep failed: {e}")
            continue
        swept.extend(released)

    _log.info(f"[Shutdown] Readers joined; still running: {len(stragglers)}. "
          f"Last-resort sweep released: {swept if swept else 'nothing'}")


def build_device_info(conn, base_config, manager):
    """GUI focus bundle for the settings window (U73/U87).

    A None connection keeps the bar's no-device lane: the section still
    builds above the tabs and resolves its focus from the manager's live
    device list (U383 / #383).
    """
    return {"connection": conn, "base_config": base_config, "manager": manager}
