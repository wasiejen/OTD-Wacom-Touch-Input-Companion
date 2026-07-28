import sys
import threading
import tkinter as tk
from tkinter import ttk, messagebox
from PIL import Image, ImageDraw, ImageFont
import pystray
from pystray import MenuItem as item


class ToolTip:
    """Hover tooltip for Tkinter widgets with robust cleanup on leave, click, scroll, and window move."""
    def __init__(self, widget, text):
        self.widget = widget
        self.text = text
        self.tip_window = None
        
        # Event bindings on the target widget
        self.widget.bind("<Enter>", self.show_tip)
        self.widget.bind("<Leave>", self.hide_tip)
        self.widget.bind("<ButtonPress>", self.hide_tip)

        # Bind to top-level window move/resize/scroll to close tooltips on window movement
        try:
            top_level = self.widget.winfo_toplevel()
            top_level.bind("<Configure>", self.hide_tip, add="+")
            top_level.bind("<Unmap>", self.hide_tip, add="+")
        except Exception:
            pass

    def show_tip(self, event=None):
        if self.tip_window or not self.text:
            return
        
        # Position below the widget dynamically based on current screen position
        x = self.widget.winfo_rootx() + 15
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 5

        self.tip_window = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        tw.attributes("-topmost", True)

        label = tk.Label(
            tw, text=self.text, justify="left",
            background="#ffffe0", foreground="#000000",
            relief="solid", borderwidth=1,
            font=("Segoe UI", 8, "normal"), padx=6, pady=4
        )
        label.pack(ipadx=1)

    def hide_tip(self, event=None):
        if self.tip_window:
            try:
                self.tip_window.destroy()
            except Exception:
                pass
            self.tip_window = None


def generate_icon_variant(enabled: bool, text: str = "WT", icon_size: int = 64) -> Image.Image:
    border = 2
    inner_size = icon_size - (2 * border)

    image = Image.new("RGBA", (icon_size, icon_size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    color = "#168b47" if enabled else "#9a2619"
    border_color = "#0d7739" if enabled else "#73160c"

    draw.ellipse([border, border, border + inner_size, border + inner_size], fill=color, outline=border_color, width=2)

    font_size = int(inner_size * 0.65)
    font = None
    for font_name in ["arial.ttf", "arialbd.ttf", "DejaVuSans.ttf"]:
        try:
            font = ImageFont.truetype(font_name, font_size)
            break
        except OSError:
            continue

    if font is None:
        font = ImageFont.load_default(size=font_size)

    bbox = draw.textbbox((0, 0), text, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]

    text_x = (icon_size - text_w) // 2 - bbox[0]
    text_y = (icon_size - text_h) // 2 - bbox[1]

    draw.text((text_x, text_y), text, fill="white", font=font)

    return image


class IconCache:
    def __init__(self, text: str = "WT"):
        self.icons = {
            True: generate_icon_variant(enabled=True, text=text),
            False: generate_icon_variant(enabled=False, text=text)
        }

    def get(self, enabled: bool) -> Image.Image:
        return self.icons[enabled]


class ToastNotification(tk.Toplevel):
    def __init__(self, parent, title, message, timeout_ms=1800):
        super().__init__(parent)
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        
        frame = ttk.Frame(self, padding=12, relief="solid", borderwidth=1)
        frame.pack(fill="both", expand=True)
        
        ttk.Label(frame, text=title, font=("Segoe UI", 9, "bold")).pack(anchor="w")
        ttk.Label(frame, text=message).pack(anchor="w", pady=(2, 0))
        
        self.update_idletasks()
        px = parent.winfo_x() + (parent.winfo_width() // 2) - (self.winfo_width() // 2)
        py = parent.winfo_y() + (parent.winfo_height() // 2) - (self.winfo_height() // 2)
        self.geometry(f"+{px}+{py}")
        
        self.after(timeout_ms, self.destroy)


class SettingsWindow(tk.Toplevel):
    def __init__(self, parent, state_ref, config_ref):
        super().__init__(parent)
        self.state = state_ref
        self.config = config_ref

        self.title("Wacom Touch Driver Settings")
        self.geometry("460x650")
        self.minsize(460, 480)

        main_container = ttk.Frame(self)
        main_container.pack(fill="both", expand=True, padx=10, pady=10)

        notebook = ttk.Notebook(main_container)
        notebook.pack(fill="both", expand=True)

        # Tab 1: Motion & Acceleration
        self.tab_motion = ttk.Frame(notebook)
        notebook.add(self.tab_motion, text="Motion & Acceleration")
        self._build_motion_tab()

        # Tab 2: Combined Features & Actions
        self.tab_features_actions = ttk.Frame(notebook)
        notebook.add(self.tab_features_actions, text="Features & Actions")
        self._build_features_actions_tab()

        # Tab 3: Thresholds & Advanced
        self.tab_advanced = ttk.Frame(notebook)
        notebook.add(self.tab_advanced, text="Thresholds & Advanced")
        self._build_advanced_tab()

        # Bottom Button Bar
        btn_frame = ttk.Frame(main_container)
        btn_frame.pack(fill="x", side="bottom", pady=(10, 0))

        ttk.Button(btn_frame, text="Close", command=self.destroy).pack(side="right", padx=(5, 0))
        ttk.Button(btn_frame, text="Save to File", command=self.save_to_file).pack(side="right", padx=5)
        ttk.Button(btn_frame, text="Apply Live", command=self.apply_settings).pack(side="right")

    def _build_motion_tab(self):
        canvas = tk.Canvas(self.tab_motion, borderwidth=0)
        scrollbar = ttk.Scrollbar(self.tab_motion, orient="vertical", command=canvas.yview)
        scroll_frame = ttk.Frame(canvas)

        scroll_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas_window = canvas.create_window((0, 0), window=scroll_frame, anchor="nw")
        canvas.bind("<Configure>", lambda e: canvas.itemconfig(canvas_window, width=e.width))
        canvas.configure(yscrollcommand=scrollbar.set)

        self.entries = {}

        # Tooltips dictionary for motion & acceleration
        cursor_tooltips = {
            "cursor_deadzone": "Minimum pixel delta per packet required before cursor moves. Filters out sensor noise/jitter.",
            "cursor_min_sens": "Base precision multiplier applied at slow finger speeds.",
            "cursor_max_sens": "Maximum speed cap multiplier applied when reaching high finger speeds.",
            "cursor_speed_low": "Finger speed threshold (px/packet) below which base precision multiplier is used.",
            "cursor_speed_high": "Finger speed threshold (px/packet) at which maximum acceleration multiplier is reached."
        }

        scroll_tooltips = {
            "scroll_deadzone": "Minimum pixel delta per packet before scrolling activates. Prevents unwanted drift.",
            "scroll_min_sens": "Base scrolling speed multiplier for slow, precise scrolling.",
            "scroll_max_sens": "Maximum scrolling speed multiplier during fast flicks.",
            "scroll_speed_low": "Lower speed limit (px/packet) for flat precision scrolling.",
            "scroll_speed_high": "Upper speed limit (px/packet) to reach maximum scroll acceleration.",
            "scroll_sensitivity_x": "Horizontal scroll multiplier. (Positive = Natural, Negative = Traditional)",
            "scroll_sensitivity_y": "Vertical scroll multiplier. (Positive = Natural, Negative = Traditional)"
        }

        # --- 1. Cursor Acceleration Frame ---
        cursor_frame = ttk.LabelFrame(scroll_frame, text=" Cursor Acceleration Parameters ", padding=10)
        cursor_frame.pack(fill="x", padx=10, pady=8, expand=True)

        # Feature toggle moved here
        self.cursor_accel_var = tk.BooleanVar(value=self.config.feature_toggles.get("cursor_acceleration", True))
        chk_cursor = ttk.Checkbutton(cursor_frame, text="Enable Cursor Acceleration", variable=self.cursor_accel_var)
        chk_cursor.grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 6))
        ToolTip(chk_cursor, "Toggle non-linear piecewise acceleration curve for cursor movement.")

        cursor_fields = [
            ("Deadzone (px):", "cursor_deadzone", self.config.cursor_deadzone),
            ("Min Sens (Precision Base):", "cursor_min_sens", self.config.cursor_min_sens),
            ("Max Sens (Hard Cap):", "cursor_max_sens", self.config.cursor_max_sens),
            ("Speed Low (px/packet):", "cursor_speed_low", self.config.cursor_speed_low),
            ("Speed High (px/packet):", "cursor_speed_high", self.config.cursor_speed_high),
        ]

        for idx, (label_text, key, initial_val) in enumerate(cursor_fields, start=1):
            lbl = ttk.Label(cursor_frame, text=label_text)
            lbl.grid(row=idx, column=0, sticky="w", pady=4)
            ent = ttk.Entry(cursor_frame, width=12)
            ent.insert(0, str(initial_val))
            ent.grid(row=idx, column=1, sticky="e", pady=4)
            self.entries[key] = ent

            if key in cursor_tooltips:
                ToolTip(lbl, cursor_tooltips[key])
                ToolTip(ent, cursor_tooltips[key])

        cursor_frame.columnconfigure(1, weight=1)

        # --- 2. Scroll Acceleration Frame ---
        scroll_frame_ui = ttk.LabelFrame(scroll_frame, text=" Scrolling & Acceleration Parameters ", padding=10)
        scroll_frame_ui.pack(fill="x", padx=10, pady=8, expand=True)

        # Feature toggle moved here
        self.scroll_accel_var = tk.BooleanVar(value=self.config.feature_toggles.get("scroll_acceleration", True))
        chk_scroll = ttk.Checkbutton(scroll_frame_ui, text="Enable Scroll Acceleration", variable=self.scroll_accel_var)
        chk_scroll.grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 6))
        ToolTip(chk_scroll, "Toggle non-linear piecewise acceleration curve for 2-finger scrolling.")

        scroll_fields = [
            ("Deadzone (px):", "scroll_deadzone", self.config.scroll_deadzone),
            ("Min Sens (Precision Base):", "scroll_min_sens", self.config.scroll_min_sens),
            ("Max Sens (Hard Cap):", "scroll_max_sens", self.config.scroll_max_sens),
            ("Speed Low (px/packet):", "scroll_speed_low", self.config.scroll_speed_low),
            ("Speed High (px/packet):", "scroll_speed_high", self.config.scroll_speed_high),
            ("Sensitivity Horizontal (X):", "scroll_sensitivity_x", self.config.scroll_sensitivity_x),
            ("Sensitivity Vertical (Y):", "scroll_sensitivity_y", self.config.scroll_sensitivity_y),
        ]

        for idx, (label_text, key, initial_val) in enumerate(scroll_fields, start=1):
            lbl = ttk.Label(scroll_frame_ui, text=label_text)
            lbl.grid(row=idx, column=0, sticky="w", pady=4)
            ent = ttk.Entry(scroll_frame_ui, width=12)
            ent.insert(0, str(initial_val))
            ent.grid(row=idx, column=1, sticky="e", pady=4)
            self.entries[key] = ent

            if key in scroll_tooltips:
                ToolTip(lbl, scroll_tooltips[key])
                ToolTip(ent, scroll_tooltips[key])

        scroll_frame_ui.columnconfigure(1, weight=1)

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

    def _build_features_actions_tab(self):
        canvas = tk.Canvas(self.tab_features_actions, borderwidth=0)
        scrollbar = ttk.Scrollbar(self.tab_features_actions, orient="vertical", command=canvas.yview)
        scroll_frame = ttk.Frame(canvas)

        scroll_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas_window = canvas.create_window((0, 0), window=scroll_frame, anchor="nw")

        canvas.bind("<Configure>", lambda e: canvas.itemconfig(canvas_window, width=e.width))
        canvas.configure(yscrollcommand=scrollbar.set)

        self.feature_vars = {}
        self.action_combos = {}
        options = getattr(self.config, "AVAILABLE_ACTIONS", [])

        # Filter out cursor_acceleration & scroll_acceleration since they are in Motion tab
        excluded_features = {"cursor_acceleration", "scroll_acceleration"}

        for feature_key, enabled in self.config.feature_toggles.items():
            if feature_key in excluded_features:
                continue

            row_frame = ttk.Frame(scroll_frame)
            row_frame.pack(fill="x", padx=10, pady=4, expand=True)

            var = tk.BooleanVar(value=enabled)
            chk = ttk.Checkbutton(row_frame, text=feature_key, variable=var)
            chk.pack(side="left", anchor="w")
            self.feature_vars[feature_key] = var

            if feature_key in self.config.action_mapping:
                current_action = self.config.action_mapping[feature_key]
                combo = ttk.Combobox(row_frame, values=options, state="readonly", width=18)
                combo.set(current_action if current_action in options else (options[0] if options else ""))
                combo.pack(side="right", anchor="e")
                self.action_combos[feature_key] = combo

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

    def _build_advanced_tab(self):
        canvas = tk.Canvas(self.tab_advanced, borderwidth=0)
        scrollbar = ttk.Scrollbar(self.tab_advanced, orient="vertical", command=canvas.yview)
        scroll_frame = ttk.Frame(canvas)

        scroll_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas_window = canvas.create_window((0, 0), window=scroll_frame, anchor="nw")

        canvas.bind("<Configure>", lambda e: canvas.itemconfig(canvas_window, width=e.width))
        canvas.configure(yscrollcommand=scrollbar.set)

        advanced_tooltips = {
            "max_tap_duration": "Maximum touch-and-release time (in seconds) to trigger a tap action.",
            "double_tap_timeout": "Time window allowed between double taps to initiate a tap-drag.",
            "tap_max_movement": "Maximum allowed finger drift (in pixels) during a tap contact.",
            "press_hold_duration": "Touch duration required to convert a static contact into a held left-click.",
            "max_gesture_touch_session_duration": "Hard limit on touch session duration for multi-finger gesture classification.",
            "swipe_threshold_x": "Horizontal swipe distance (in Wacom units) required to execute a swipe gesture.",
            "swipe_threshold_y": "Vertical swipe distance (in Wacom units) required to execute a swipe gesture.",
            "axis_dominance_ratio": "Required ratio of primary axis movement vs cross-axis movement to confirm straight swipes.",
            "pinch_continuous_sensitivity": "Distance delta (in pixels) per continuous pinch zoom step.",
            "pinch_discrete_threshold": "Spread change distance needed to trigger discrete multi-finger pinch triggers.",
            "scroll_activation_threshold": "Centroid distance (px) required before 2-finger scrolling locks in.",
            "pinch_activation_threshold": "Finger spread change (px) required before pinch zoom locks in.",
            "alt_tab_activation_threshold": "Distance (px) required to open Alt-Tab overlay with 5 fingers.",
            "alt_tab_step_threshold": "Distance (px) traversed per application icon step in Alt-Tab.",
            "alt_tab_step_sensitivity": "Packet accumulation rate required to increment one step in Alt-Tab."
        }

        advanced_fields = [
            ("Max Tap Duration (s):", "max_tap_duration", self.config.max_tap_duration),
            ("Double Tap Timeout (s):", "double_tap_timeout", self.config.double_tap_timeout),
            ("Tap Max Movement (px):", "tap_max_movement", self.config.tap_max_movement),
            ("Press & Hold Duration (s):", "press_hold_duration", self.config.press_hold_duration),
            ("Gesture Session Duration (s):", "max_gesture_touch_session_duration", self.config.max_gesture_touch_session_duration),
            ("Swipe Threshold X (px):", "swipe_threshold_x", self.config.swipe_threshold_x),
            ("Swipe Threshold Y (px):", "swipe_threshold_y", self.config.swipe_threshold_y),
            ("Axis Dominance Ratio:", "axis_dominance_ratio", self.config.axis_dominance_ratio),
            ("Pinch Sens (Continuous):", "pinch_continuous_sensitivity", self.config.pinch_continuous_sensitivity),
            ("Pinch Threshold (Discrete):", "pinch_discrete_threshold", self.config.pinch_discrete_threshold),
            ("Scroll Activation Threshold:", "scroll_activation_threshold", self.config.scroll_activation_threshold),
            ("Pinch Activation Threshold:", "pinch_activation_threshold", self.config.pinch_activation_threshold),
            ("Alt-Tab Activation Threshold:", "alt_tab_activation_threshold", self.config.alt_tab_activation_threshold),
            ("Alt-Tab Step Threshold:", "alt_tab_step_threshold", self.config.alt_tab_step_threshold),
            ("Alt-Tab Step Sensitivity:", "alt_tab_step_sensitivity", self.config.alt_tab_step_sensitivity),
        ]

        frame = ttk.LabelFrame(scroll_frame, text=" Advanced Thresholds & Timings ", padding=10)
        frame.pack(fill="x", padx=10, pady=10, expand=True)

        for idx, (label_text, key, initial_val) in enumerate(advanced_fields):
            lbl = ttk.Label(frame, text=label_text)
            lbl.grid(row=idx, column=0, sticky="w", pady=4)
            ent = ttk.Entry(frame, width=12)
            ent.insert(0, str(initial_val))
            ent.grid(row=idx, column=1, sticky="e", pady=4)
            self.entries[key] = ent

            if key in advanced_tooltips:
                ToolTip(lbl, advanced_tooltips[key])
                ToolTip(ent, advanced_tooltips[key])

        frame.columnconfigure(1, weight=1)

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

    def apply_settings(self):
        try:
            # 1. Update individual numeric variables
            for key, ent in self.entries.items():
                val = float(ent.get())
                setattr(self.config, key, val)

            # 2. Re-sync sensitivities dictionaries for active driver calculations
            self.config.sensitivities["cursor_acceleration"] = [
                self.config.cursor_deadzone,
                self.config.cursor_min_sens,
                self.config.cursor_max_sens,
                self.config.cursor_speed_low,
                self.config.cursor_speed_high,
                1, 1
            ]

            self.config.sensitivities["scroll_acceleration"] = [
                self.config.scroll_deadzone,
                self.config.scroll_min_sens,
                self.config.scroll_max_sens,
                self.config.scroll_speed_low,
                self.config.scroll_speed_high,
                self.config.scroll_sensitivity_x,
                self.config.scroll_sensitivity_y
            ]

            # 3. Acceleration Feature Toggles
            self.config.feature_toggles["cursor_acceleration"] = self.cursor_accel_var.get()
            self.config.feature_toggles["scroll_acceleration"] = self.scroll_accel_var.get()

            # 4. Standard Feature Toggles
            for feat, var in self.feature_vars.items():
                self.config.feature_toggles[feat] = var.get()

            # 5. Action Mappings
            for feat_key, combo in self.action_combos.items():
                self.config.action_mapping[feat_key] = combo.get()

            ToastNotification(self, "Success", "Settings applied directly to driver!", timeout_ms=1500)
        except ValueError as e:
            messagebox.showerror("Invalid Input", f"Please enter valid numeric values.\nDetails: {e}")

    def save_to_file(self):
        self.apply_settings()
        self.config.save_to_file("user.cfg")
        ToastNotification(self, "File Saved", "Configuration saved to user.cfg", timeout_ms=1800)


class DriverTrayApp:
    def __init__(self, root, state_ref, config_ref):
        self.root = root
        self.state = state_ref
        self.config = config_ref
        self.icon = None
        self.settings_window = None

        self.icon_cache = IconCache(text="WT")

    def toggle_touch(self, icon=None, item=None):
        self.state.touch_paused = not self.state.touch_paused
        status = "Paused" if self.state.touch_paused else "Active"
        print(f"[Tray] Touch input is now {status} (touch_paused = {self.state.touch_paused})")
        self.update_icon()

    def is_touch_enabled(self, item=None):
        return not self.state.touch_paused

    def update_icon(self):
        if self.icon:
            is_enabled = not self.state.touch_paused
            self.icon.icon = self.icon_cache.get(is_enabled)
            self.icon.title = f"Wacom Touch Driver ({'Disabled' if self.state.touch_paused else 'Enabled'})"

    def open_settings(self, icon=None, item=None):
        self.root.after(0, self._show_settings_gui)

    def _show_settings_gui(self):
        if self.settings_window is not None and self.settings_window.winfo_exists():
            self.settings_window.lift()
            return
        self.settings_window = SettingsWindow(self.root, self.state, self.config)

    def quit_driver(self, icon=None, item=None):
        print("[Tray] Exiting Wacom Touch Driver...")
        self.state.running = False
        if self.icon:
            self.icon.stop()
        self.root.after(0, self.root.quit)

    def run_tray(self):
        menu = pystray.Menu(
            item('Enable Touch Input', self.toggle_touch, checked=self.is_touch_enabled, default=True),
            item('Settings...', self.open_settings),
            pystray.Menu.SEPARATOR,
            item('Exit Driver', self.quit_driver)
        )

        initial_state = not self.state.touch_paused
        self.icon = pystray.Icon(
            "WacomTouchDriver",
            self.icon_cache.get(initial_state),
            "Wacom Touch Driver",
            menu
        )
        self.icon.run()


def launch_gui(state_ref, config_ref):
    root = tk.Tk()
    root.withdraw()

    app = DriverTrayApp(root, state_ref, config_ref)
    tray_thread = threading.Thread(target=app.run_tray, daemon=True)
    tray_thread.start()

    root.mainloop()