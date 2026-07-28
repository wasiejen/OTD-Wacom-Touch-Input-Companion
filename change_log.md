
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
