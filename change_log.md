260724-1820 (KI generated):

Here is a clean, structured summary you can drop directly into your `CHANGELOG.md` or release history to document the development of the GUI!

---

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
