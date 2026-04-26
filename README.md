README.md
# darkStar: The OBS Raspberry Pi-Pass

A Raspberry Pi-native control panel for OBSBOT-style USB cameras — built because OBS Studio does not always play nicely on Raspberry Pi, and because some vendor camera control SDKs are locked to x86_64 Linux.

**darkStar: The OBS Raspberry Pi-Pass** is a small Python/Tkinter GUI that talks directly to Linux's standard V4L2 camera controls. It lets you preview, move, tune, reset, and capture from a compatible USB camera without relying on OBS Studio, proprietary desktop software, or an x86-only SDK.

The name is a little joke: it is an OBS “by-pass,” but for Raspberry Pi. So naturally, it became the **Raspberry Pi-Pass**.

---

## Why this exists

This project started from a simple problem: an OBSBOT Meet SE worked beautifully as a USB webcam on a Raspberry Pi, but the usual Linux control app could not build because its bundled OBSBOT SDK library was compiled for **x86_64**, while the Pi was running **aarch64 / ARM64**.

The camera itself was fine. Linux could see it. `ffplay` could preview it. V4L2 exposed useful controls like pan, tilt, zoom, focus, exposure, white balance, brightness, contrast, and sharpness.

The missing piece was a friendly control surface.

So this project skips the proprietary SDK path entirely and uses the controls the camera already exposes through Linux.

Instead of fighting this:

```text
Raspberry Pi ARM64 → x86_64-only vendor SDK → build failure
```

This app uses this:

```text
Raspberry Pi ARM64 → /dev/video0 → v4l2-ctl + ffplay → working camera control
```

---

## What it solves

`darkStar` solves a very specific but frustrating gap:

* OBS Studio may fail to launch on Raspberry Pi because of Wayland, EGL, OpenGL, or graphics backend issues.
* Some camera control projects depend on vendor SDK binaries that are not available for ARM64.
* A camera can work perfectly as a UVC webcam, yet still feel awkward to control from the command line.
* `v4l2-ctl` is powerful, but not exactly something you want to use manually every time you want to pan, zoom, focus, or reset your camera.

This app gives those controls a simple GUI.

It is not trying to replace OBS Studio forever. It is a practical bypass when you just need the camera to work on the Pi.

---

## Features

* Native Raspberry Pi / ARM64-friendly Python GUI
* No OBS Studio required for preview
* No OBSBOT proprietary SDK required
* Uses standard Linux V4L2 controls through `v4l2-ctl`
* Live camera preview through `ffplay`
* PTZ-style D-pad for pan and tilt
* Home / center camera command
* Zoom in and out controls
* Snapshot capture
* 720p and 1080p preview presets
* Brightness, contrast, saturation, hue, gain, sharpness, and backlight controls
* Auto focus toggle
* Manual focus support when exposed by the camera
* Auto exposure reset
* Manual exposure support when exposed by the camera
* Auto white balance toggle
* Manual white balance support when exposed by the camera
* Device list viewer
* Format list viewer
* Safe reset command for returning the camera to sane defaults

---

## Tested camera

This project was originally built around:

```text
OBSBOT Meet SE
```

Detected on Linux as:

```text
OBSBOT Meet SE: OBSBOT Meet SE  (usb-xhci-hcd.0-1):
    /dev/video0
    /dev/video1
    /dev/media3
```

The camera exposed controls such as:

```text
pan_absolute
tilt_absolute
zoom_absolute
focus_absolute
focus_automatic_continuous
auto_exposure
exposure_time_absolute
white_balance_automatic
white_balance_temperature
brightness
contrast
saturation
sharpness
```

Other USB cameras may work if they expose useful V4L2 controls.

---

## How it works

```mermaid
graph TD
    A[USB Camera / OBSBOT Meet SE] --> B[Linux UVC Driver]
    B --> C[/dev/video0]
    C --> D[v4l2-ctl]
    C --> E[ffplay]
    D --> F[Camera Controls]
    E --> G[Live Preview]
    F --> H[darkStar GUI]
    G --> H
    H --> I[Pan / Tilt / Zoom]
    H --> J[Focus / Exposure / White Balance]
    H --> K[Image Tuning]
    H --> L[Snapshot Capture]
```

The app does not decode or control the camera through a private SDK. It calls the same Linux tools you would normally use manually:

```bash
v4l2-ctl --device=/dev/video0 --list-ctrls-menus
v4l2-ctl --device=/dev/video0 --set-ctrl=zoom_absolute=4
ffplay -f v4l2 -input_format mjpeg -video_size 1280x720 -framerate 30 /dev/video0
```

The GUI simply makes those controls easier to use.

---

## Requirements

Install the required packages:

```bash
sudo apt update
sudo apt install -y python3-tk v4l-utils ffmpeg
```

You need:

* Raspberry Pi OS, Debian, Kali, or another Linux system with V4L2 support
* Python 3
* Tkinter
* `v4l2-ctl`
* `ffmpeg` / `ffplay`
* A USB camera that appears as a V4L2 video device, usually `/dev/video0`

---

## Quick start

Clone the repo:

```bash
git clone https://github.com/YOUR_USERNAME/darkstar-obs-raspberry-pi-pass.git
cd darkstar-obs-raspberry-pi-pass
```

Install dependencies:

```bash
sudo apt update
sudo apt install -y python3-tk v4l-utils ffmpeg
```

Run the app:

```bash
python3 project_darkstar_obs_bypass.py
```

If your camera is not on `/dev/video0`, list devices:

```bash
v4l2-ctl --list-devices
```

Then enter the correct device path in the app, such as:

```text
/dev/video1
```

---

## Confirm your camera works first

Before using the GUI, make sure Linux sees your camera:

```bash
v4l2-ctl --list-devices
```

Check supported formats:

```bash
v4l2-ctl --device=/dev/video0 --list-formats-ext
```

Check exposed camera controls:

```bash
v4l2-ctl --device=/dev/video0 --list-ctrls-menus
```

Test a live preview:

```bash
ffplay -f v4l2 -input_format mjpeg -video_size 1280x720 -framerate 30 /dev/video0
```

If that works, `darkStar` should be able to launch a preview too.

---

## Recommended OBSBOT Meet SE preview command

For the OBSBOT Meet SE on Raspberry Pi, this worked well:

```bash
ffplay -f v4l2 -input_format mjpeg -video_size 1280x720 -framerate 30 /dev/video0
```

1080p may also work:

```bash
ffplay -f v4l2 -input_format mjpeg -video_size 1920x1080 -framerate 30 /dev/video0
```

If 1080p feels laggy, 720p30 is a strong default for the Pi.

---

## Optional install into `~/bin`

```bash
mkdir -p ~/bin
cp project_darkstar_obs_bypass.py ~/bin/darkstar-pi-pass
chmod +x ~/bin/darkstar-pi-pass
```

Run it with:

```bash
darkstar-pi-pass
```

If `~/bin` is not in your PATH, run:

```bash
echo 'export PATH="$HOME/bin:$PATH"' >> ~/.bashrc
source ~/.bashrc
```

---

## Optional desktop launcher

Create a desktop menu entry:

```bash
mkdir -p ~/.local/share/applications

cat > ~/.local/share/applications/darkstar-pi-pass.desktop <<'EOF'
[Desktop Entry]
Type=Application
Name=darkStar Pi-Pass
Comment=Raspberry Pi-native OBSBOT camera control panel
Exec=/home/darkstar/bin/darkstar-pi-pass
Icon=camera-web
Terminal=false
Categories=AudioVideo;Video;
EOF

chmod +x ~/.local/share/applications/darkstar-pi-pass.desktop
update-desktop-database ~/.local/share/applications 2>/dev/null || true
```

Adjust the `Exec=` path if your username is not `darkstar`.

---

## Camera controls explained

Some cameras expose a lot of controls. Some expose very few. This app only shows what the camera reports through V4L2.

### Pan and tilt

Many PTZ-style camera controls use large numeric ranges. On the OBSBOT Meet SE, pan and tilt looked like this:

```text
pan_absolute  min=-648000 max=648000 step=3600 default=0
tilt_absolute min=-648000 max=648000 step=3600 default=0
```

A step of `3600` is roughly one degree. The app turns this into a simple D-pad.

### Zoom

The OBSBOT Meet SE exposed:

```text
zoom_absolute min=0 max=12 step=1 default=0
```

The app uses `zoom_absolute` for reliable zoom steps.

### Focus

If autofocus is enabled, manual focus may appear inactive. Turn off continuous autofocus first, then adjust manual focus.

```bash
v4l2-ctl -d /dev/video0 --set-ctrl=focus_automatic_continuous=0
v4l2-ctl -d /dev/video0 --set-ctrl=focus_absolute=50
```

### Exposure

If auto exposure is enabled, manual exposure may appear inactive. Switch exposure mode first, then adjust exposure time.

### White balance

If automatic white balance is enabled, manual white balance temperature may appear inactive. Disable auto white balance first if you want manual control.

---

## Known limitations

This project is intentionally simple and practical.

It does not currently provide:

* OBSBOT proprietary SDK features that are not exposed through V4L2
* Guaranteed AI tracking controls
* Firmware updates
* Vendor-specific mode switching
* Built-in video recording
* Embedded preview inside the Tkinter window
* Multi-camera scene composition like OBS Studio

The preview opens in an `ffplay` window because that is reliable, lightweight, and avoids fighting Pi graphics backends.

Also, many cameras allow only one app to use `/dev/video0` at a time. If snapshot capture fails while preview is running, stop preview first and try again.

---

## Why not just use OBS Studio?

You should use OBS Studio when it works. It is powerful and flexible.

This project exists because on some Raspberry Pi desktop setups, OBS may fail before it ever reaches the camera. For example, it can fail while creating an EGL/OpenGL context under Wayland. In that situation, the camera can still work perfectly through V4L2 and `ffplay`, but OBS itself is blocked by graphics initialization.

`darkStar` bypasses that problem by avoiding OBS completely for basic camera control and preview.

---

## Why not use the OBSBOT SDK?

Some Linux OBSBOT control projects bundle a vendor SDK library. That can work on x86_64 Linux machines, but it may not work on Raspberry Pi if the library is not available for ARM64.

This project avoids that architecture trap.

If the camera exposes a control through V4L2, `darkStar` can use it. If the camera only exposes a feature through a proprietary SDK, this app will not see that feature.

That tradeoff is deliberate.

---

## Troubleshooting

### Camera does not appear

Run:

```bash
lsusb
v4l2-ctl --list-devices
```

If the camera does not appear, try:

* A known-good USB data cable
* A different USB port
* A powered USB hub
* Rebooting with the camera plugged in

### Permission denied

Add your user to the `video` and `audio` groups:

```bash
sudo usermod -aG video,audio "$USER"
sudo reboot
```

### Preview works, but snapshot fails

Close the preview first. Some cameras only allow one program to use `/dev/video0` at a time.

### Manual focus or exposure is inactive

Disable the related automatic mode first:

```bash
v4l2-ctl -d /dev/video0 --set-ctrl=focus_automatic_continuous=0
v4l2-ctl -d /dev/video0 --set-ctrl=white_balance_automatic=0
```

Exposure mode values vary by camera. Use this to inspect your options:

```bash
v4l2-ctl -d /dev/video0 --list-ctrls-menus
```

### OBS still does not work

That is okay. This project does not need OBS.

If you still want OBS, try launching it through XWayland or switching the Raspberry Pi desktop session to X11. But for camera control and preview, `darkStar` is intended to avoid that fight entirely.

---

## Project philosophy

This is a small tool built in the spirit of making hardware useful again.

If Linux can see the camera, and the camera exposes controls, you should not need a fragile proprietary stack just to move it, zoom it, focus it, or take a quick snapshot.

`darkStar` is not fancy. It is not trying to be a studio suite. It is a working control panel for a real problem on real hardware.

That is the point.

---

## License

MIT License.

Use it, fork it, break it, improve it, and make your Raspberry Pi camera station better.

---

## Name

**darkStar: The OBS Raspberry Pi-Pass**

Because sometimes the cleanest way through the problem is around it.
