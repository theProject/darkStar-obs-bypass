#!/usr/bin/env python3
"""
Project darkStar: the OBS Raspberry Pi-pass (bypass lol)
Pi-native OBSBOT Meet SE control panel using standard Linux V4L2 controls.

This app avoids the x86_64-only OBSBOT SDK and does not require OBS Studio.
It controls any camera features exposed through /dev/video0 via v4l2-ctl,
and launches live preview through ffplay using MJPEG.

Recommended packages:
    sudo apt update
    sudo apt install -y python3-tk v4l-utils ffmpeg

Run:
    python3 project_darkstar_obs_bypass.py
"""

from __future__ import annotations

import os
import re
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import tkinter as tk
from tkinter import filedialog, messagebox, ttk


APP_NAME = "Project darkStar: the OBS by-pass"
DEFAULT_DEVICE = "/dev/video0"
PAN_TILT_UNITS_PER_DEGREE = 3600
DEFAULT_PREVIEW_SIZE = "1280x720"
DEFAULT_PREVIEW_FPS = 30
DEFAULT_PREVIEW_FORMAT = "mjpeg"


@dataclass
class ControlInfo:
    name: str
    ctrl_type: str
    min_value: Optional[int] = None
    max_value: Optional[int] = None
    step: Optional[int] = None
    default: Optional[int] = None
    value: Optional[int] = None
    flags: str = ""
    menu_items: Dict[int, str] = field(default_factory=dict)

    @property
    def inactive(self) -> bool:
        return "inactive" in self.flags.lower()

    @property
    def read_only(self) -> bool:
        lowered = self.flags.lower()
        return "read-only" in lowered or "readonly" in lowered

    @property
    def disabled(self) -> bool:
        return self.inactive or self.read_only

    def clamp(self, value: int) -> int:
        if self.min_value is not None:
            value = max(self.min_value, value)
        if self.max_value is not None:
            value = min(self.max_value, value)
        if self.step and self.step > 1:
            base = self.min_value or 0
            value = base + round((value - base) / self.step) * self.step
        return int(value)


class V4L2Error(RuntimeError):
    pass


def run_command(args: List[str], check: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, text=True, capture_output=True, check=check)


def command_exists(command: str) -> bool:
    return subprocess.run(
        ["bash", "-lc", f"command -v {command} >/dev/null 2>&1"],
        text=True,
        capture_output=True,
    ).returncode == 0


def parse_int(value: Optional[str]) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def parse_key_values(text: str) -> Dict[str, str]:
    return {key: value for key, value in re.findall(r"([A-Za-z_]+)=([^ ]+)", text)}


def parse_controls(output: str) -> Dict[str, ControlInfo]:
    controls: Dict[str, ControlInfo] = {}
    current: Optional[ControlInfo] = None

    control_pattern = re.compile(
        r"^\s*([A-Za-z0-9_]+)\s+0x[0-9a-fA-F]+\s+\(([^)]+)\)\s*:\s*(.*)$"
    )
    menu_pattern = re.compile(r"^\s*(\d+)\s*:\s*(.+?)\s*$")

    for raw_line in output.splitlines():
        line = raw_line.rstrip()
        match = control_pattern.match(line)
        if match:
            name, ctrl_type, rest = match.groups()
            kv = parse_key_values(rest)
            current = ControlInfo(
                name=name,
                ctrl_type=ctrl_type.lower().strip(),
                min_value=parse_int(kv.get("min")),
                max_value=parse_int(kv.get("max")),
                step=parse_int(kv.get("step")),
                default=parse_int(kv.get("default")),
                value=parse_int(kv.get("value")),
                flags=kv.get("flags", ""),
            )
            controls[name] = current
            continue

        menu_match = menu_pattern.match(line)
        if menu_match and current:
            value_text, label = menu_match.groups()
            try:
                current.menu_items[int(value_text)] = label.strip()
            except ValueError:
                pass

    return controls


def list_controls(device: str) -> Dict[str, ControlInfo]:
    result = run_command(["v4l2-ctl", f"--device={device}", "--list-ctrls-menus"])
    if result.returncode != 0:
        raise V4L2Error(result.stderr.strip() or result.stdout.strip() or "Unable to list controls.")
    return parse_controls(result.stdout)


def set_control(device: str, name: str, value: int) -> Tuple[bool, str]:
    result = run_command(["v4l2-ctl", f"--device={device}", f"--set-ctrl={name}={value}"])
    message = "\n".join(part for part in [result.stdout.strip(), result.stderr.strip()] if part)
    return result.returncode == 0, message


def list_devices_text() -> str:
    result = run_command(["v4l2-ctl", "--list-devices"])
    return "\n".join(part for part in [result.stdout.strip(), result.stderr.strip()] if part)


def list_formats_text(device: str) -> str:
    result = run_command(["v4l2-ctl", f"--device={device}", "--list-formats-ext"])
    return "\n".join(part for part in [result.stdout.strip(), result.stderr.strip()] if part)


class DarkStarApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()

        self.title(APP_NAME)
        self.geometry("1120x820")
        self.minsize(960, 680)

        self.device_var = tk.StringVar(value=DEFAULT_DEVICE)
        self.status_var = tk.StringVar(value="Standing by. Camera controls will load from /dev/video0.")
        self.preview_size_var = tk.StringVar(value=DEFAULT_PREVIEW_SIZE)
        self.preview_fps_var = tk.IntVar(value=DEFAULT_PREVIEW_FPS)
        self.ptz_step_degrees_var = tk.IntVar(value=10)
        self.controls: Dict[str, ControlInfo] = {}
        self.preview_process: Optional[subprocess.Popen[str]] = None
        self.slider_jobs: Dict[str, str] = {}
        self.widget_registry: Dict[str, List[tk.Widget]] = {}

        self._configure_theme()
        self._build_layout()
        self.refresh_all()
        self.protocol("WM_DELETE_WINDOW", self.close_app)

    def _configure_theme(self) -> None:
        self.configure(bg="#07090d")
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure("Dark.TFrame", background="#07090d")
        style.configure("Panel.TFrame", background="#10151f")
        style.configure("Card.TLabelframe", background="#10151f", foreground="#e9f2ff", bordercolor="#243042")
        style.configure("Card.TLabelframe.Label", background="#10151f", foreground="#67e8f9", font=("TkDefaultFont", 10, "bold"))
        style.configure("Header.TLabel", background="#07090d", foreground="#f8fafc", font=("TkDefaultFont", 21, "bold"))
        style.configure("Subheader.TLabel", background="#07090d", foreground="#94a3b8", font=("TkDefaultFont", 10))
        style.configure("Panel.TLabel", background="#10151f", foreground="#e2e8f0")
        style.configure("Muted.TLabel", background="#10151f", foreground="#94a3b8")
        style.configure("Status.TLabel", background="#0f172a", foreground="#cbd5e1", padding=10)
        style.configure("Accent.TButton", padding=8)
        style.configure("Danger.TButton", padding=8)
        style.configure("TButton", padding=6)
        style.configure("TCheckbutton", background="#10151f", foreground="#e2e8f0")
        style.map("TCheckbutton", background=[("active", "#10151f")], foreground=[("disabled", "#64748b")])
        style.configure("Horizontal.TScale", background="#10151f")

    def _build_layout(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        header = ttk.Frame(self, style="Dark.TFrame", padding=(18, 16, 18, 10))
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(1, weight=1)

        title_box = ttk.Frame(header, style="Dark.TFrame")
        title_box.grid(row=0, column=0, sticky="w")
        ttk.Label(title_box, text="Project darkStar", style="Header.TLabel").pack(anchor="w")
        ttk.Label(title_box, text="the OBS by-pass · Pi-native OBSBOT control over V4L2", style="Subheader.TLabel").pack(anchor="w")

        device_box = ttk.Frame(header, style="Dark.TFrame")
        device_box.grid(row=0, column=1, sticky="e")
        ttk.Label(device_box, text="Device", style="Subheader.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 6))
        self.device_entry = ttk.Entry(device_box, textvariable=self.device_var, width=16)
        self.device_entry.grid(row=1, column=0, sticky="ew", padx=(0, 8))
        ttk.Button(device_box, text="Refresh", command=self.refresh_all).grid(row=1, column=1, padx=(0, 6))
        ttk.Button(device_box, text="Devices", command=self.show_devices).grid(row=1, column=2)

        toolbar = ttk.Frame(self, style="Dark.TFrame", padding=(18, 0, 18, 12))
        toolbar.grid(row=1, column=0, sticky="ew")

        ttk.Button(toolbar, text="Preview", style="Accent.TButton", command=self.start_preview).pack(side="left", padx=(0, 8))
        ttk.Button(toolbar, text="Stop Preview", command=self.stop_preview).pack(side="left", padx=(0, 8))
        ttk.Button(toolbar, text="Snapshot", command=self.take_snapshot).pack(side="left", padx=(0, 8))
        ttk.Button(toolbar, text="Home Camera", command=self.home_camera).pack(side="left", padx=(0, 8))
        ttk.Button(toolbar, text="Safe Auto Reset", command=self.safe_auto_reset).pack(side="left", padx=(0, 8))
        ttk.Button(toolbar, text="Formats", command=self.show_formats).pack(side="left", padx=(0, 8))

        ttk.Label(toolbar, text="Preview:", style="Subheader.TLabel").pack(side="left", padx=(18, 4))
        ttk.Combobox(
            toolbar,
            textvariable=self.preview_size_var,
            values=["1280x720", "1920x1080", "640x480", "640x360"],
            width=12,
            state="readonly",
        ).pack(side="left", padx=(0, 8))
        ttk.Spinbox(toolbar, from_=5, to=60, increment=5, textvariable=self.preview_fps_var, width=5).pack(side="left")

        main = ttk.Frame(self, style="Dark.TFrame", padding=(18, 0, 18, 0))
        main.grid(row=2, column=0, sticky="nsew")
        main.columnconfigure(0, weight=0)
        main.columnconfigure(1, weight=1)
        main.rowconfigure(0, weight=1)

        left = ttk.Frame(main, style="Panel.TFrame", padding=14)
        left.grid(row=0, column=0, sticky="nsw", padx=(0, 12))
        self._build_ptz_panel(left)
        self._build_quick_panel(left)

        right = ttk.Frame(main, style="Dark.TFrame")
        right.grid(row=0, column=1, sticky="nsew")
        right.rowconfigure(0, weight=1)
        right.columnconfigure(0, weight=1)
        self._build_scrollable_controls(right)

        status = ttk.Label(self, textvariable=self.status_var, style="Status.TLabel", anchor="w")
        status.grid(row=3, column=0, sticky="ew")

    def _build_ptz_panel(self, parent: ttk.Frame) -> None:
        frame = ttk.LabelFrame(parent, text="PTZ Command", style="Card.TLabelframe", padding=12)
        frame.pack(fill="x", pady=(0, 12))

        ttk.Label(frame, text="Step", style="Panel.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Combobox(
            frame,
            textvariable=self.ptz_step_degrees_var,
            values=[1, 3, 5, 10, 15, 25, 45, 90],
            width=8,
            state="readonly",
        ).grid(row=0, column=1, sticky="ew", padx=(8, 0))
        ttk.Label(frame, text="degrees", style="Muted.TLabel").grid(row=0, column=2, sticky="w", padx=(6, 0))

        pad = ttk.Frame(frame, style="Panel.TFrame")
        pad.grid(row=1, column=0, columnspan=3, pady=(16, 8))

        ttk.Button(pad, text="↖", width=5, command=lambda: self.move_relative(-1, 1)).grid(row=0, column=0, padx=3, pady=3)
        ttk.Button(pad, text="↑", width=5, command=lambda: self.move_relative(0, 1)).grid(row=0, column=1, padx=3, pady=3)
        ttk.Button(pad, text="↗", width=5, command=lambda: self.move_relative(1, 1)).grid(row=0, column=2, padx=3, pady=3)
        ttk.Button(pad, text="←", width=5, command=lambda: self.move_relative(-1, 0)).grid(row=1, column=0, padx=3, pady=3)
        ttk.Button(pad, text="⌂", width=5, command=self.home_camera).grid(row=1, column=1, padx=3, pady=3)
        ttk.Button(pad, text="→", width=5, command=lambda: self.move_relative(1, 0)).grid(row=1, column=2, padx=3, pady=3)
        ttk.Button(pad, text="↙", width=5, command=lambda: self.move_relative(-1, -1)).grid(row=2, column=0, padx=3, pady=3)
        ttk.Button(pad, text="↓", width=5, command=lambda: self.move_relative(0, -1)).grid(row=2, column=1, padx=3, pady=3)
        ttk.Button(pad, text="↘", width=5, command=lambda: self.move_relative(1, -1)).grid(row=2, column=2, padx=3, pady=3)

        zoom = ttk.Frame(frame, style="Panel.TFrame")
        zoom.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        ttk.Button(zoom, text="Zoom -", command=lambda: self.adjust_control("zoom_absolute", -1)).pack(side="left", expand=True, fill="x", padx=(0, 4))
        ttk.Button(zoom, text="Zoom +", command=lambda: self.adjust_control("zoom_absolute", 1)).pack(side="left", expand=True, fill="x", padx=(4, 0))

    def _build_quick_panel(self, parent: ttk.Frame) -> None:
        frame = ttk.LabelFrame(parent, text="Mission Actions", style="Card.TLabelframe", padding=12)
        frame.pack(fill="x")

        actions = [
            ("Set 720p Preview", lambda: self.set_preview_preset("1280x720", 30)),
            ("Set 1080p Preview", lambda: self.set_preview_preset("1920x1080", 30)),
            ("Auto Focus On", lambda: self.set_named_control("focus_automatic_continuous", 1)),
            ("Auto WB On", lambda: self.set_named_control("white_balance_automatic", 1)),
            ("Auto Exposure On", lambda: self.set_auto_exposure_auto()),
            ("Manual Focus Mode", lambda: self.set_named_control("focus_automatic_continuous", 0)),
        ]
        for label, command in actions:
            ttk.Button(frame, text=label, command=command).pack(fill="x", pady=3)

    def _build_scrollable_controls(self, parent: ttk.Frame) -> None:
        container = ttk.Frame(parent, style="Dark.TFrame")
        container.grid(row=0, column=0, sticky="nsew")
        container.rowconfigure(0, weight=1)
        container.columnconfigure(0, weight=1)

        self.canvas = tk.Canvas(container, bg="#07090d", highlightthickness=0)
        scrollbar = ttk.Scrollbar(container, orient="vertical", command=self.canvas.yview)
        self.controls_frame = ttk.Frame(self.canvas, style="Dark.TFrame")
        self.controls_window = self.canvas.create_window((0, 0), window=self.controls_frame, anchor="nw")
        self.canvas.configure(yscrollcommand=scrollbar.set)

        self.canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")

        self.controls_frame.bind("<Configure>", lambda _event: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda event: self.canvas.itemconfigure(self.controls_window, width=event.width))
        self.canvas.bind_all("<MouseWheel>", self._on_mousewheel)

    def _on_mousewheel(self, event: tk.Event) -> None:
        try:
            self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        except tk.TclError:
            pass

    def device(self) -> str:
        return self.device_var.get().strip() or DEFAULT_DEVICE

    def set_status(self, message: str) -> None:
        self.status_var.set(message)

    def refresh_all(self) -> None:
        device = self.device()
        if not Path(device).exists():
            self.controls = {}
            self.clear_dynamic_controls()
            self.set_status(f"{device} does not exist. Plug in the camera or select the correct device.")
            return

        try:
            self.controls = list_controls(device)
        except Exception as exc:
            self.controls = {}
            self.clear_dynamic_controls()
            messagebox.showerror(APP_NAME, str(exc))
            self.set_status("Unable to refresh camera controls.")
            return

        self.build_dynamic_controls()
        self.set_status(f"Loaded {len(self.controls)} V4L2 controls from {device}.")

    def clear_dynamic_controls(self) -> None:
        self.widget_registry.clear()
        for child in self.controls_frame.winfo_children():
            child.destroy()

    def build_dynamic_controls(self) -> None:
        self.clear_dynamic_controls()

        groups = [
            ("Core Image", ["brightness", "contrast", "saturation", "hue", "sharpness", "gain", "backlight_compensation", "power_line_frequency"]),
            ("White Balance", ["white_balance_automatic", "white_balance_temperature", "red_balance", "blue_balance"]),
            ("Exposure", ["auto_exposure", "exposure_time_absolute"]),
            ("Focus", ["focus_automatic_continuous", "focus_absolute"]),
            ("Pan / Tilt / Zoom", ["pan_absolute", "tilt_absolute", "zoom_absolute", "zoom_continuous", "pan_speed", "tilt_speed"]),
        ]

        for title, names in groups:
            available = [self.controls[name] for name in names if name in self.controls]
            if not available:
                continue
            card = ttk.LabelFrame(self.controls_frame, text=title, style="Card.TLabelframe", padding=12)
            card.pack(fill="x", pady=(0, 12), padx=(0, 8))
            card.columnconfigure(1, weight=1)
            for control in available:
                self.add_control_row(card, control)

        leftovers = [control for name, control in self.controls.items() if not any(name in group[1] for group in groups)]
        if leftovers:
            card = ttk.LabelFrame(self.controls_frame, text="Other Exposed Controls", style="Card.TLabelframe", padding=12)
            card.pack(fill="x", pady=(0, 12), padx=(0, 8))
            card.columnconfigure(1, weight=1)
            for control in leftovers:
                self.add_control_row(card, control)

    def add_control_row(self, parent: ttk.LabelFrame, control: ControlInfo) -> None:
        row = ttk.Frame(parent, style="Panel.TFrame")
        row.pack(fill="x", pady=6)
        row.columnconfigure(1, weight=1)

        label_text = control.name.replace("_", " ").title()
        ttk.Label(row, text=label_text, style="Panel.TLabel", width=26).grid(row=0, column=0, sticky="w", padx=(0, 8))

        self.widget_registry.setdefault(control.name, [])

        if "bool" in control.ctrl_type:
            self._add_bool_widget(row, control)
        elif "menu" in control.ctrl_type or control.menu_items:
            self._add_menu_widget(row, control)
        elif "int" in control.ctrl_type or "integer" in control.ctrl_type:
            self._add_int_widget(row, control)
        else:
            ttk.Label(row, text=f"Unsupported type: {control.ctrl_type}", style="Muted.TLabel").grid(row=0, column=1, sticky="w")

        meta = self.control_meta(control)
        ttk.Label(row, text=meta, style="Muted.TLabel").grid(row=1, column=1, sticky="w", pady=(3, 0))

    def _add_bool_widget(self, row: ttk.Frame, control: ControlInfo) -> None:
        var = tk.IntVar(value=int(control.value or 0))
        widget = ttk.Checkbutton(
            row,
            text="Enabled",
            variable=var,
            command=lambda: self.set_named_control(control.name, var.get(), refresh_after=True),
        )
        widget.grid(row=0, column=1, sticky="w")
        self.widget_registry[control.name].append(widget)
        if control.disabled:
            widget.state(["disabled"])

    def _add_menu_widget(self, row: ttk.Frame, control: ControlInfo) -> None:
        value_to_label = control.menu_items.copy()
        if not value_to_label:
            min_value = control.min_value if control.min_value is not None else 0
            max_value = control.max_value if control.max_value is not None else 3
            value_to_label = {value: str(value) for value in range(min_value, max_value + 1)}

        current_label = value_to_label.get(control.value if control.value is not None else 0, str(control.value))
        label_to_value = {label: value for value, label in value_to_label.items()}

        var = tk.StringVar(value=current_label)
        combo = ttk.Combobox(row, textvariable=var, values=list(label_to_value.keys()), state="readonly")
        combo.grid(row=0, column=1, sticky="ew")
        self.widget_registry[control.name].append(combo)

        def on_select(_event: tk.Event) -> None:
            label = var.get()
            value = label_to_value.get(label)
            if value is not None:
                self.set_named_control(control.name, value, refresh_after=True)

        combo.bind("<<ComboboxSelected>>", on_select)
        if control.disabled:
            combo.state(["disabled"])

    def _add_int_widget(self, row: ttk.Frame, control: ControlInfo) -> None:
        min_value = control.min_value if control.min_value is not None else 0
        max_value = control.max_value if control.max_value is not None else 100
        current = control.value if control.value is not None else min_value
        current = max(min_value, min(max_value, current))

        var = tk.IntVar(value=current)

        slider = ttk.Scale(
            row,
            from_=min_value,
            to=max_value,
            orient="horizontal",
            command=lambda raw: self.queue_slider_update(control.name, raw, var),
        )
        slider.set(current)
        slider.grid(row=0, column=1, sticky="ew")

        value_label = ttk.Label(row, textvariable=var, style="Panel.TLabel", width=8)
        value_label.grid(row=0, column=2, sticky="e", padx=(8, 0))

        apply_button = ttk.Button(row, text="Apply", command=lambda: self.set_named_control(control.name, var.get(), refresh_after=True))
        apply_button.grid(row=0, column=3, padx=(8, 0))

        default_button = ttk.Button(
            row,
            text="Default",
            command=lambda: self.set_named_control(control.name, control.default if control.default is not None else current, refresh_after=True),
        )
        default_button.grid(row=0, column=4, padx=(8, 0))

        self.widget_registry[control.name].extend([slider, value_label, apply_button, default_button])
        if control.disabled:
            slider.state(["disabled"])
            apply_button.configure(state="disabled")
            default_button.configure(state="disabled")

    def control_meta(self, control: ControlInfo) -> str:
        parts = [control.ctrl_type]
        if control.min_value is not None and control.max_value is not None:
            parts.append(f"range {control.min_value}..{control.max_value}")
        if control.step is not None:
            parts.append(f"step {control.step}")
        if control.default is not None:
            parts.append(f"default {control.default}")
        if control.flags:
            parts.append(f"flags {control.flags}")
        return " · ".join(parts)

    def queue_slider_update(self, name: str, raw: str, var: tk.IntVar) -> None:
        control = self.controls.get(name)
        if not control:
            return
        try:
            value = int(round(float(raw)))
        except ValueError:
            return
        value = control.clamp(value)
        var.set(value)

        existing = self.slider_jobs.get(name)
        if existing:
            self.after_cancel(existing)
        self.slider_jobs[name] = self.after(140, lambda: self.set_named_control(name, value, quiet=True))

    def set_named_control(self, name: str, value: int, quiet: bool = False, refresh_after: bool = False) -> bool:
        if name not in self.controls:
            if not quiet:
                messagebox.showwarning(APP_NAME, f"Camera does not expose {name}.")
            return False

        control = self.controls[name]
        value = control.clamp(int(value))
        ok, message = set_control(self.device(), name, value)
        if ok:
            if name in self.controls:
                self.controls[name].value = value
            self.set_status(f"{name} = {value}")
            if refresh_after:
                self.after(250, self.refresh_all)
            return True

        self.set_status(f"Failed to set {name} = {value}")
        if not quiet:
            messagebox.showerror(APP_NAME, message or f"Failed to set {name}={value}")
        return False

    def adjust_control(self, name: str, delta: int) -> None:
        self.refresh_control_cache_silent()
        control = self.controls.get(name)
        if not control:
            messagebox.showwarning(APP_NAME, f"Camera does not expose {name}.")
            return
        current = control.value if control.value is not None else control.default or 0
        step = control.step if control.step and control.step > 0 else 1
        self.set_named_control(name, current + (delta * step), refresh_after=True)

    def refresh_control_cache_silent(self) -> None:
        try:
            self.controls = list_controls(self.device())
        except Exception:
            pass

    def move_relative(self, pan_direction: int, tilt_direction: int) -> None:
        self.refresh_control_cache_silent()
        step_units = self.ptz_step_degrees_var.get() * PAN_TILT_UNITS_PER_DEGREE

        pan = self.controls.get("pan_absolute")
        tilt = self.controls.get("tilt_absolute")

        if pan_direction and pan:
            current = pan.value if pan.value is not None else 0
            self.set_named_control("pan_absolute", current + pan_direction * step_units, quiet=True)

        if tilt_direction and tilt:
            current = tilt.value if tilt.value is not None else 0
            self.set_named_control("tilt_absolute", current + tilt_direction * step_units, quiet=True)

        if not pan and not tilt:
            messagebox.showwarning(APP_NAME, "Camera does not expose pan_absolute or tilt_absolute.")
            return

        self.after(180, self.refresh_all)

    def home_camera(self) -> None:
        commands = [
            ("pan_absolute", 0),
            ("tilt_absolute", 0),
            ("zoom_absolute", 0),
        ]
        changed = 0
        for name, value in commands:
            if name in self.controls and self.set_named_control(name, value, quiet=True):
                changed += 1
        self.set_status(f"Camera home command sent. {changed} controls updated.")
        self.after(250, self.refresh_all)

    def safe_auto_reset(self) -> None:
        commands = [
            ("focus_automatic_continuous", 1),
            ("white_balance_automatic", 1),
            ("zoom_absolute", 0),
            ("pan_absolute", 0),
            ("tilt_absolute", 0),
        ]
        for name, value in commands:
            if name in self.controls:
                self.set_named_control(name, value, quiet=True)
        self.set_auto_exposure_auto(quiet=True)
        self.set_status("Safe auto reset sent: center, zoom out, auto focus, auto white balance, auto exposure.")
        self.after(350, self.refresh_all)

    def set_auto_exposure_auto(self, quiet: bool = False) -> None:
        control = self.controls.get("auto_exposure")
        if not control:
            if not quiet:
                messagebox.showwarning(APP_NAME, "Camera does not expose auto_exposure.")
            return

        # On this OBSBOT Meet SE, value 0 showed as Auto Mode.
        # If menu labels are present, prefer the item containing "auto".
        auto_value = 0
        for value, label in control.menu_items.items():
            if "auto" in label.lower():
                auto_value = value
                break
        self.set_named_control("auto_exposure", auto_value, quiet=quiet, refresh_after=True)

    def set_preview_preset(self, size: str, fps: int) -> None:
        self.preview_size_var.set(size)
        self.preview_fps_var.set(fps)
        self.set_status(f"Preview preset set to {size}@{fps}. Press Preview to launch.")

    def start_preview(self) -> None:
        if not command_exists("ffplay"):
            messagebox.showerror(APP_NAME, "ffplay is missing. Install it with: sudo apt install -y ffmpeg")
            return

        device = self.device()
        if not Path(device).exists():
            messagebox.showerror(APP_NAME, f"{device} does not exist.")
            return

        self.stop_preview(silent=True)
        size = self.preview_size_var.get().strip() or DEFAULT_PREVIEW_SIZE
        fps = int(self.preview_fps_var.get() or DEFAULT_PREVIEW_FPS)

        args = [
            "ffplay",
            "-hide_banner",
            "-loglevel",
            "warning",
            "-f",
            "v4l2",
            "-input_format",
            DEFAULT_PREVIEW_FORMAT,
            "-video_size",
            size,
            "-framerate",
            str(fps),
            device,
        ]

        try:
            self.preview_process = subprocess.Popen(args, text=True)
            self.set_status(f"Preview launched: {device} · {size}@{fps} · MJPEG")
        except Exception as exc:
            messagebox.showerror(APP_NAME, str(exc))

    def stop_preview(self, silent: bool = False) -> None:
        if self.preview_process and self.preview_process.poll() is None:
            try:
                self.preview_process.terminate()
                self.preview_process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.preview_process.kill()
            except Exception:
                pass
        self.preview_process = None
        if not silent:
            self.set_status("Preview stopped.")

    def take_snapshot(self) -> None:
        if not command_exists("ffmpeg"):
            messagebox.showerror(APP_NAME, "ffmpeg is missing. Install it with: sudo apt install -y ffmpeg")
            return

        default_name = f"darkstar-obsbot-{time.strftime('%Y%m%d-%H%M%S')}.jpg"
        path = filedialog.asksaveasfilename(
            title="Save darkStar Snapshot",
            initialdir=str(Path.home()),
            initialfile=default_name,
            defaultextension=".jpg",
            filetypes=[("JPEG image", "*.jpg"), ("All files", "*.*")],
        )
        if not path:
            return

        size = self.preview_size_var.get().strip() or DEFAULT_PREVIEW_SIZE
        args = [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "v4l2",
            "-input_format",
            DEFAULT_PREVIEW_FORMAT,
            "-video_size",
            size,
            "-i",
            self.device(),
            "-frames:v",
            "1",
            path,
        ]
        result = run_command(args)
        if result.returncode == 0:
            self.set_status(f"Snapshot saved: {path}")
        else:
            messagebox.showerror(APP_NAME, result.stderr.strip() or "Snapshot failed. Close preview if it is using the camera.")

    def show_devices(self) -> None:
        self.show_text_popup("V4L2 Devices", list_devices_text() or "No devices returned.")

    def show_formats(self) -> None:
        try:
            text = list_formats_text(self.device())
        except Exception as exc:
            text = str(exc)
        self.show_text_popup(f"Formats for {self.device()}", text or "No formats returned.")

    def show_text_popup(self, title: str, text: str) -> None:
        popup = tk.Toplevel(self)
        popup.title(title)
        popup.geometry("820x520")
        popup.configure(bg="#07090d")
        popup.transient(self)

        box = tk.Text(
            popup,
            wrap="word",
            bg="#0f172a",
            fg="#e2e8f0",
            insertbackground="#e2e8f0",
            relief="flat",
            padx=12,
            pady=12,
        )
        box.pack(fill="both", expand=True, padx=12, pady=12)
        box.insert("1.0", text)
        box.configure(state="disabled")
        ttk.Button(popup, text="Close", command=popup.destroy).pack(pady=(0, 12))

    def close_app(self) -> None:
        self.stop_preview(silent=True)
        self.destroy()


def ensure_dependencies() -> List[str]:
    missing = []
    for command in ["v4l2-ctl", "ffplay", "ffmpeg"]:
        if not command_exists(command):
            missing.append(command)
    return missing


def main() -> int:
    missing = ensure_dependencies()
    if missing:
        print(f"Missing command(s): {', '.join(missing)}", file=sys.stderr)
        print("Install dependencies with:", file=sys.stderr)
        print("  sudo apt update && sudo apt install -y python3-tk v4l-utils ffmpeg", file=sys.stderr)
        return 1

    app = DarkStarApp()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
