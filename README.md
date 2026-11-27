# Wacom Touch Companion

A lightweight Python driver that unlocks pen, touch input and multi-touch gesture support on Wacom Intuos Pro tablets.
#### **OpenTabletDriver is not longer needed for pen input.**

**Current version: v0.5.0** (November 2026) - since v0.4.0: quit never leaves stuck keys (bounded reader join + last-resort input sweep), Apply Live survives pause and reconnect with per-device persistence, one strict command line (unknown flags refuse to start; every legacy spelling still works), a documented config file search order, console output mirrored to `%APPDATA%\wtd\wtd.log`, per-device override hint + Restore to Defaults, and CI-built release executables on a `v*` tag.

Touch support on certain older Wacom devices requires specialized parsing. This driver captures HID touch reports and maps them to custom mouse cursor movements, multi-finger gestures, continuous scrolling, and Windows productivity shortcuts. Pen input is handled natively as well (cursor movement, pressure clicks, pen button clicks), replacing the external OpenTabletDriver dependency.

---

## 📸 Overview & Interface

| Main menu | Settings: Pen Behavior | Settings: Features & Actions |
| :---: | :---: | :---: |
| <img width="715" height="1653" alt="image" src="https://github.com/user-attachments/assets/9e40eb0c-e42d-4d85-86b9-075f242c7c68" /> | <img width="712" height="1525" alt="image" src="https://github.com/user-attachments/assets/664c0dac-c9a0-4954-9b47-e927ec19e1ef" /> | <img width="692" height="1774" alt="image" src="https://github.com/user-attachments/assets/4391a9c7-7880-43be-a8b3-7ad9b38902e8" /> |



| Tray Icon & Menu | Tablet Rotation Setting | Rotation also translates to Pen Area (multi-display support) |
| :---: | :---: | :---: |
| <img width="216" height="90" alt="grafik" src="https://github.com/user-attachments/assets/e7da3518-93af-4bc6-8d8c-99df95d47244" /> | <img width="627" height="359" alt="image" src="https://github.com/user-attachments/assets/22719475-c579-4968-9400-a6c7f552ce6c" /> | <img width="776" height="470" alt="image" src="https://github.com/user-attachments/assets/e17de153-7469-40b7-9175-758f3a8c7161" /> |

---

## Hardware Support

Specifically designed and tested for the **Wacom Intuos5 / Intuos Pro (PTH-x51)** family:
* **PTH-451** (Intuos Pro Small)
* **PTH-651** (Intuos Pro Medium)
* **PTH-851** (Intuos Pro Large)

*(Other HID-compliant Wacom touch devices may work, but vendor/product IDs might need adjustments).*

---

## Key Features

* **Palm Rejection:** Automatically disables touch input instantly whenever the pen enters proximity/hover range above the tablet surface.
* **Pen Pointer Support:** Built-in pen input without an external driver: cursor movement (absolute tablet-to-screen mapping or relative deltas with speed setting), pen tip pressure behaves like a left mouse button press/release, and the two pen buttons default to right-click and middle-click (customizable). Touch stays suppressed while the pen is active.
* **Accelerated Cursor Motion:** Smooth 1-finger tracking with piecewise linear acceleration and configurable deadzones.
* **Rich Multi-Finger Gestures:**
  * **1-Finger:** Tap, press-and-hold drag, and double-tap drag.
  * **2-Finger:** Tap (Right Click), natural horizontal/vertical scrolling, and pinch-to-zoom (Ctrl + Scroll).
  * **3-Finger & 4-Finger:** Tap, directional swipes (Up/Down/Left/Right), and discrete pinch in/out gestures.
  * **5-Finger:** Smooth Alt-Tab window switcher navigation.
* **System Tray Integration:** Quick access via a dynamic system tray icon (`WT`) with live color indicators (Green = Touch Enabled, Red = Touch Paused - touch gestures/cursor/scroll only: the pen and the tablet buttons keep working while paused) and a quick toggle option. The same touch state is also an assignable action (**Touch toggle**) for any tablet button.
* **GUI Settings Manager:** Built-in multi-tab configuration menu for live tuning.
* **Persistent Configuration:** All settings, gesture toggles, threshold timings, and custom action mappings are saved to your `user.cfg` file (see Configuration File).
* **Per-Device Settings:** Each paired tablet keeps its own values inside one shared `user.cfg` - the Focused Device bar picks the device, and the driver stores identity-keyed overrides per device on top of the global settings. A bad value now costs only its own section: the rest of that device's override still applies, and the error names the section, the key, and - when that key really sits in the saved file's block for the device - its line.
  * **Override hint:** the Focused Device section shows `Device-specific values active: <sections>` - exactly which settings the focused device overrides right now, recomputed on every focus change and apply/save.
  * **Restore to Defaults:** one button clears the focused device's overrides after a confirm dialog naming the sections; the device runs the global values immediately (no restart), and pen-area maps and aliases stay untouched.

---

## Configurable Options

Everything in the driver is exposed and editable via the GUI:

1. **Motion & Speed:** Fine-tune base cursor sensitivity, acceleration curves (`speed_low`, `speed_high`), deadzone thresholds, and natural scroll speeds.
2. **Pen:** Pen movement speed (px/mm for relative deltas), absolute-vs-relative movement toggle, pressure threshold for the left-button hold, the move flush cap (**Move flush Hz**, how often pen cursor updates reach Windows, default 250), and dropdown mapping of the two pen buttons to actions. In the 1:1 pen-area rectangle mode the drawn rectangle may extend beyond the screens - drag, resize, or type values past a monitor edge and it stops only where the last corner would leave the combined display area (the bounding box of all your monitors), because at least one corner must always stay inside it (pen travel in the outside part parks the cursor at the screen edge, so the off-screen part works as a dead zone).
3. **Features & Actions:** Individual check-box toggles for every single gesture type, paired with dropdown selectors to map gestures to standard actions (e.g., Task View, Show Desktop, Alt-Tab, Desktop Switch, Window Maximize/Minimize, plus the clipboard and session entries `copy`, `cut`, `paste`, `lock_workstation` and `escape`).
4. **Thresholds & Advanced:** Adjust physical motion tolerances, tap duration limits, press-and-hold timing, swipe distance limits, and pinch activation limits.

### Clipboard and session actions

The gesture, pen-button and tablet-button dropdowns offer these five options
(the per-button touch strips keep their own modifier list instead: `none`,
`ctrl`, `shift`, `alt`):

| Dropdown entry | What it does |
|---|---|
| `copy` | taps Ctrl + C - copies the current selection |
| `cut` | taps Ctrl + X - cuts the current selection |
| `paste` | taps Ctrl + V - pastes at the cursor |
| `lock_workstation` | locks your signed-in Windows session - the same call the Start-menu Lock entry makes, not a Win + L keystroke (Windows ignores an injected Win + L) |
| `escape` | taps Esc - closes menus and dialogs in many programs |

A tablet button mapped to one of these fires on the press edge and does nothing
on release. `lock_workstation` locks the session at once, so map it where you
will not hit it by accident.

---

## Installation & Setup

### Prerequisites

1. **Python 3.10+**: Ensure Python is installed on your system.
   * Download the latest version from [python.org](https://www.python.org/downloads/).
   * **Important (Windows):** Check the box **"Add python.exe to PATH"** during installation.

2. **OpenTabletDriver (optional):** Pen input is now handled by this companion itself, so OpenTabletDriver is not required for pen use. Install [OpenTabletDriver](https://opentabletdriver.net/) only if you need pen pressure/tilt features beyond pointer control - do not run both pointer paths at once.

### Installation

1. **Clone the repository:**
   ```bash
   git clone https://github.com/wasiejen/OTD-Wacom-Touch-Input-Companion.git
   cd OTD-Wacom-Touch-Input-Companion
   ```

2. **Install dependencies:**

   ```bash
   pip install -r requirements.txt
   ```

3. **Run the driver:**
   ```bash
   python -m wtd
   ```

4. **Verify touch input (optional):**
   ```bash
   python probe/touch_stream_probe.py
   ```
   Needs a connected Wacom tablet — it prints raw touch reports for
   troubleshooting.

### Using the portable Executable

On the Release Page there is now an "OTD_Wacom_Touch_Driver_%VERSION%_win64.exe" to be downloaded, which can be used directly. When saving your configuration the user.cfg will be created in the same folder the executable is residing in - that file keeps being the one the driver reads, so a portable install you already set up is unaffected. Without such a file the driver uses your per-user file instead (see Configuration File).

[Releases](https://github.com/wasiejen/Wacom-Touch-Enabler/releases/)

*(The driver will start listening for tablet touch events and spawn the `WT` tray icon in your taskbar).*

---

## Configuration File (`user.cfg`)

The driver picks one config file at start, in this order (the order is the approved one, estate review Item 5 / plan unit B1):

1. the file named by `--config <path>` on the command line;
2. `user.cfg` in the current folder (the application directory), but only if that file already exists - so an existing portable install keeps reading its own file;
3. your per-user file `%APPDATA%\wtd\user.cfg`. Its folder is created by the first save, so a fresh install needs no setup.

When you hit **Save to File** in the settings GUI, the chosen file is created or updated; nothing else is written. If a `user.cfg` in the current folder and a per-user file both exist with different modification times, the current folder wins and the start prints one `[Config Migration]` line naming both files - the driver never moves or copies your data. The search runs once, at start, so `--save-as` and the GUI always follow the file that was actually loaded.

### Wireless tablet size (`wireless_tablet_size`)

The wireless add-on is one receiver (product id 132) on every tablet size, and it cannot detect which tablet it is paired to. The driver therefore does not guess: the setting names the paired tablet explicitly — `small` (788), `medium` (789), or `large` (791) — under `motion_and_thresholds.wireless_tablet_size` in `user.cfg`, or via the Pen tab's **Wireless Tablet Size** choice in the GUI. While a wireless receiver is connected and no size is chosen, the driver prints a loud notice (console) and shows a red notice row (GUI) naming the three options; until a size is chosen the mapping uses the medium extents as a labeled fallback, never as a silent default. The old key name `wireless_pen_size` (pre-rename configs) is still read and migrated with a notice; the new key wins if both are present. A size change takes effect at the next device reconnect.

### Command line options

Start the driver with `python -m wtd [options]` (the `wtd/` package
entry point; `python touch_controller.py` is gone):

| Option | Effect |
|---|---|
| `-DEBUG` | print raw touch reports on the console |
| `-wireless` | prefer the wireless interface when a tablet exposes both |
| `--config <path>` | load and save `<path>` instead of `user.cfg` |
| `--save-as <path>` | save to `<path>` while loading from `--config` |
| `--list` | print every stored device entry (alias + identity key) and exit |
| `--restore-with <name>` | apply one device's saved values, then re-save the live values |
| `--save-device <alias>` | write the current settings under a new device alias and exit |
| `--elevated` (`-elevated`) | self-elevate at start through one UAC prompt (details below) |

A start argument the driver does not know is a fatal error: it prints one
`[Start Parameter]` line and the driver does not start (previously such an
argument was skipped quietly, and a misspelling such as `-wirelessize` could
switch an option on by matching part of its name). `-h` / `--help` prints the
available start options and exits.

`--elevated` (`-elevated`): self-elevation at startup: if the run is not
elevated yet, the driver relaunches itself through one Windows "run as
administrator" prompt, passing along all other start arguments (the
`--elevated` flag itself is stripped in the relaunched child, so no relaunch
loop can happen), and the first process exits 0. If the run is already
elevated, the driver logs one `[Elevation] running elevated` line and starts
normally. Running from python.exe (dev mode) never attempts a relaunch - it
prints `[Elevation] dev mode - relaunch the exe manually or use scheduled
task` and starts normally.

The driver runs one instance at a time - starting a second one exits with a
notice (it logs `[SingleInstance] another WTD is already running - exiting`
and exits with code 1).

**Prompt-free elevated autostart (scheduled task):** run this ONCE from an
elevated shell: `schtasks /Create /TN "WacomTouchDriver" /TR "C:\path\OTD_Wacom_Touch_Driver.exe" /SC ONLOGON /RL HIGHEST`
(or in Task Scheduler UI check "Run with highest privileges"). The task starts
the driver elevated at every logon with no UAC prompt - registration is the
only elevation moment, and the exe needs no `--elevated` flag there
(`/RL HIGHEST` alone carries the elevation; the flag is for manual/relaunch
use).

Every console line is mirrored to `%APPDATA%\wtd\wtd.log` (UTF-8,
rotating at 2 MB with 2 backups), so a tray start without a console
keeps the same grep-able log. Opt-in diagnostic line outputs (read at
each start): `WTD_METRICS_DIAG=1` prints the one-line desktop-metrics
readout, `WTD_PEN_MAP_DIAG=1` prints the pen absolute-mapping line once
per change.

`--list`, `--restore-with` and `--save-device <alias>` are maintenance lanes: they run
against the config file and exit without starting the driver or the GUI. The
old bare-name alias spelling `--save-as <alias>` still routes to the same lane
but prints a deprecation notice naming `--save-device`; a `--save-as` value that
looks like a file path (a `/` or `\` separator or a file extension) keeps the
save-file lane and is unchanged.
`--restore-with` takes a device alias or its 12-character identity key; the
named device's saved values become the live values, and that device's entry is
then rewritten with the live state.

---

## 🛠️ Tech Stack & Dependencies

* **`hidapi`**: Low-level USB/HID report reading for Wacom Touch/Pen interfaces.
* **`ctypes` SendInput** (Python stdlib): mouse and keyboard event injection via the Windows API.
* **`pystray` & `Pillow (PIL)**`: System tray icon rendering and management.
* **`tkinter`**: Cross-platform graphical user interface.

---

## Development setup

The steps a contributor (or a reviewer) needs to run this repo's suite from a
fresh clone on Windows:

```bash
python -m venv .venv
.venv/scripts/python.exe -m pip install -r requirements-dev.txt
.venv/scripts/python.exe -m pytest tests -q

# Optional lint (CI runs it informationally, so it is not in the install line):
#   .venv/scripts/python.exe -m pip install ruff
#   .venv/scripts/python.exe -m ruff check .
```

* The suite needs the venv: `requirements-dev.txt` pulls `requirements.txt`
  (`hidapi`, `pystray`, `pillow`) plus `pytest`; `requirements-built.txt` is the
  same base plus PyInstaller for the packaging build. `ruff` is not in any
  requirements file: CI installs it to run an informational lint, and the optional
  commands for it are in the block above.
* Run pytest from the repo root. `tests/conftest.py` refuses to collect if a
  `user.cfg` sits in the current folder, so an app config left there stops the
  suite instead of silently overriding the mapping defaults (estate TODO #425).
* The suite is device-free: no tablet is needed and no live input is injected
  (the conftest installs a guard over every Win32 injection entry point).
* Continuous integration runs the same commands on a Windows runner
  (`.github/workflows/ci.yml`); `.github/workflows/release.yml` builds the
  executable with `OTD_Wacom_Touch_Driver.spec` when the maintainer pushes a
  `v*` tag.

---

## License

Distributed under the MIT License. See `LICENSE` for more information.
