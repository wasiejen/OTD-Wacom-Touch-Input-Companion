# Changelog

All notable changes are documented here. Entries are development timestamps
(`YYMMDD-HHMM`, newest first), kept verbatim from the work log. Release tags
on GitHub mark shipped states — `v0.2.2` is the latest release.

261103-0448:

- per-model pen tables structure (TODO #311, research Draft A):
  `PEN_SPECS_BY_PRODUCT` now carries explicit per-model entries keyed by the
  wired product id. 789 (PTH-651, Medium) is pinned unchanged from upstream
  Wacom/PTH-651.json. 788 (PTH-451, Small) and 791 (PTH-851, Large) are
  explicit PENDING entries (value `None`): their real digitizer extents are
  not recorded in this repo, the git history never carried them, and the
  upstream datasheet values returned 404 at implementation time — no
  numbers are invented. The real extents land as a maintainer-hardware
  input item; filling an entry routes that model to its own table with no
  resolver change.
- the fallback path prints ONE byte-stable named line for a product with no
  table: `pen_specs= no-table product=<id> using=medium` (replaces the old
  "[Pen] No digitizer ranges for product ...; using Intuos Pro Medium
  defaults" line). Medium behavior is unchanged; relative speed scaling,
  rotation center and rotated-extent math all read the resolved specs dict
  untouched.
- Device-free pins: known id routes to its own table quietly; pending ids
  (788/791/132) and unknown ids print the named line byte-exact. RED-before
  measured: 3 pins FAIL pre-change (old line shape + KeyError).

- import-order fix (latent NameError at module import, TODO #311 lead):
  the module-level `config = DriverConfig()` runs at import and validates
  `orientation_degrees`, which referenced `ORIENTATION_MAX_DEGREES` before
  the constant was defined below the class. With a repo-root `user.cfg`
  carrying an orientation key, importing the module raised NameError and
  the whole test suite failed collection (16 collection errors, measured
  pre-fix). The four U74 rotation constants (TOUCH_COORD_MAX,
  TOUCH_ROTATION_CENTER, ORIENTATION_MIN/MAX_DEGREES) moved above the
  config class; values, comments and behavior unchanged.

261031-0720:

- config load atomicity and validation (TODO #254): `load_from_file`
  validates the WHOLE file via `validate_config_data` before applying —
  an offending key applies nothing, defaults stay intact ("Nothing
  applied"). Each bad key or action names its section path and file line;
  `action_mapping` values are checked against `AVAILABLE_ACTIONS` and an
  invalid action is rejected by name, never applied (no per-gesture
  runtime warnings). Unknown sections, motion keys, toggles and gestures
  warn by name and are ignored; the load still succeeds. Device lanes
  (`device_alias_`/`device_settings_`) validate the same way, errors name
  the lane. Valid files round-trip with the U64 save side.
- Device-free tests: corrupt JSON syntax keeps defaults; multiple bad keys
  each named; invalid action inside a device lane names the lane; alias
  and settings lanes round-trip.

261031-0530:

- pen absolute extent at rotated orientation (TODO #274): absolute mode
  scaled rotated coordinates with the fixed digitizer `max_x`/`max_y`, so a
  non-square tablet (PTH-651 44704x27940) swapped extents at 90/270 and the
  pen cursor covered only the inner desktop band.
- Absolute mode now maps the ROTATED digitizer bounding box (new
  `rotated_pen_extents`) onto the full virtual desktop; whole-quadrant
  cos/sin artifacts are snapped so box corners land exactly on full extent.
  0° behavior is byte-identical; relative mode and touch are unchanged.
- Device-free projection test pins the 90/270 corner mapping.

261031-0413:
- config CLI lanes (approved packet Part C): `--list` prints every stored device
  entry (alias plus identity key) and exits — no driver, no GUI.
  `--restore-with <name>` applies one device's saved values (alias or identity
  key), then re-saves the live values under that key: option B, the file entry
  mirrors the live state after a restore. `--save-as <alias>` writes the
  current settings under a new device alias and exits.
- A `--save-as` value with a path separator or a file extension keeps the
  Part A save-file lane; any other value names a device alias. Without a lane
  flag the driver starts exactly as before.

261031-0406:
- device identity (approved packet parts a, b, e): one interface's identity key
  is a 12-hex sha1 over its full HID interface path plus its serial string,
  never a bare interface index. The device bar feeds a focused-device combo on
  the live connection list; unnamed devices show their interface name.
- Alias lane in `user.cfg`: `device_alias_<hash>` stores the user name of one
  device; assigning an alias renames the focused device and reloads it on the
  next start. Per-device lane `device_settings_<hash>` carries motion, toggle
  and mapping sections; those values overlay the alias entry, global values
  stay the fallback default.
- "Save to Device" button on the device bar writes ONLY the focused device's
  current values under its identity key; the main Save button is unchanged.

261031-0315:
- device orientation rotation (approved packet a,b,c,d): `orientation_degrees`
  (0..359, 1° steps) rotates touch contacts and pen points about the tablet
  center through one shared sin/cos transform before the gesture/cursor path;
  0° is an exact passthrough and leaves all semantics untouched.
- Motion tab: direct numeric entry, presets 0/90/180/270, and a Tk-drawn
  tablet pictogram (central round + dial ring, keys 1-4 above, 5-8 mirrored
  below) that rotates with the angle; value round-trips through user.cfg.
- pen tilt ignored fully per ruling; pen absolute-mode screen area mapping at
  90°/270° covers less than the full desktop (tracked as follow-up).

261031-0237:
- pen support inside the companion (approved packet a,b,c): full 10-byte 0x02 pen
  report parsing per the documented Intuos-V1 layout (status byte switches bytes
  6-7: rotation reports carry pressure over, bit-5 tablet reports decode pressure).
- cursor movement through SendInput: absolute tablet-to-virtual-desktop mapping
  or relative deltas with a speed setting; pressure = left-button press/release,
  pen buttons default right+middle click and stay customizable; tilt ignored.
- new GUI tab "Pen" (speed, absolute/relative toggle, pressure threshold, button
  mapping); pen settings round-trip through user.cfg.
- device verification pending: no tablet in CI, maintainer run required.


260805-1328:
- fixed not working touch_paused function.
- introduced events for 
    - touch_enabled_event to use the wait function to pause the device manager 
    - stop_event to replace global variable running
- fixed bug that would lead to program not closing correctly due to incorrect usage of event.wait() that caused busy polling and then getting stuck in wait
- general code cleanup


260805-1146:
- device manager class that runs it own thread and manages the devices and interfaces and populates them with threads.
    - thus automatic discovery and connection to new devices and changing devices
    - significantly simplified interface threads to migration of the reconnect and sync logic to the device manager class


260731-1054:
- added support for the wireless receiver. will now automatically recogniced.
- cable connection will be prefered if both cable and wireless receiver are connected at the same time
- when using 2 wacom pads at the same time it should now check if the touch interface is not already blocked by another device and will connect to the first pad with non blocked touch input 
- added start parameter when starting the program via console
    - `-wireless` prefers wireless connection instead of default cable connection
        - but keep in mind that wireless connection is not supported by OpenTabletDriver
    - `-debug` activated debug output

- added ability to connect to multiple wacom pads at the same time and implemented some a bit more smart reconnect mechanism
    - rearranged some code into new classes to support multiple independent wacom touch devices at the same time without influencing each other
        - all use the same config setting though
    - used with start parameter `-connectall`


idea:
- if wireless connection is established the buttons are not controlled via OpenTabletDriver and thus can be freely used.
- could i even implement a basic relative pen movement and thus replacing the need for OTD completely for pen input?
    - movement + sensitivity
    - pen click and hold behaviour
    - pen buttons
    - tablet buttons and rotary control
    (i personally do not need pressure or tilt functionality)

idea2: 
- to implement a manager thread that simply manages the connections and has an overview of what interfaces are present and regularly looks for changes and starts new interface threads as needed.
    - the current interface thread can be heavily simplified because they do not have to hold the reconnect code and try to synchronise with each other without knowing about other threads itself


260728-2031:
- replaced pynput completely with SendInput via WIN32.dll and CTYPES.
- added vk_codes dict as file (maybe a bit too much :_) )
- introduced action dispatcher to get rid of the elif execute destinction
- added actions for media keys
- changed pinch sensitivity behavior - now a multiplier instead of step change
- implemented continues zoom - ctrl now is only pressed on start of 2f_pinch and released when finger count changes again - thus a very smooth zoom :-)

260728-0949:

- with each reconnection attempt now searches for changed hid interfaces to reconnect to the currently connected device.
    - so hot swap of the drawing tablet is now possible without restarting the program :-)

- updated gui (via AI)
    - display both set of motion parameters
    - added a tooltip functionality for motion and threshold parameters
    - scolling function to easier access to feature toggles farther down the list

260727-2326:

- update interaction of pen hover touch deactivation. before it stopped tracking of touch packets completely which lead to a lost state if any finger were on the drawing tablet when pen hover ended. thus a not known state the touch input was in.
    - now it tracks the state even if pen in hover distance but does not run the evaluation for any action. so it will always start in a known state and even can instantly scoll or zoom if 2 fingers are already on the pad, or move the cursor with one finger instandly without first lifting.
- changed cursor movement from pynput to CTYPES to force windows to recognise the cursor as a mouse and thus provide context sensitive hover information which are supressed for touch input normally.
- added a second set of sensitivities for scrolling to adjust the acceleration curve, because duo to the change to CTYPES for the cursor movement the sensitivity for the cursor changed a lot
    - gui change still needs to be done #XXX


bug:
- after 2-3 seconds of no input, 1f_tap. 2f_tap (and the other taps likely) are not working. 2f_press works. seems that the first packets are not registered correctly. 
    - the drawing tablet is just not sending any packets until a least some contact time and thus short interactions will not be registered. thus is somewhat annoying
    - tested blocking and non blocking packet reception but both behave the same way

260727-1911:

- 2f_pinch zoom now works a bit smoother - the same assumption was present as with scolling functionality.
- minor adjustments to distinquishing between scolling and zooming to work more predictable.

260727-1742:
- smoother scrolling - removed assumption that only full integers could be applied - led to jumpy discreet scroll increaments instead of smooth scrolling
    - now also additionally uses the acceleration function of the cursor movement :-)
- made 1f_press and 1f_double_tap now configurable
- added gesture support to select "left_hold"
- if any gesture is used for action "left_hold" on 0 touch contact the corresponding "left_hold_release will automatically triggered.
- added feature toggles for "cursor_acceleration" and "scrolling_acceleration"
    "cursor_acceleration" - if disabled the base sensitivity setting will be used intepdent of cursor speed
    "scrolling_acceleration" if disabled will fall back to scolling sensitivity setting without prior application of acceleration function

-version change to 0.2.1

260727-1452:
-version change to 0.2

260727-1438:

- implemented press actions for all numbers of fingers. 
- included option to use 5 finger gestures instead of 5 finger alt tabbing 
    - as soon as "f5_alt_tap" is enabled it is replacing/disabling all other 5 finger actions (taps, press, up, down, left, right, pinch_in and out)
- removed batching of inputs and thus reduced input latency and made the state behavior more deterministic (before sometimes some contact increase and releases got batched together and thus the intermediate contact state was lost)
- included more predefined actions like "prev", "next", "undo" and "redo"
- adjusted default cursor sensitivity and accelerations variables
- gestures now can only be triggered when not finger was in contact before and a new config variable, controls how long after initial touch contact a gesture can be triggered: "max_gesture_touch_session_duration = 0.300"
- added a config variable for "5f_alt_tap" to control the sensitivity: "alt_tab_step_sensitivity = 0.2"
- fixed a lot of behavior issues in general
    - when changing touch contact numbers (fingers) and made it a bit more easy to not fall into a state when no action can be taken and all fingers must be lifted again to reset everything. (e.g. now stepping down from multitouch and not gesture was triggered to 1f enables move again)
    - cursor jumping when last finger was not the first finger in contact

remaining bugs:
- touch input not working or only partly working when the task manager window is active
    - running as admin fixes the issue

- touch input does not work when windows admin request menu is open
    - pynput mouse controller seems to not get any current position data to move the mouse relative with the provided deltas.
    - pynput throw as NoneType TypeError in a place I can not fix it.
    - running as admin does not change the behavior

260727-0010:

- interface hooks now try to reconnect to interfaces every 5 seconds if connection was lost due to removed device. hibernation, etc.
    - when touch is paused, reconnection attempts are also paused until reactiviation of touch input via conext menu
- after 60 attempts to reconnect (300 seconds) touch input will automatically paused and needs to be reenabled via conetxt menu to restart reconnections attempts

260726-1026:

- reenabled blocking mode of touch input reading - I do not know why this was disabled.

- Bug:
    - when windows (sleeps?) or hibernates on restart the program is still active but no input is recognized
        - when disconnecting the drawing tablet an "read error" exception is thrown for both threads for 
            - do i also get a read error for hibernation?
        - potential fix without reading any state information of OS: set a variable that keeps track of the current time and compare it to the last current time. if delta is larger than some seconds - which means that the current time could not be fetched due to hibernation or other sleep state - the thread can recognise a previous sleep state and starts the connection anew
            - then it should try for some time to reestablish connection and if "read error" percists then pause threads?/program?/ or just try every some seconds again?
                maybe show an option the restart interface hooks in the context menu? or add this as a part of the pause touch input option - thus if unpaused the interface hooks will always be restarted. (seems like a clean way to manually restart interfaces)

    - when a admin right request pops up the touch input stops while and also after it disappears.
        - runing as admin does not help here


    
    - double tap to drag is skipping back to the original position after letting go
        - not working correctly in general because 1f_tap has priority to have smooth interaction and thus it is always first recognized as a double_tap by windows
        - replace it with something like 2f_hold_drag?
            - if 2f in contact and movement is detected about a limit then grap and release as soon as less than 2f are in contact and continue with normal move
            - (might be really well because no taping needed for movement and dragging or selection action any more)
    
    - when holding one finger and then change to 3f contact, sometimes a gesture will be fired immediately because the location of the centoid changed enough. centoid should be reset with every change in number of finger count.

- what happens when connections fails and is reestablished? autoreconnect and restarts of the threads?
- scrolling speed of 5f_alt_tap is a bit high
    - higher speed limit might not help, but to only register every second or third input as valid to reduce the high sensitivity due to the amount of movement packets incoming

260724-2346

- fixed f5_alt_tap ending that was not triggered
- fixed f5_alt_tap jumping to the next when ended and thus selecting wrong window to focus on
    - fixed it a second time because there were 2 causes for this potential behavior xD --> :-)
- added "windows + direction keys" as option to move windows with 3 finger gestures (deactivate windows window snap suggestions to not be interrupted every time)


260724-1820 (KI generated):

### 🎨 Settings GUI & System Tray Integration

**Overview:**
Added a full-featured Tkinter GUI and a `pystray` system tray app (`WTD`) to allow live tuning of motion parameters, feature toggles, action mappings, and advanced thresholds without restarting the driver.

#### Key Features & Improvements

* **Combined Features & Actions View:** Unified feature toggles and customizable action dropdowns into a single scrollable tab. Unmapped features dynamically display as toggleable rows.
* **Thresholds & Advanced Settings:** Exposed all remaining timing, swipe, pinch, and activation threshold parameters from `DriverConfig`.
* **Persistent Settings (`user.cfg`):** Added a "Save to File" option to auto-load and persist configuration settings across sessions using JSON.
* **Dynamic Tray Icon:** High-DPI pre-rendered tray icon featuring a centered "WTD" badge with drop shadow and dynamic color state indicators (Green = Active, Red = Paused).
* **Non-Blocking Notifications:** Replaced modal alert dialogs with auto-dismissing toast popups for seamless workflow updates.

#### 🛠️ Challenges & Solutions

* **Issue: State Mutability Across Threads (Pause Toggle / Unapplied Settings)**
* *Problem:* Thread boundary issues and duplicate `state`/`config` module imports caused the GUI and tray callbacks to modify isolated memory copies, ignoring pause toggles and settings changes.
* *Solution:* Refactored `launch_gui(state, config)` to explicitly pass live singleton references directly into the GUI and tray manager. Added immediate batch clearing on pause to discard buffered contacts instantly.


* **Issue: Missing GUI Controls & Scrolling Layout**
* *Problem:* Fixed-height window frames caused action buttons ("Apply", "Save", "Close") and scrollable canvas elements to cut off.
* *Solution:* Redesigned the layout hierarchy using packed main container frames with explicit dynamic width binding on canvas configure events.


* **Issue: PyInstaller Missing `hidapi` C-Extensions**
* *Problem:* PyInstaller failed to bundle dynamic C-extension files (`hid.pyd`) from the virtual environment, leading to `ModuleNotFoundError: No module named 'hid'` when running compiled `.exe` files.
* *Solution:* Added explicit binary data mapping (`--add-data "../.venv/Lib/site-packages/hid.cp312-win_amd64.pyd;."`) and hidden imports for `pynput` and `pystray` backend hooks.

260724-1500 (mostly KI generated):

features_implemented:
- continuous 2-finger pinch-to-zoom:
    - calculates spread change relative to contact start to stream Ctrl + Scroll step events
- discrete 3-finger and 4-finger pinch gestures:
    - mapped 3F/4F pinch-in and pinch-out triggers (e.g., window minimize/maximize)
- 2-finger scroll vs. pinch differentiation:
    - added activation threshold checks to latch state exclusively into either 2F_SCROLL or 2F_PINCH on initial movement, preventing accidental zoom while scrolling
- continuous 5-finger alt-tab window switcher:
    - implemented continuous horizontal 5-finger sliding to cycle forward (Tab) and backward (Shift+Tab) through open windows
    - integrated Windows sticky task switcher (Ctrl + Alt + Tab) with Enter on finger release for rock-solid window switching without key-state drops
- piecewise linear cursor acceleration curve:
    - added configurable deadzone filtering for micro-jitter suppression
    - implemented flat base precision zone (min sensitivity), linear ramp up to high speed, and a hard upper cap (max sensitivity) with fractional sub-pixel error accumulation

bugs_fixed:
- 2-finger gesture lockout on latch:
    - fixed continuous execution bug where setting active_gesture immediately blocked subsequent frame updates; separated threshold evaluation from continuous stream execution
- synthetic alt key release in window switcher:
    - resolved issue where holding virtual Alt via pynput caused the window preview overlay to flash and close instantly by switching to Windows Sticky Alt-Tab (`Ctrl + Alt + Tab`) + `Enter` commit logic
- high-speed cursor acceleration drift:
    - replaced non-linear exponential speed scaling with a bounded piecewise linear model (flat low speed -> linear ramp -> hard cap) to ensure predictable cursor targeting on large tablet surface

tested but removed:
- dynamic gesture action mapping:
    - supported dynamic lambda expressions in gesture config for context-aware actions
    - experiented with it, but design wise it clashes with the current way to assign actions

260724-1020:

- blocking interface read for touch
    - tested if buttons are impacted, but still work - so great

260724-0011:

ideas:
- automaically deactivate touch after 15 minutes of no input except 0x81 status messages
    - or reduce polling rate to 10/s to reduce load
- needs a tray icon that shows state of activitiy
    - tray icon click should enable and disable it
- tray icon with context menu to set basic settings, like sensitivity
- 3-finger gestures for window navigation might be a nice extra
    - similar to 2 finger scrolling - same centois logic but with 3 points and as soon as not 3 points are there anymore the gesture ends and thus prevents jumping of the centoid as soon as one finger is lifted.
- 2 finger pinch to zoom is still missing.


bugs?
- double tap to drag seems after release with another double tap to end?
