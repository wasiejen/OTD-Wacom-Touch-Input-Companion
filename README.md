# OpenTabletDriver - Wacom Touch Companion

A lightweight Python driver companion for **OpenTabletDriver (OTD)** that unlocks touch input and multi-touch gesture support on Wacom Intuos Pro tablets. 

While OpenTabletDriver excels at handling pen input, touch support on certain older Wacom devices requires specialized parsing. This background driver runs alongside OTD to capture HID touch reports and map them to custom mouse cursor movements, multi-finger gestures, continuous scrolling, and Windows productivity shortcuts.

---

## 📸 Overview & Interface

| Settings: Motion & Speed | Settings: Features & Actions |
| :---: | :---: |
| <img width="301" height="564" alt="grafik" src="https://github.com/user-attachments/assets/ac5b79c7-7b56-40b2-bd51-4f8210ae32d6" /> | <img width="301" height="564" alt="grafik" src="https://github.com/user-attachments/assets/8211b4a4-200f-49b3-b2ac-61bba4a66d05" /> |



| Settings: Thresholds & Advanced | Tray Icon & Menu |
| :---: | :---: |
| <img width="301" height="564" alt="grafik" src="https://github.com/user-attachments/assets/dab487b9-977b-423d-a319-351a61fd9abe" /> | <img width="216" height="90" alt="grafik" src="https://github.com/user-attachments/assets/e7da3518-93af-4bc6-8d8c-99df95d47244" /> |

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
* **Accelerated Cursor Motion:** Smooth 1-finger tracking with piecewise linear acceleration and configurable deadzones.
* **Rich Multi-Finger Gestures:**
  * **1-Finger:** Tap, press-and-hold drag, and double-tap drag.
  * **2-Finger:** Tap (Right Click), natural horizontal/vertical scrolling, and pinch-to-zoom (Ctrl + Scroll).
  * **3-Finger & 4-Finger:** Tap, directional swipes (Up/Down/Left/Right), and discrete pinch in/out gestures.
  * **5-Finger:** Smooth Alt-Tab window switcher navigation.
* **System Tray Integration:** Quick access via a dynamic system tray icon (`WTD`) with live color indicators (Green = Active, Red = Paused) and a quick toggle option.
* **GUI Settings Manager:** Built-in multi-tab configuration menu for live tuning.
* **Persistent Configuration:** All settings, gesture toggles, threshold timings, and custom action mappings are saved to a local `user.cfg` file.

---

## Configurable Options

Everything in the driver is exposed and editable via the GUI:

1. **Motion & Speed:** Fine-tune base cursor sensitivity, acceleration curves (`speed_low`, `speed_high`), deadzone thresholds, and natural scroll speeds.
2. **Features & Actions:** Individual check-box toggles for every single gesture type, paired with dropdown selectors to map gestures to standard actions (e.g., Task View, Show Desktop, Alt-Tab, Desktop Switch, Window Maximize/Minimize).
3. **Thresholds & Advanced:** Adjust physical motion tolerances, tap duration limits, press-and-hold timing, swipe distance limits, and pinch activation limits.

---

## Installation & Setup

### Prerequisites

1. **Python 3.10+**: Ensure Python is installed on your system.
   * Download the latest version from [python.org](https://www.python.org/downloads/).
   * **Important (Windows):** Check the box **"Add python.exe to PATH"** during installation.

2. **OpenTabletDriver**: Make sure [OpenTabletDriver](https://opentabletdriver.net/) is installed and configured for your pen input.

### Installation

1. **Clone the repository:**
   ```bash
   git clone [https://github.com/YOUR_USERNAME/YOUR_REPOSITORY_NAME.git](https://github.com/YOUR_USERNAME/YOUR_REPOSITORY_NAME.git)
   cd YOUR_REPOSITORY_NAME
   ```

2. **Install dependencies:**

   ```bash
   pip install -r requirements.txt
   ```

3. **Run the driver:**
   ```bash
   python touch_controller.py
   ```

### Using the portable Executable

On the Release Page there is now an "OTD_Wacom_Touch_Driver_%VERSION%_win64.exe" to be downloaded, which can be used directly. When saving your configuration the user.cfg will be created in the same folder the executable is residing in.

[Releases](https://github.com/wasiejen/Wacom-Touch-Enabler/releases/)

*(The driver will start listening for tablet touch events and spawn the `WT` tray icon in your taskbar).*

---

## Configuration File (`user.cfg`)

When you hit **Save to File** in the settings GUI, a `user.cfg` file is automatically created or updated in the application directory. On startup, the driver automatically loads your saved configuration from this file.

---

## 🛠️ Tech Stack & Dependencies

* **`hidapi`**: Low-level USB/HID report reading for Wacom Touch/Pen interfaces.
* **`pynput`**: High-precision mouse and keyboard event synthesis.
* **`pystray` & `Pillow (PIL)**`: System tray icon rendering and management.
* **`tkinter`**: Cross-platform graphical user interface.

---

## License

Distributed under the MIT License. See `LICENSE` for more information.

```

```
