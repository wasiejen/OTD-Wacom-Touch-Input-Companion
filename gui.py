import sys
import threading
import tkinter as tk
from tkinter import ttk, messagebox
from PIL import Image, ImageDraw, ImageFont
import pystray
from pystray import MenuItem as item


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

    #draw.text((text_x + 2, text_y + 2), text, fill=(0, 0, 0, 180), font=font)
    draw.text((text_x, text_y), text, fill="white", font=font, bold=True)

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
        self.geometry("400x1100")
        self.minsize(400, 1100)

        main_container = ttk.Frame(self)
        main_container.pack(fill="both", expand=True, padx=10, pady=10)

        notebook = ttk.Notebook(main_container)
        notebook.pack(fill="both", expand=True)

        # Tab 1: Motion & Speed
        self.tab_motion = ttk.Frame(notebook)
        notebook.add(self.tab_motion, text="Motion & Speed")
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
        frame = ttk.LabelFrame(self.tab_motion, text=" Cursor & Acceleration Parameters ", padding=10)
        frame.pack(fill="x", padx=10, pady=10)

        self.entries = {}
        fields = [
            ("Cursor Deadzone (px):", "cursor_deadzone", self.config.cursor_deadzone),
            ("Min Sensitivity (Base):", "cursor_min_sens", self.config.cursor_min_sens),
            ("Max Sensitivity (Cap):", "cursor_max_sens", self.config.cursor_max_sens),
            ("Speed Low (px/packet):", "speed_low", self.config.speed_low),
            ("Speed High (px/packet):", "speed_high", self.config.speed_high),
            ("Scroll Sens Vertical:", "scroll_sensitivity_y", self.config.scroll_sensitivity_y),
            ("Scroll Sens Horizontal:", "scroll_sensitivity_x", self.config.scroll_sensitivity_x),
        ]

        for idx, (label_text, key, initial_val) in enumerate(fields):
            ttk.Label(frame, text=label_text).grid(row=idx, column=0, sticky="w", pady=4)
            ent = ttk.Entry(frame, width=12)
            ent.insert(0, str(initial_val))
            ent.grid(row=idx, column=1, sticky="e", pady=4)
            self.entries[key] = ent

        frame.columnconfigure(1, weight=1)

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

        for feature_key, enabled in self.config.feature_toggles.items():
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
            ttk.Label(frame, text=label_text).grid(row=idx, column=0, sticky="w", pady=4)
            ent = ttk.Entry(frame, width=12)
            ent.insert(0, str(initial_val))
            ent.grid(row=idx, column=1, sticky="e", pady=4)
            self.entries[key] = ent

        frame.columnconfigure(1, weight=1)

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

    def apply_settings(self):
        try:
            # Motion & Advanced Numeric Entries
            for key, ent in self.entries.items():
                val = float(ent.get())
                setattr(self.config, key, val)

            # Feature Toggles
            for feat, var in self.feature_vars.items():
                self.config.feature_toggles[feat] = var.get()

            # Action Mappings
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