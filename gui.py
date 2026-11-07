import sys
import threading
import tkinter as tk
from tkinter import ttk, messagebox
from PIL import Image, ImageDraw, ImageFont
import pystray
from pystray import MenuItem as item

from touch_controller import (
    ORIENTATION_MAX_DEGREES,
    rotate_point,
    snapshot_device_sections,
)


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


INVALID_ACTION_PREFIX = "! invalid: "

# Sections a focused device transfers to another alias (U73 d).
DEVICE_TRANSFER_SECTIONS = ("motion_and_thresholds", "feature_toggles", "action_mapping")

# No live connection when the window was built (U383): the section still
# shows above the tabs and its focus is picked up from the live list.
NO_DEVICE_LABEL = "No device connected yet"


def collect_config_sections(config, section_names):
    """Snapshot the named sections of a live config instance as plain dicts."""
    return snapshot_device_sections(config, section_names)


def device_display_name(conn):
    """Device-bar label: the user alias when set, else the interface name (U87 a)."""
    alias = conn.get("alias")
    if alias:
        return alias
    return conn.get("product_string") or "unknown device"


# Touch button and dial controls (U71): one toggle plus one mapping per control.
BUTTON_CONTROL_KEYS = tuple(f"btn{n}_press" for n in range(1, 9)) + (
    "central_press", "dial_cw", "dial_ccw")


def action_display_value(action, options):
    """Combo display text: the saved action, marked when it is not a valid option."""
    if action in options:
        return action
    return INVALID_ACTION_PREFIX + str(action)


def resolve_action_mapping(display, saved_action, options):
    """Resolve a combobox display back to the value that should be stored.

    A display still showing the marked saved value keeps that saved action, so
    an invalid mapping survives open plus Apply instead of being rewritten to
    the first option.
    """
    if saved_action is not None and display == action_display_value(saved_action, options):
        return saved_action
    return display


def parse_numeric_values(values):
    """Validate raw settings-entry strings and return them as floats.

    Raises ValueError naming the offending key. Nothing is mutated here.
    """
    parsed = {}
    for key, raw in values.items():
        try:
            parsed[key] = float(raw)
        except (TypeError, ValueError):
            raise ValueError(f"Field '{key}' is not a number: {raw!r}")
    return parsed


# Device orientation (U74 c): direct numeric entry in 1° steps plus presets.
ORIENTATION_PRESETS = (0, 90, 180, 270)
# Generalized tablet aspect for the pictogram (PTH-651 digitizer, mm).
PICTOGRAM_WIDTH = 223.52
PICTOGRAM_HEIGHT = 139.7


def parse_orientation_degrees(raw):
    """Whole degrees 0..359 from an entry string; None when invalid."""
    try:
        degrees = float(raw)
    except (TypeError, ValueError):
        return None
    if degrees != int(degrees) or not 0 <= degrees <= ORIENTATION_MAX_DEGREES:
        return None
    return int(degrees)


def tablet_pictogram_layout(degrees, width=PICTOGRAM_WIDTH, height=PICTOGRAM_HEIGHT):
    """Rotated tablet pictogram geometry, centered at the origin (U74 d).

    Unrotated layout: tablet rectangle spanning +-width/2 x +-height/2; the
    central round on the middle axis with the dial ring around it; express
    keys 1-4 above and 5-8 mirrored below, numbered top-to-bottom. Every
    point rotates clockwise by degrees with the same matrix the input
    transform uses, so widget and cursor mapping stay consistent.
    """
    half_w, half_h = width / 2, height / 2
    dial_radius = half_h * 0.32
    button_radius = half_h * 0.08
    inner_offset = half_h * 0.42
    step = half_h * 0.19
    column_x = -half_w * 0.5

    offsets = {
        1: -inner_offset - 3 * step, 2: -inner_offset - 2 * step,
        3: -inner_offset - step, 4: -inner_offset,
        5: inner_offset, 6: inner_offset + step,
        7: inner_offset + 2 * step, 8: inner_offset + 3 * step,
    }

    def rotated(x, y):
        if degrees == 0:
            return (x, y)
        return rotate_point(x, y, degrees, 0, 0)

    corners = [(-half_w, -half_h), (half_w, -half_h),
               (half_w, half_h), (-half_w, half_h)]
    buttons = [(number, *rotated(column_x, offset))
               for number, offset in offsets.items()]
    return {
        "rect": [rotated(x, y) for x, y in corners],
        "center": rotated(0, 0),
        "dial_radius": dial_radius,
        "button_radius": button_radius,
        "buttons": buttons,
    }


class OrientationWidget(tk.Canvas):
    """Orientation pictogram (U74 d): a generalized tablet drawn in Tk, no image
    file. Rectangle, central round with dial ring, express keys numbered
    1-4 above and 5-8 mirrored below; the drawing rotates clockwise with the
    orientation angle so the button mapping stays visible.
    """
    CANVAS_WIDTH = 220
    CANVAS_HEIGHT = 150
    SCALE = 0.82

    def __init__(self, parent, degrees=0, **kwargs):
        super().__init__(parent, width=self.CANVAS_WIDTH, height=self.CANVAS_HEIGHT,
                         background="#2b2b2b", highlightthickness=1,
                         highlightbackground="#666", **kwargs)
        self.draw_degrees(degrees)

    def _canvas_point(self, x, y):
        scale = self.CANVAS_HEIGHT / PICTOGRAM_HEIGHT * self.SCALE
        return (self.CANVAS_WIDTH / 2 + x * scale,
                self.CANVAS_HEIGHT / 2 + y * scale)

    def draw_degrees(self, degrees):
        """Redraw the pictogram at the given angle."""
        layout = tablet_pictogram_layout(degrees)
        scale = self.CANVAS_HEIGHT / PICTOGRAM_HEIGHT * self.SCALE
        self.delete("all")

        self.create_polygon(*[p for corner in layout["rect"]
                              for p in self._canvas_point(*corner)],
                            fill="#3a3a3a", outline="#8a8a8a")

        cx, cy = self._canvas_point(*layout["center"])
        dial = layout["dial_radius"] * scale
        self.create_oval(cx - dial, cy - dial, cx + dial, cy + dial,
                         outline="#6a9fd8")
        central = layout["button_radius"] * 1.2 * scale
        self.create_oval(cx - central, cy - central, cx + central, cy + central,
                         fill="#2e5a86", outline="#6a9fd8")
        self.create_text(cx, cy, text="central", fill="#e0e0e0",
                         font=("Segoe UI", 7))

        button = layout["button_radius"] * scale
        for number, x, y in layout["buttons"]:
            bx, by = self._canvas_point(x, y)
            self.create_oval(bx - button, by - button, bx + button, by + button,
                             fill="#575757", outline="#9a9a9a")
            self.create_text(bx, by, text=str(number), fill="#e6e6e6",
                             font=("Segoe UI", 8))


def apply_settings_to_config(config, numeric_values, accel_toggles, feature_toggles,
                             action_mappings, pen_button_actions=None, pen_absolute_mode=None,
                             button_actions=None, button_toggles=None):
    """Apply a complete settings set to config, or apply nothing at all.

    numeric_values are raw entry strings; they are validated before the first
    mutation, so one bad entry cannot leave config half-applied.
    """
    parsed = parse_numeric_values(numeric_values)

    if "orientation_degrees" in parsed:
        # U74: the entry feeds the 1° rotation matrix; whole degrees only.
        degrees = parse_orientation_degrees(numeric_values["orientation_degrees"])
        if degrees is None:
            raise ValueError(f"Field 'orientation_degrees' needs a whole degree "
                             f"0..{ORIENTATION_MAX_DEGREES}: "
                             f"{numeric_values['orientation_degrees']!r}")
        parsed["orientation_degrees"] = degrees

    for key, val in parsed.items():
        setattr(config, key, val)

    config.sync_sensitivities()

    config.feature_toggles["cursor_acceleration"] = accel_toggles["cursor_acceleration"]
    config.feature_toggles["scroll_acceleration"] = accel_toggles["scroll_acceleration"]

    for feat, enabled in feature_toggles.items():
        config.feature_toggles[feat] = enabled

    for feat_key, action in action_mappings.items():
        config.action_mapping[feat_key] = action

    if pen_absolute_mode is not None:
        config.feature_toggles["pen_absolute_mode"] = pen_absolute_mode

    for pen_key, action in (pen_button_actions or {}).items():
        config.action_mapping[pen_key] = action

    for key, enabled in (button_toggles or {}).items():
        config.feature_toggles[key] = enabled

    for key, action in (button_actions or {}).items():
        config.action_mapping[key] = action


class SettingsWindow(tk.Toplevel):
    def __init__(self, parent, state_ref, config_ref, device_info=None):
        super().__init__(parent)
        self.state = state_ref
        self.config = config_ref
        # {"connection": conn dict, "base_config": global config, "manager":
        # manager} when the driver runs (U383: main() always passes this
        # bundle; connection may be None and the section adopts the first
        # live device); a None bundle keeps the window on plain global
        # settings.
        self.device_info = device_info
        self.title("Wacom Touch Driver Settings")
        self.geometry("460x650")
        self.minsize(460, 480)

        # Shared numeric-entry registry: Motion, Pen and Advanced tabs fill this dict.
        self.entries = {}
        # Mapping values as they were when the window opened, keyed by feature.
        # Initialized here so the Pen tab entries survive the Features tab build.
        self.saved_actions = {}
        # Pen button comboboxes, filled by the Pen tab.
        self.pen_combos = {}
        # Button/dial toggles and comboboxes (U71), filled by the Buttons tab.
        self.button_vars = {}
        self.button_combos = {}

        main_container = ttk.Frame(self)
        main_container.pack(fill="both", expand=True, padx=10, pady=10)

        # Focused-device controls (U73): alias assignment and subset copy.
        # U383: main() always passes the bundle, so the section is present
        # even when no device had connected at launch.
        if device_info is not None:
            self._build_device_bar(main_container)

        notebook = ttk.Notebook(main_container)
        notebook.pack(fill="both", expand=True)

        # Tab 1: Motion & Acceleration
        self.tab_motion = ttk.Frame(notebook)
        notebook.add(self.tab_motion, text="Motion & Acceleration")
        self._build_motion_tab()

        # Tab 2: Pen
        self.tab_pen = ttk.Frame(notebook)
        notebook.add(self.tab_pen, text="Pen")
        self._build_pen_tab()

        # Tab 3: Touch Buttons & Rotary Dial
        self.tab_buttons = ttk.Frame(notebook)
        notebook.add(self.tab_buttons, text="Buttons")
        self._build_buttons_tab()

        # Tab 4: Combined Features & Actions
        self.tab_features_actions = ttk.Frame(notebook)
        notebook.add(self.tab_features_actions, text="Features & Actions")
        self._build_features_actions_tab()

        # Tab 5: Thresholds & Advanced
        self.tab_advanced = ttk.Frame(notebook)
        notebook.add(self.tab_advanced, text="Thresholds & Advanced")
        self._build_advanced_tab()

        # Bottom Button Bar
        btn_frame = ttk.Frame(main_container)
        btn_frame.pack(fill="x", side="bottom", pady=(10, 0))

        ttk.Button(btn_frame, text="Close", command=self.destroy).pack(side="right", padx=(5, 0))
        ttk.Button(btn_frame, text="Save to File", command=self.save_to_file).pack(side="right", padx=5)
        ttk.Button(btn_frame, text="Apply Live", command=self.apply_settings).pack(side="right")

    def _build_device_bar(self, container):
        # U383: a missing launch connection is no reason to skip the section;
        # focus the first live device from the manager, else none. The picked
        # device becomes the editing target too -- the same config the
        # launch-connection path passes in as config_ref -- so tabs, Apply
        # Live and the save lanes all agree on it.
        conn = self.device_info.get("connection")
        if conn is None:
            live = self._live_connections()
            conn = live[0] if live else None
            self.device_info["connection"] = conn
            if conn is not None:
                self.config = conn["config"]
        base = self.device_info["base_config"]
        frame = ttk.LabelFrame(container, text=" Focused Device ", padding=8)
        frame.pack(fill="x", pady=(0, 6))

        # Live device list (U87 a): the combo feeds on the running connections.
        ttk.Label(frame, text="Device:").grid(row=0, column=0, sticky="w")
        self.devices_combo = ttk.Combobox(frame, width=18, state="readonly")
        self.devices_combo.grid(row=0, column=1, sticky="w", padx=(4, 0))
        self.devices_combo.bind("<<ComboboxSelected>>", self._on_device_selected)
        refresh_btn = ttk.Button(frame, text="Refresh", width=8,
                                 command=self._refresh_device_combo)
        refresh_btn.grid(row=0, column=2, padx=(6, 0))
        ToolTip(refresh_btn, "Re-read the live device list from the running driver.")

        self.device_label = ttk.Label(
            frame, text=device_display_name(conn) if conn else NO_DEVICE_LABEL)
        self.device_label.grid(row=1, column=0, columnspan=4, sticky="w")

        ttk.Label(frame, text="Alias:").grid(row=2, column=0, sticky="w")
        self.alias_entry = ttk.Entry(frame, width=16)
        self.alias_entry.insert(0, (conn.get("alias") or "") if conn else "")
        self.alias_entry.grid(row=2, column=1, sticky="w", padx=(4, 0))
        btn = ttk.Button(frame, text="Assign alias", command=self.assign_alias)
        btn.grid(row=2, column=2, padx=(6, 0))
        ToolTip(btn, "Name this device; the name shows in the device list and "
                     "persists in the settings file under its identity key. "
                     "Tags the wireless receiver twin manually as well.")

        ttk.Label(frame, text="Copy settings to:").grid(row=3, column=0, sticky="w", pady=(6, 0))
        self.copy_alias_combo = ttk.Combobox(frame, values=sorted(base.devices), width=16)
        self.copy_alias_combo.grid(row=3, column=1, sticky="w", padx=(4, 0))
        self.copy_section_vars = {
            name: tk.BooleanVar(value=True) for name in DEVICE_TRANSFER_SECTIONS}
        section_labels = {
            "motion_and_thresholds": "Motion",
            "feature_toggles": "Toggles",
            "action_mapping": "Mapping",
        }
        for idx, (name, var) in enumerate(self.copy_section_vars.items(), start=3):
            chk = ttk.Checkbutton(frame, text=section_labels[name], variable=var)
            chk.grid(row=idx, column=2, sticky="w")
        copy_btn = ttk.Button(frame, text="Copy to alias", command=self.copy_settings_to_alias)
        copy_btn.grid(row=3, column=3, sticky="e")
        ToolTip(copy_btn, "Copies the selected sections of this device's settings into "
                          "the chosen alias; takes effect on that device's next connection.")

        device_btn = ttk.Button(frame, text="Save to Device",
                                command=self.save_to_focused_device)
        device_btn.grid(row=6, column=0, columnspan=2, sticky="w", pady=(8, 0))
        ToolTip(device_btn, "Save ONLY this device's current values under its "
                            "identity key; the main Save button stays global.")
        frame.columnconfigure(3, weight=1)
        self._refresh_device_combo()

    def _live_connections(self):
        """The running device list; the focused connection when no manager is wired."""
        manager = self.device_info.get("manager")
        if manager is not None:
            conns = manager.list_connections()
            if conns:
                return conns
        conn = self.device_info.get("connection")
        return [conn] if conn is not None else []

    def _refresh_device_combo(self):
        """Rebuild the combo from the live device list, keeping the focus."""
        conns = self._live_connections()
        self._device_conns = conns
        labels = [device_display_name(conn) for conn in conns]
        self.devices_combo["values"] = labels
        focused = self.device_info.get("connection")
        index = -1
        for i, candidate in enumerate(conns):
            if candidate is focused:
                index = i
                break
        if index >= 0:
            self.devices_combo.current(index)
        elif labels:
            # U383: the focused connection dropped -- adopt the first live
            # device so combo, label, editing config and save lanes agree.
            self.device_info["connection"] = conns[0]
            self.devices_combo.current(0)
            if hasattr(self, "device_label"):
                self._rebind_focused_device()

    def _on_device_selected(self, event=None):
        """Point the window and its widgets at the selected live device."""
        conns = getattr(self, "_device_conns", [])
        index = self.devices_combo.current()
        if index < 0 or index >= len(conns):
            return False
        conn = conns[index]
        if conn is self.device_info["connection"]:
            return False
        self.device_info["connection"] = conn
        self._rebind_focused_device()
        ToastNotification(self, "Device Focused",
                          f"Editing settings of {device_display_name(conn)}.",
                          timeout_ms=1500)
        return True

    def _rebind_focused_device(self):
        conn = self.device_info["connection"]
        self.config = conn["config"]
        self.alias_entry.delete(0, "end")
        self.alias_entry.insert(0, conn.get("alias") or "")
        self.device_label.configure(text=device_display_name(conn))
        self._rebind_config_widgets()

    @staticmethod
    def _set_widget_value(widget, text):
        """Write a value into an Entry, Combobox or variable-like widget."""
        if hasattr(widget, "delete") and hasattr(widget, "insert"):
            widget.delete(0, "end")
            widget.insert(0, text)
        elif hasattr(widget, "set"):
            widget.set(text)

    def _rebind_config_widgets(self):
        """Reload every tab widget from the now-focused device's config."""
        config = self.config
        options = getattr(config, "AVAILABLE_ACTIONS", [])
        for key, ent in self.entries.items():
            value = getattr(config, key, None)
            if value is not None:
                self._set_widget_value(ent, str(value))
        for name, var in getattr(self, "feature_vars", {}).items():
            if name in config.feature_toggles:
                self._set_widget_value(var, config.feature_toggles[name])
        for name, var in (("cursor_acceleration", getattr(self, "cursor_accel_var", None)),
                          ("scroll_acceleration", getattr(self, "scroll_accel_var", None))):
            if var is not None and name in config.feature_toggles:
                self._set_widget_value(var, config.feature_toggles[name])
        for key, var in getattr(self, "button_vars", {}).items():
            if key in config.feature_toggles:
                self._set_widget_value(var, config.feature_toggles[key])
        pen_absolute_var = getattr(self, "pen_absolute_var", None)
        if pen_absolute_var is not None:
            self._set_widget_value(pen_absolute_var,
                                   config.feature_toggles.get("pen_absolute_mode", True))
        self.saved_actions = dict(config.action_mapping)
        for combo_map in (getattr(self, "action_combos", {}),
                          getattr(self, "pen_combos", {}),
                          getattr(self, "button_combos", {})):
            for key, combo in combo_map.items():
                self._set_widget_value(
                    combo, action_display_value(config.action_mapping.get(key), options))
        orientation_widget = getattr(self, "orientation_widget", None)
        if orientation_widget is not None and hasattr(orientation_widget, "draw_degrees"):
            orientation_widget.draw_degrees(config.orientation_degrees)

    def assign_alias(self):
        """Name the focused device: stores the alias under its identity key (U87 a)."""
        alias = self.alias_entry.get().strip()
        if not alias:
            messagebox.showerror("Invalid Alias", "Enter an alias name before assigning it.")
            return False
        conn = self.device_info.get("connection")
        if conn is None:
            messagebox.showerror(
                "No Device Focused",
                "Wake the device and pick it in the device list first.")
            return False
        base = self.device_info["base_config"]
        conn["alias"] = alias
        identity = conn.get("identity")
        if identity is not None:
            base.set_device_alias(identity, alias)
        # The binding lets this product id resolve to the alias on the next
        # connection; the receiver twin is never auto-aliased.
        base.set_device_entry(alias, {}, bindings=[conn["product_id"]])
        self.device_label.configure(text=device_display_name(conn))
        self._refresh_device_combo()
        ToastNotification(self, "Alias Assigned",
                          f"Device named '{alias}'. Save to persist.",
                          timeout_ms=1800)
        return True

    def copy_settings_to_alias(self):
        """Transfer a selectable subset of the focused device's settings.

        Writes the subset under the target alias; applies to that device on
        its next connection.
        """
        if not self.apply_settings():
            return False
        target = self.copy_alias_combo.get().strip()
        if not target:
            messagebox.showerror("Invalid Target", "Pick the alias to copy settings into.")
            return False
        selected = [name for name, var in self.copy_section_vars.items() if var.get()]
        if not selected:
            messagebox.showerror("Nothing Selected", "Select at least one section to copy.")
            return False
        conn = self.device_info.get("connection")
        if conn is None:
            messagebox.showerror(
                "No Device Focused",
                "Wake the device and pick it in the device list first.")
            return False
        base = self.device_info["base_config"]
        entry = dict(base.devices.get(target, {}))
        entry.update(collect_config_sections(conn["config"], selected))
        base.devices[target] = entry
        ToastNotification(self, "Settings Copied",
                          f"Copied {len(selected)} section(s) to '{target}'. Save to persist.",
                          timeout_ms=1800)
        return True

    def _build_motion_tab(self):
        canvas = tk.Canvas(self.tab_motion, borderwidth=0)
        scrollbar = ttk.Scrollbar(self.tab_motion, orient="vertical", command=canvas.yview)
        scroll_frame = ttk.Frame(canvas)

        scroll_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas_window = canvas.create_window((0, 0), window=scroll_frame, anchor="nw")
        canvas.bind("<Configure>", lambda e: canvas.itemconfig(canvas_window, width=e.width))
        canvas.configure(yscrollcommand=scrollbar.set)

        # --- 0. Device Orientation Frame (U74 c/d) ---
        orient_frame = ttk.LabelFrame(scroll_frame, text=" Device Orientation ", padding=10)
        orient_frame.pack(fill="x", padx=10, pady=8, expand=True)

        orient_lbl = ttk.Label(orient_frame, text="Rotation (degrees, 1° steps):")
        orient_lbl.grid(row=0, column=0, sticky="w", pady=4)
        orient_ent = ttk.Entry(orient_frame, width=8)
        orient_ent.insert(0, str(self.config.orientation_degrees))
        orient_ent.grid(row=0, column=1, sticky="w", padx=(4, 0))
        self.entries["orientation_degrees"] = orient_ent
        orient_ent.bind("<KeyRelease>", self._on_orientation_entry)
        orient_tip = ("Rotates the interpretation of touch and pen input clockwise "
                      "by this angle around the tablet center. 0 leaves input unchanged.")
        ToolTip(orient_lbl, orient_tip)
        ToolTip(orient_ent, orient_tip)

        preset_frame = ttk.Frame(orient_frame)
        preset_frame.grid(row=0, column=2, columnspan=2, sticky="e", padx=(8, 0))
        for preset in ORIENTATION_PRESETS:
            preset_btn = ttk.Button(preset_frame, text=f"{preset}°", width=4,
                                    command=lambda angle=preset: self._set_orientation(angle))
            preset_btn.pack(side="left", padx=(3, 0))
            ToolTip(preset_btn, f"Set the orientation preset {preset} degrees.")

        self.orientation_widget = OrientationWidget(
            orient_frame, degrees=self.config.orientation_degrees)
        self.orientation_widget.grid(row=1, column=0, columnspan=4, pady=(6, 0))
        orient_frame.columnconfigure(1, weight=1)

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

    def _on_orientation_entry(self, event=None):
        """Live-redraw the pictogram while typing a valid angle."""
        degrees = parse_orientation_degrees(self.entries["orientation_degrees"].get())
        if degrees is not None:
            self.orientation_widget.draw_degrees(degrees)

    def _set_orientation(self, degrees):
        """Preset button: write the angle into the entry and redraw the widget."""
        entry = self.entries["orientation_degrees"]
        entry.delete(0, "end")
        entry.insert(0, str(degrees))
        self.orientation_widget.draw_degrees(degrees)

    def _build_pen_tab(self):
        canvas = tk.Canvas(self.tab_pen, borderwidth=0)
        scrollbar = ttk.Scrollbar(self.tab_pen, orient="vertical", command=canvas.yview)
        scroll_frame = ttk.Frame(canvas)

        scroll_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas_window = canvas.create_window((0, 0), window=scroll_frame, anchor="nw")
        canvas.bind("<Configure>", lambda e: canvas.itemconfig(canvas_window, width=e.width))
        canvas.configure(yscrollcommand=scrollbar.set)

        pen_tooltips = {
            "pen_speed": "Relative-mode speed: pixels per millimeter of pen travel. Ignored in absolute mode.",
            "pen_pressure_threshold": "Pressure units (0..2047) at which the left button is held. Below it the button releases; filters light-contact jitter.",
        }

        # --- 1. Movement Frame ---
        movement_frame = ttk.LabelFrame(scroll_frame, text=" Pen Movement Parameters ", padding=10)
        movement_frame.pack(fill="x", padx=10, pady=8, expand=True)

        self.pen_absolute_var = tk.BooleanVar(value=self.config.feature_toggles.get("pen_absolute_mode", True))
        chk_pen = ttk.Checkbutton(movement_frame, text="Absolute Movement (Off = Relative Deltas)", variable=self.pen_absolute_var)
        chk_pen.grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 6))
        ToolTip(chk_pen, "Absolute: pen position maps the tablet range onto the screen. Relative: movement is sent as deltas scaled by the speed entry.")

        pen_fields = [
            ("Speed (px/mm):", "pen_speed", self.config.pen_speed),
            ("Pressure Threshold:", "pen_pressure_threshold", self.config.pen_pressure_threshold),
        ]

        for idx, (label_text, key, initial_val) in enumerate(pen_fields, start=1):
            lbl = ttk.Label(movement_frame, text=label_text)
            lbl.grid(row=idx, column=0, sticky="w", pady=4)
            ent = ttk.Entry(movement_frame, width=12)
            ent.insert(0, str(initial_val))
            ent.grid(row=idx, column=1, sticky="e", pady=4)
            self.entries[key] = ent

            if key in pen_tooltips:
                ToolTip(lbl, pen_tooltips[key])
                ToolTip(ent, pen_tooltips[key])

        movement_frame.columnconfigure(1, weight=1)

        # --- 2. Pen Button Mapping Frame ---
        button_frame = ttk.LabelFrame(scroll_frame, text=" Pen Button Mapping ", padding=10)
        button_frame.pack(fill="x", padx=10, pady=8, expand=True)

        options = getattr(self.config, "AVAILABLE_ACTIONS", [])
        button_fields = [
            ("Pen Button 1:", "pen_button_1",
             "Press/release action for pen button 1 (status bit 1)."),
            ("Pen Button 2:", "pen_button_2",
             "Press/release action for pen button 2 (status bit 2). Mouse-button actions hold while pressed; other actions tap on the press edge."),
        ]

        for idx, (label_text, key, tip) in enumerate(button_fields, start=1):
            lbl = ttk.Label(button_frame, text=label_text)
            lbl.grid(row=idx, column=0, sticky="w", pady=4)
            current_action = self.config.action_mapping.get(key)
            combo = ttk.Combobox(button_frame, values=options, state="readonly", width=22)
            combo.set(action_display_value(current_action, options))
            combo.grid(row=idx, column=1, sticky="e", pady=4)
            self.pen_combos[key] = combo
            self.saved_actions[key] = current_action
            ToolTip(lbl, tip)
            ToolTip(combo, tip)

        button_frame.columnconfigure(1, weight=1)

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

    def _build_buttons_tab(self):
        canvas = tk.Canvas(self.tab_buttons, borderwidth=0)
        scrollbar = ttk.Scrollbar(self.tab_buttons, orient="vertical", command=canvas.yview)
        scroll_frame = ttk.Frame(canvas)

        scroll_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas_window = canvas.create_window((0, 0), window=scroll_frame, anchor="nw")
        canvas.bind("<Configure>", lambda e: canvas.itemconfig(canvas_window, width=e.width))
        canvas.configure(yscrollcommand=scrollbar.set)

        button_tooltips = {
            "btn1_press": "Action on the press edge of express key 1 (byte 4 bitmask bit 0). "
                          "Mouse-button actions hold while pressed; other actions tap once.",
            "central_press": "Action on the press edge of the central button (byte 3 bit 0).",
            "dial_cw": "Action per clockwise step of the rotary touch dial. Steps accumulate "
                       "from the reported dial angle; a decreasing angle is clockwise.",
            "dial_ccw": "Action per counter-clockwise step of the rotary touch dial "
                        "(increasing angle).",
        }

        # --- 1. Dial Parameters Frame ---
        dial_frame = ttk.LabelFrame(scroll_frame, text=" Rotary Dial Parameters ", padding=10)
        dial_frame.pack(fill="x", padx=10, pady=8, expand=True)

        dial_fields = [
            ("Step Threshold (angle units):", "dial_step_threshold", self.config.dial_step_threshold),
        ]
        dial_tooltips = {
            "dial_step_threshold": "Dial angle units traversed before one CW/CCW step action fires.",
        }

        for idx, (label_text, key, initial_val) in enumerate(dial_fields, start=1):
            lbl = ttk.Label(dial_frame, text=label_text)
            lbl.grid(row=idx, column=0, sticky="w", pady=4)
            ent = ttk.Entry(dial_frame, width=12)
            ent.insert(0, str(initial_val))
            ent.grid(row=idx, column=1, sticky="e", pady=4)
            self.entries[key] = ent

            if key in dial_tooltips:
                ToolTip(lbl, dial_tooltips[key])
                ToolTip(ent, dial_tooltips[key])

        dial_frame.columnconfigure(1, weight=1)

        # --- 2. Button Mapping Frame ---
        mapping_frame = ttk.LabelFrame(scroll_frame, text=" Touch Button & Dial Mapping ", padding=10)
        mapping_frame.pack(fill="x", padx=10, pady=8, expand=True)

        options = getattr(self.config, "AVAILABLE_ACTIONS", [])
        control_fields = [(f"Button {n}:", f"btn{n}_press") for n in range(1, 9)] + [
            ("Central Button:", "central_press"),
            ("Dial Clockwise:", "dial_cw"),
            ("Dial Counter-Clockwise:", "dial_ccw"),
        ]

        for idx, (label_text, key) in enumerate(control_fields, start=1):
            lbl = ttk.Label(mapping_frame, text=label_text)
            lbl.grid(row=idx, column=0, sticky="w", pady=4)

            var = tk.BooleanVar(value=self.config.feature_toggles.get(key, True))
            chk = ttk.Checkbutton(mapping_frame, text="on", variable=var)
            chk.grid(row=idx, column=1, sticky="w", pady=4)
            self.button_vars[key] = var

            current_action = self.config.action_mapping.get(key)
            combo = ttk.Combobox(mapping_frame, values=options, state="readonly", width=22)
            combo.set(action_display_value(current_action, options))
            combo.grid(row=idx, column=2, sticky="e", pady=4)
            self.button_combos[key] = combo
            self.saved_actions[key] = current_action

            tip = button_tooltips.get(key, "Action for this touch control; toggle enables the control.")
            ToolTip(lbl, tip)
            ToolTip(chk, tip)
            ToolTip(combo, tip)

        mapping_frame.columnconfigure(2, weight=1)

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
        # self.saved_actions is initialized in __init__ so the Pen tab entries
        # built earlier are not wiped here.
        options = getattr(self.config, "AVAILABLE_ACTIONS", [])

        # Filter out cursor_acceleration & scroll_acceleration since they are in Motion tab,
        # pen_absolute_mode since it is in the Pen tab, and the button/dial controls
        # since they are in the Buttons tab.
        excluded_features = ({"cursor_acceleration", "scroll_acceleration", "pen_absolute_mode"}
                             | set(BUTTON_CONTROL_KEYS))

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
                combo.set(action_display_value(current_action, options))
                combo.pack(side="right", anchor="e")
                self.action_combos[feature_key] = combo
                self.saved_actions[feature_key] = current_action

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
            "pinch_sensitivity": "Multiplier applied to the pinch zoom with 2 fingers.",
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
            ("Pinch Sensitivity:", "pinch_sensitivity", self.config.pinch_sensitivity),
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
        """Apply the widget values to the live config; returns False on bad input."""
        try:
            # 1. Collect widget values only; config is untouched until validation passes
            numeric_values = {key: ent.get() for key, ent in self.entries.items()}
            accel_toggles = {
                "cursor_acceleration": self.cursor_accel_var.get(),
                "scroll_acceleration": self.scroll_accel_var.get(),
            }
            feature_toggles = {feat: var.get() for feat, var in self.feature_vars.items()}
            options = getattr(self.config, "AVAILABLE_ACTIONS", [])
            saved_actions = getattr(self, "saved_actions", {})
            action_mappings = {
                feat_key: resolve_action_mapping(
                    combo.get(),
                    saved_actions.get(feat_key, self.config.action_mapping.get(feat_key)),
                    options,
                )
                for feat_key, combo in self.action_combos.items()
            }
            pen_combos = getattr(self, "pen_combos", {})
            pen_button_actions = {
                pen_key: resolve_action_mapping(
                    combo.get(),
                    saved_actions.get(pen_key, self.config.action_mapping.get(pen_key)),
                    options,
                )
                for pen_key, combo in pen_combos.items()
            }
            pen_absolute_var = getattr(self, "pen_absolute_var", None)
            pen_absolute_mode = pen_absolute_var.get() if pen_absolute_var is not None else None
            button_combos = getattr(self, "button_combos", {})
            button_actions = {
                key: resolve_action_mapping(
                    combo.get(),
                    saved_actions.get(key, self.config.action_mapping.get(key)),
                    options,
                )
                for key, combo in button_combos.items()
            }
            button_vars = getattr(self, "button_vars", {})
            button_toggles = {key: var.get() for key, var in button_vars.items()}

            # 2. Validate everything, then apply all-or-nothing
            apply_settings_to_config(
                self.config, numeric_values, accel_toggles, feature_toggles, action_mappings,
                pen_button_actions=pen_button_actions,
                pen_absolute_mode=pen_absolute_mode,
                button_actions=button_actions,
                button_toggles=button_toggles,
            )
        except ValueError as e:
            messagebox.showerror("Invalid Input", f"Please enter valid numeric values.\nDetails: {e}")
            return False

        ToastNotification(self, "Success", "Settings applied directly to driver!", timeout_ms=1500)
        return True

    def save_to_focused_device(self):
        """Subset save (U87 e): only the focused device's values, under its key.

        The main Save button keeps writing the global sections; this button
        writes nothing else, so one device's values never leak into another.
        """
        if not self.apply_settings():
            return False

        conn = self.device_info.get("connection")
        if conn is None:
            messagebox.showerror(
                "No Device Focused",
                "Wake the device and pick it in the device list first.")
            return False
        base = self.device_info["base_config"]
        identity = conn.get("identity")
        if identity is None:
            messagebox.showerror(
                "No Device Identity",
                "This connection carries no identity key; nothing was saved.")
            return False

        base.set_device_identity_settings(
            identity, collect_config_sections(conn["config"], DEVICE_TRANSFER_SECTIONS))
        filepath = getattr(base, "save_path", "user.cfg")
        base.save_to_file(filepath)

        ToastNotification(self, "Device Saved",
                          f"Saved {device_display_name(conn)} under its identity key.",
                          timeout_ms=1800)
        return True

    def save_to_file(self):
        """Apply first; the file is written only when the apply succeeded.

        With a focused device the device settings are written into its
        devices entry under its alias; the global sections stay untouched.
        """
        if not self.apply_settings():
            return False

        device_info = getattr(self, "device_info", None)
        conn = device_info.get("connection") if device_info is not None else None
        if conn is not None:
            base = device_info["base_config"]
            alias = conn.get("alias")
            if alias is None:
                messagebox.showerror(
                    "No Device Alias",
                    "Assign an alias before saving per-device settings.")
                return False
            base.set_device_entry(alias, collect_config_sections(
                conn["config"], DEVICE_TRANSFER_SECTIONS))
            filepath = getattr(base, "save_path", "user.cfg")
            base.save_to_file(filepath)
        else:
            filepath = getattr(self.config, "save_path", "user.cfg")
            self.config.save_to_file(filepath)

        ToastNotification(self, "File Saved", f"Configuration saved to {filepath}", timeout_ms=1800)
        return True


class DriverTrayApp:
    def __init__(self, root, state_ref, config_ref, enabled_event_ref, stop_event_ref, device_info=None):
        self.root = root
        self.state = state_ref
        self.config = config_ref
        self.device_info = device_info
        self.touch_enabled_event = enabled_event_ref
        self.stop_event = stop_event_ref
        self.icon = None
        self.settings_window = None
        self.icon_cache = IconCache(text="WT")

    def toggle_touch(self, icon=None, item=None):
        if self.touch_enabled_event.is_set():
            self.touch_enabled_event.clear()
        else:
            self.touch_enabled_event.set()
        
        status = "Paused" if not self.touch_enabled_event.is_set() else "Active"
        print(f"[Tray] Touch input is now {status} (touch_paused = {not self.touch_enabled_event.is_set()})")
        self.update_icon()
        
    def is_touch_enabled(self, item=None):
        return self.touch_enabled_event.is_set()
    
    def update_icon(self):
        if self.icon:
            is_enabled = self.touch_enabled_event.is_set()
            self.icon.icon = self.icon_cache.get(is_enabled)
            self.icon.title = f"Wacom Touch Driver ({'Enabled' if self.touch_enabled_event.is_set() else 'Disabled'})"

    def open_settings(self, icon=None, item=None):
        self.root.after(0, self._show_settings_gui)

    def _show_settings_gui(self):
        if self.settings_window is not None and self.settings_window.winfo_exists():
            self.settings_window.lift()
            return
        self.settings_window = SettingsWindow(self.root, self.state, self.config, self.device_info)

    def quit_driver(self, icon=None, item=None):
        print("[Tray] Exiting Wacom Touch Driver...")
        self.stop_event.set()
        #self.state.set_running_global(False)
        # to stop waiting of device manager if touch is disabled
        if self.touch_enabled_event:
            self.touch_enabled_event.set()
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

        initial_state = self.touch_enabled_event.is_set()
        self.icon = pystray.Icon(
            "WacomTouchDriver",
            self.icon_cache.get(initial_state),
            "Wacom Touch Driver",
            menu
        )
        self.icon.run()


def launch_gui(state_ref, config_ref, pause_event_ref, stop_event_ref, device_info=None):
    root = tk.Tk()
    root.withdraw()

    app = DriverTrayApp(root, state_ref, config_ref, pause_event_ref, stop_event_ref,
                        device_info=device_info)
    tray_thread = threading.Thread(target=app.run_tray, daemon=True)
    tray_thread.start()

    root.mainloop()