#!/usr/bin/env python3
"""
By Tristan Smith @theProjet · github.com/theProject
Project darkStar: The OBS Raspberry Pi-Pass
Version 3 - Clean recording, optional timestamp overlay, camera audio, timer, and review scrubbing.

Pi-native OBSBOT / UVC camera control panel using standard Linux V4L2 controls.

This app avoids the x86_64-only OBSBOT SDK and does not require OBS Studio.
It controls whatever the camera exposes through /dev/video0 via v4l2-ctl,
previews through ffplay, and records sessions through ffmpeg.

Recommended packages:
    sudo apt update
    sudo apt install -y python3-tk v4l-utils ffmpeg alsa-utils fonts-dejavu-core

Run:
    python3 project_darkstar_obs_bypass.py
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import tkinter as tk
from tkinter import filedialog, messagebox, ttk


APP_NAME = "Project darkStar: The OBS Raspberry Pi-Pass"
APP_VERSION = "3.0.0"
DEFAULT_DEVICE = "/dev/video0"
DEFAULT_PREVIEW_SIZE = "1280x720"
DEFAULT_PREVIEW_FPS = 30
DEFAULT_PREVIEW_FORMAT = "mjpeg"
PAN_TILT_UNITS_PER_DEGREE = 3600
CONFIG_DIR = Path.home() / ".config" / "darkstar-pi-pass"
CONFIG_FILE = CONFIG_DIR / "config.json"
DEFAULT_RECORD_DIR = Path.home() / "Videos" / "darkStar"
DEFAULT_FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"


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


def list_alsa_capture_devices() -> List[str]:
    """Return friendly ALSA capture device strings, e.g. hw:2,0 - OBSBOT Meet SE."""
    if not command_exists("arecord"):
        return []

    result = run_command(["arecord", "-l"])
    if result.returncode != 0:
        return []

    devices: List[str] = []
    current_card_label = ""
    card_pattern = re.compile(r"^card\s+(\d+):\s+([^\[]+)\[([^\]]+)\],\s+device\s+(\d+):\s+([^\[]+)\[([^\]]+)\]")

    for line in result.stdout.splitlines():
        stripped = line.strip()
        match = card_pattern.match(stripped)
        if not match:
            continue
        card_num, short_card, long_card, dev_num, short_dev, long_dev = match.groups()
        hw = f"hw:{card_num},{dev_num}"
        label = f"{long_card.strip()} / {long_dev.strip()}"
        devices.append(f"{hw}  -  {label}")

    return devices


def seconds_to_clock(seconds: int) -> str:
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def safe_filename_stamp() -> str:
    return dt.datetime.now().strftime("%Y%m%d-%H%M%S")


def load_config() -> Dict[str, str]:
    if not CONFIG_FILE.exists():
        return {}
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_config(config: Dict[str, str]) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(config, indent=2), encoding="utf-8")


def truthy_config(value: Optional[str], default: bool = False) -> bool:
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


class DarkStarApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()

        self.config_data = load_config()

        self.title(f"{APP_NAME} v{APP_VERSION}")
        self.geometry("1220x900")
        self.minsize(1040, 740)

        self.device_var = tk.StringVar(value=self.config_data.get("device", DEFAULT_DEVICE))
        self.status_var = tk.StringVar(value="Standing by. Camera controls will load from /dev/video0.")
        self.preview_size_var = tk.StringVar(value=self.config_data.get("preview_size", DEFAULT_PREVIEW_SIZE))
        self.preview_fps_var = tk.IntVar(value=int(self.config_data.get("preview_fps", DEFAULT_PREVIEW_FPS)))
        self.ptz_step_degrees_var = tk.IntVar(value=int(self.config_data.get("ptz_step", 10)))
        self.record_dir_var = tk.StringVar(value=self.config_data.get("record_dir", str(DEFAULT_RECORD_DIR)))
        self.record_preset_var = tk.StringVar(value=self.config_data.get("record_preset", "720p30"))
        self.record_container_var = tk.StringVar(value=self.config_data.get("record_container", "mkv"))
        self.audio_enabled_var = tk.BooleanVar(value=truthy_config(self.config_data.get("audio_enabled"), False))
        self.audio_device_var = tk.StringVar(value=self.config_data.get("audio_device", "default"))
        self.timestamp_enabled_var = tk.BooleanVar(value=truthy_config(self.config_data.get("timestamp_enabled"), False))
        self.timestamp_position_var = tk.StringVar(value=self.config_data.get("timestamp_position", "Bottom Left"))
        self.timestamp_text_var = tk.StringVar(value=self.config_data.get("timestamp_text", "darkStar %Y-%m-%d %H\\:%M\\:%S"))
        self.record_status_var = tk.StringVar(value="Not recording")
        self.record_timer_var = tk.StringVar(value="00:00:00")
        self.review_offset_var = tk.IntVar(value=0)
        self.review_length_var = tk.StringVar(value="Last recording: none")

        self.controls: Dict[str, ControlInfo] = {}
        self.preview_process: Optional[subprocess.Popen[str]] = None
        self.record_process: Optional[subprocess.Popen[str]] = None
        self.record_started_at: Optional[float] = None
        self.record_timer_job: Optional[str] = None
        self.record_log_file: Optional[object] = None
        self.last_recording_path: Optional[Path] = None
        self.last_recording_seconds: int = 0
        self.review_process: Optional[subprocess.Popen[str]] = None
        self.slider_jobs: Dict[str, str] = {}
        self.widget_registry: Dict[str, List[tk.Widget]] = {}

        self._configure_theme()
        self._build_layout()
        self.refresh_audio_devices()
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
        style.configure("Record.TLabel", background="#10151f", foreground="#f87171", font=("TkDefaultFont", 12, "bold"))
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
        ttk.Label(title_box, text="The OBS Raspberry Pi-Pass · clean capture, optional timestamp, camera audio", style="Subheader.TLabel").pack(anchor="w")

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
        self._build_record_panel(left)
        self._build_review_panel(left)
        self._build_ptz_panel(left)
        self._build_quick_panel(left)

        right = ttk.Frame(main, style="Dark.TFrame")
        right.grid(row=0, column=1, sticky="nsew")
        right.rowconfigure(0, weight=1)
        right.columnconfigure(0, weight=1)
        self._build_scrollable_controls(right)

        status = ttk.Label(self, textvariable=self.status_var, style="Status.TLabel", anchor="w")
        status.grid(row=3, column=0, sticky="ew")

    def _build_record_panel(self, parent: ttk.Frame) -> None:
        frame = ttk.LabelFrame(parent, text="Session Recorder", style="Card.TLabelframe", padding=12)
        frame.pack(fill="x", pady=(0, 12))
        frame.columnconfigure(1, weight=1)

        ttk.Label(frame, textvariable=self.record_status_var, style="Record.TLabel").grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(frame, textvariable=self.record_timer_var, style="Panel.TLabel", font=("TkDefaultFont", 18, "bold")).grid(
            row=1, column=0, columnspan=3, sticky="w", pady=(4, 10)
        )

        ttk.Button(frame, text="Start Recording", command=self.start_recording).grid(row=2, column=0, sticky="ew", padx=(0, 6), pady=3)
        ttk.Button(frame, text="Stop Recording", command=self.stop_recording).grid(row=2, column=1, sticky="ew", padx=(0, 6), pady=3)
        ttk.Button(frame, text="Open Folder", command=self.open_record_folder).grid(row=2, column=2, sticky="ew", pady=3)

        ttk.Label(frame, text="Preset", style="Panel.TLabel").grid(row=3, column=0, sticky="w", pady=(10, 3))
        ttk.Combobox(
            frame,
            textvariable=self.record_preset_var,
            values=["720p30", "1080p30", "720p15", "1080p15", "480p30"],
            state="readonly",
            width=10,
        ).grid(row=3, column=1, sticky="ew", pady=(10, 3), padx=(0, 6))
        ttk.Combobox(
            frame,
            textvariable=self.record_container_var,
            values=["mkv", "mp4"],
            state="readonly",
            width=6,
        ).grid(row=3, column=2, sticky="ew", pady=(10, 3))

        ttk.Label(frame, text="Save folder", style="Panel.TLabel").grid(row=4, column=0, sticky="w", pady=3)
        ttk.Entry(frame, textvariable=self.record_dir_var, width=24).grid(row=4, column=1, sticky="ew", pady=3, padx=(0, 6))
        ttk.Button(frame, text="Choose", command=self.choose_record_folder).grid(row=4, column=2, sticky="ew", pady=3)

        ttk.Checkbutton(frame, text="Record audio", variable=self.audio_enabled_var, command=self.save_preferences).grid(
            row=5, column=0, columnspan=3, sticky="w", pady=(10, 3)
        )

        ttk.Label(frame, text="Audio source", style="Panel.TLabel").grid(row=6, column=0, sticky="w", pady=3)
        self.audio_combo = ttk.Combobox(frame, textvariable=self.audio_device_var, values=["default"], width=24)
        self.audio_combo.grid(row=6, column=1, sticky="ew", pady=3, padx=(0, 6))
        ttk.Button(frame, text="Audio Scan", command=self.refresh_audio_devices).grid(row=6, column=2, sticky="ew", pady=3)

        ttk.Checkbutton(frame, text="Burn timestamp overlay", variable=self.timestamp_enabled_var, command=self.save_preferences).grid(
            row=7, column=0, columnspan=3, sticky="w", pady=(10, 3)
        )

        ttk.Label(frame, text="Stamp pos", style="Panel.TLabel").grid(row=8, column=0, sticky="w", pady=3)
        ttk.Combobox(
            frame,
            textvariable=self.timestamp_position_var,
            values=["Bottom Left", "Bottom Right", "Top Left", "Top Right"],
            state="readonly",
            width=14,
        ).grid(row=8, column=1, sticky="ew", pady=3, padx=(0, 6))
        ttk.Button(frame, text="Clean Default", command=self.disable_timestamp_overlay).grid(row=8, column=2, sticky="ew", pady=3)

        ttk.Label(frame, text="Stamp text", style="Panel.TLabel").grid(row=9, column=0, sticky="w", pady=3)
        ttk.Entry(frame, textvariable=self.timestamp_text_var, width=24).grid(row=9, column=1, columnspan=2, sticky="ew", pady=3)

    def _build_review_panel(self, parent: ttk.Frame) -> None:
        frame = ttk.LabelFrame(parent, text="Review / Light Scrub", style="Card.TLabelframe", padding=12)
        frame.pack(fill="x", pady=(0, 12))
        frame.columnconfigure(0, weight=1)

        ttk.Label(frame, textvariable=self.review_length_var, style="Muted.TLabel", wraplength=330).grid(row=0, column=0, columnspan=3, sticky="w")
        self.review_scale = ttk.Scale(
            frame,
            from_=0,
            to=0,
            orient="horizontal",
            command=lambda raw: self.review_offset_var.set(int(float(raw))),
        )
        self.review_scale.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(8, 3))
        ttk.Label(frame, textvariable=self.review_offset_var, style="Panel.TLabel").grid(row=2, column=0, sticky="w")
        ttk.Button(frame, text="Play From Slider", command=self.play_last_recording_from_slider).grid(row=2, column=1, sticky="ew", padx=(6, 6))
        ttk.Button(frame, text="Stop Review", command=self.stop_review).grid(row=2, column=2, sticky="ew")

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

    def disable_timestamp_overlay(self) -> None:
        self.timestamp_enabled_var.set(False)
        self.save_preferences()
        self.set_status("Timestamp overlay disabled. Future recordings will be clean unless you turn it back on.")

    def save_preferences(self) -> None:
        config = {
            "device": self.device_var.get(),
            "preview_size": self.preview_size_var.get(),
            "preview_fps": str(self.preview_fps_var.get()),
            "ptz_step": str(self.ptz_step_degrees_var.get()),
            "record_dir": self.record_dir_var.get(),
            "record_preset": self.record_preset_var.get(),
            "record_container": self.record_container_var.get(),
            "audio_enabled": "true" if self.audio_enabled_var.get() else "false",
            "audio_device": self.audio_device_var.get(),
            "timestamp_enabled": "true" if self.timestamp_enabled_var.get() else "false",
            "timestamp_position": self.timestamp_position_var.get(),
            "timestamp_text": self.timestamp_text_var.get(),
        }
        save_config(config)

    def refresh_all(self) -> None:
        self.save_preferences()
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
        commands = [("pan_absolute", 0), ("tilt_absolute", 0), ("zoom_absolute", 0)]
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

        auto_value = 0
        for value, label in control.menu_items.items():
            if "auto" in label.lower():
                auto_value = value
                break
        self.set_named_control("auto_exposure", auto_value, quiet=quiet, refresh_after=True)

    def set_preview_preset(self, size: str, fps: int) -> None:
        self.preview_size_var.set(size)
        self.preview_fps_var.set(fps)
        self.save_preferences()
        self.set_status(f"Preview preset set to {size}@{fps}. Press Preview to launch.")

    def start_preview(self) -> None:
        if not command_exists("ffplay"):
            messagebox.showerror(APP_NAME, "ffplay is missing. Install it with: sudo apt install -y ffmpeg")
            return

        device = self.device()
        if not Path(device).exists():
            messagebox.showerror(APP_NAME, f"{device} does not exist.")
            return

        if self.record_process and self.record_process.poll() is None:
            messagebox.showwarning(APP_NAME, "Recording is active. This camera may not allow preview and recording at the same time.")

        self.stop_preview(silent=True)
        self.save_preferences()
        size = self.preview_size_var.get().strip() or DEFAULT_PREVIEW_SIZE
        fps = int(self.preview_fps_var.get() or DEFAULT_PREVIEW_FPS)

        args = [
            "ffplay",
            "-hide_banner",
            "-loglevel",
            "error",
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

        default_name = f"darkstar-obsbot-{safe_filename_stamp()}.jpg"
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

    def refresh_audio_devices(self) -> None:
        devices = ["default"] + list_alsa_capture_devices()
        if hasattr(self, "audio_combo"):
            self.audio_combo.configure(values=devices)
        if self.audio_device_var.get() not in devices:
            obsbot_match = next((item for item in devices if "obsbot" in item.lower() or "remo" in item.lower() or "meet" in item.lower()), None)
            self.audio_device_var.set(obsbot_match or "default")
        self.save_preferences()

    def choose_record_folder(self) -> None:
        selected = filedialog.askdirectory(title="Choose recording folder", initialdir=self.record_dir_var.get() or str(Path.home()))
        if selected:
            self.record_dir_var.set(selected)
            self.save_preferences()
            self.set_status(f"Recording folder set: {selected}")

    def open_record_folder(self) -> None:
        folder = Path(self.record_dir_var.get()).expanduser()
        folder.mkdir(parents=True, exist_ok=True)
        try:
            subprocess.Popen(["xdg-open", str(folder)])
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"Could not open folder: {exc}")

    def record_preset_to_size_fps(self) -> Tuple[str, int]:
        preset = self.record_preset_var.get()
        mapping = {
            "720p30": ("1280x720", 30),
            "1080p30": ("1920x1080", 30),
            "720p15": ("1280x720", 15),
            "1080p15": ("1920x1080", 15),
            "480p30": ("640x480", 30),
        }
        return mapping.get(preset, ("1280x720", 30))

    def selected_audio_hw(self) -> str:
        selected = self.audio_device_var.get().strip()
        if selected.startswith("hw:"):
            return selected.split()[0]
        return selected or "default"

    def timestamp_filter(self) -> str:
        text = self.timestamp_text_var.get().strip() or "darkStar %Y-%m-%d %H\\:%M\\:%S"
        escaped = text.replace("'", r"\'")
        position = self.timestamp_position_var.get()

        x = "20"
        y = "h-th-20"
        if position == "Bottom Right":
            x = "w-tw-20"
            y = "h-th-20"
        elif position == "Top Left":
            x = "20"
            y = "20"
        elif position == "Top Right":
            x = "w-tw-20"
            y = "20"

        font_clause = f"fontfile={DEFAULT_FONT}:" if Path(DEFAULT_FONT).exists() else ""
        return (
            "drawtext="
            f"{font_clause}"
            f"text='{escaped}':"
            "fontcolor=white:fontsize=24:"
            "box=1:boxcolor=black@0.55:boxborderw=10:"
            f"x={x}:y={y}"
        )

    def build_record_command(self, output_path: Path) -> List[str]:
        size, fps = self.record_preset_to_size_fps()
        burn_timestamp = self.timestamp_enabled_var.get()

        args = [
            "ffmpeg",
            "-y",
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
            "-i",
            self.device(),
        ]

        if self.audio_enabled_var.get():
            args.extend(["-f", "alsa", "-thread_queue_size", "1024", "-i", self.selected_audio_hw()])

        if burn_timestamp:
            # Timestamp overlay requires decoding and re-encoding. Ultrafast keeps this realistic on a Pi.
            args.extend(["-vf", self.timestamp_filter(), "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23", "-pix_fmt", "yuv420p"])
        else:
            # Clean mode copies the MJPEG camera stream directly. Fast, light, and no burned overlay.
            args.extend(["-c:v", "copy"])

        if self.audio_enabled_var.get():
            args.extend(["-c:a", "aac", "-b:a", "128k", "-ar", "48000"])
        else:
            args.append("-an")

        if output_path.suffix.lower() == ".mp4":
            args.extend(["-movflags", "+faststart"])

        args.append(str(output_path))
        return args

    def start_recording(self) -> None:
        if not command_exists("ffmpeg"):
            messagebox.showerror(APP_NAME, "ffmpeg is missing. Install it with: sudo apt install -y ffmpeg")
            return

        if self.record_process and self.record_process.poll() is None:
            messagebox.showinfo(APP_NAME, "Recording is already running.")
            return

        if self.preview_process and self.preview_process.poll() is None:
            answer = messagebox.askyesno(
                APP_NAME,
                "Preview is currently running. Many cameras only allow one app to use /dev/video0 at a time.\n\nStop preview and start recording?",
            )
            if not answer:
                return
            self.stop_preview(silent=True)

        device = self.device()
        if not Path(device).exists():
            messagebox.showerror(APP_NAME, f"{device} does not exist.")
            return

        self.save_preferences()
        folder = Path(self.record_dir_var.get()).expanduser()
        folder.mkdir(parents=True, exist_ok=True)

        container = self.record_container_var.get().lower().strip()
        if container not in {"mkv", "mp4"}:
            container = "mkv"

        preset = self.record_preset_var.get()
        audio_label = "audio" if self.audio_enabled_var.get() else "silent"
        stamp_label = "timestamp" if self.timestamp_enabled_var.get() else "clean"
        output_path = folder / f"darkstar-session-{preset}-{audio_label}-{stamp_label}-{safe_filename_stamp()}.{container}"
        command = self.build_record_command(output_path)

        log_path = folder / f"{output_path.stem}.log"
        try:
            self.record_log_file = open(log_path, "w", encoding="utf-8")
            self.record_log_file.write("Command:\n" + " ".join(command) + "\n\n")
            self.record_log_file.flush()
            self.record_process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=self.record_log_file,
                stderr=self.record_log_file,
                text=True,
                start_new_session=True,
            )
        except Exception as exc:
            if self.record_log_file:
                self.record_log_file.close()
                self.record_log_file = None
            messagebox.showerror(APP_NAME, f"Could not start recording: {exc}")
            return

        self.last_recording_path = output_path
        self.last_recording_seconds = 0
        self.record_started_at = time.monotonic()
        self.record_status_var.set(f"● Recording: {output_path.name}")
        self.record_timer_var.set("00:00:00")
        self.set_status(f"Recording started: {output_path}")
        self.update_record_timer()

    def update_record_timer(self) -> None:
        if not self.record_process or self.record_process.poll() is not None or self.record_started_at is None:
            self.record_timer_job = None
            return

        elapsed = int(time.monotonic() - self.record_started_at)
        self.record_timer_var.set(seconds_to_clock(elapsed))
        self.record_timer_job = self.after(500, self.update_record_timer)

    def stop_recording(self) -> None:
        if not self.record_process or self.record_process.poll() is not None:
            self.record_status_var.set("Not recording")
            self.set_status("No active recording to stop.")
            return

        elapsed = int(time.monotonic() - self.record_started_at) if self.record_started_at else 0
        self.last_recording_seconds = max(elapsed, 1)

        try:
            if self.record_process.stdin:
                # Ask ffmpeg to stop cleanly so MKV/MP4 headers finalize.
                self.record_process.stdin.write("q\n")
                self.record_process.stdin.flush()
            self.record_process.wait(timeout=8)
        except Exception:
            try:
                os.killpg(os.getpgid(self.record_process.pid), signal.SIGINT)
                self.record_process.wait(timeout=5)
            except Exception:
                self.record_process.kill()

        if self.record_timer_job:
            self.after_cancel(self.record_timer_job)
            self.record_timer_job = None

        if self.record_log_file:
            self.record_log_file.close()
            self.record_log_file = None

        self.record_process = None
        self.record_started_at = None
        self.record_status_var.set("Not recording")
        self.record_timer_var.set(seconds_to_clock(elapsed))

        if self.last_recording_path:
            self.review_scale.configure(to=max(self.last_recording_seconds, 1))
            self.review_offset_var.set(0)
            self.review_length_var.set(f"Last recording: {self.last_recording_path.name} · {seconds_to_clock(self.last_recording_seconds)}")
            self.set_status(f"Recording saved: {self.last_recording_path}")
        else:
            self.set_status("Recording stopped.")

    def play_last_recording_from_slider(self) -> None:
        if not self.last_recording_path or not self.last_recording_path.exists():
            messagebox.showinfo(APP_NAME, "No finished recording is available to review yet.")
            return
        if not command_exists("ffplay"):
            messagebox.showerror(APP_NAME, "ffplay is missing. Install it with: sudo apt install -y ffmpeg")
            return

        self.stop_review(silent=True)
        offset = max(0, int(self.review_offset_var.get()))
        args = [
            "ffplay",
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            str(offset),
            str(self.last_recording_path),
        ]
        try:
            self.review_process = subprocess.Popen(args, text=True)
            self.set_status(f"Review playing from {seconds_to_clock(offset)}: {self.last_recording_path.name}")
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"Could not open review: {exc}")

    def stop_review(self, silent: bool = False) -> None:
        if self.review_process and self.review_process.poll() is None:
            try:
                self.review_process.terminate()
                self.review_process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.review_process.kill()
            except Exception:
                pass
        self.review_process = None
        if not silent:
            self.set_status("Review stopped.")

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
        if self.record_process and self.record_process.poll() is None:
            answer = messagebox.askyesno(APP_NAME, "Recording is still active. Stop recording and exit?")
            if not answer:
                return
            self.stop_recording()
        self.save_preferences()
        self.stop_preview(silent=True)
        self.stop_review(silent=True)
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
        print("  sudo apt update && sudo apt install -y python3-tk v4l-utils ffmpeg alsa-utils fonts-dejavu-core", file=sys.stderr)
        return 1

    app = DarkStarApp()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
