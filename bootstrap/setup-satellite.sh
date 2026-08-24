#!/usr/bin/env bash
# Turn the X1's own hardware into a sensor node:
#   - built-in mic  -> a "hey Jarvis" voice satellite (wyoming-satellite)
#   - built-in webcam -> a local RTSP camera stream (go2rtc) that Frigate
#     or Home Assistant can watch like any security camera.
# Run AFTER setup-x1.sh and the voice profile:  bash bootstrap/setup-satellite.sh
set -euo pipefail

SAT_USER="${SAT_USER:-$USER}"
SAT_NAME="${SAT_NAME:-X1}"
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
# Auto-detect the USB webcam's capture node. On the X1 Tablet the Intel IPU3
# grabs /dev/video0-13 (not capture devices); the real UVC camera lands higher.
detect_webcam() {
  for dev in /dev/video*; do
    [ -e "$dev" ] || continue
    if udevadm info -q property -n "$dev" 2>/dev/null | grep -q "ID_USB_DRIVER=uvcvideo" \
       && v4l2-ctl -d "$dev" --get-fmt-video >/dev/null 2>&1; then
      echo "$dev"; return
    fi
  done
  echo /dev/video0
}
command -v v4l2-ctl >/dev/null || sudo apt-get install -y v4l-utils
WEBCAM_DEV="${WEBCAM_DEV:-$(detect_webcam)}"
REAR_CAMERA_DEV="${REAR_CAMERA_DEV:-}"
IR_CAMERA_DEV="${IR_CAMERA_DEV:-}"

echo "==> Jarvis satellite setup (user: $SAT_USER, webcam: $WEBCAM_DEV)"

echo "==> Installing audio/video tools..."
sudo apt-get update
sudo apt-get install -y --no-install-recommends \
  alsa-utils bluez curl ffmpeg libinput-tools pulseaudio-utils \
  python3-venv python3-pip rfkill v4l-utils
sudo usermod -aG audio,video "$SAT_USER"
sudo systemctl enable --now bluetooth.service 2>/dev/null || true
sudo rfkill unblock bluetooth 2>/dev/null || true

# ---------- Mic: wyoming-satellite (hey Jarvis on the laptop itself) ----------
echo "==> Installing wyoming-satellite..."
sudo mkdir -p /opt/wyoming-satellite
sudo python3 -m venv /opt/wyoming-satellite/venv
# Jabra performs echo/noise processing in hardware. The optional WebRTC
# extension is not required and currently does not build on Python 3.14.
sudo /opt/wyoming-satellite/venv/bin/pip install --upgrade pip wyoming-satellite

# Prefer the commissioned Jabra PHS002W speakerphone, then any USB endpoint.
# The X1 Tablet Gen 3's
# built-in mic array is not wired to the audio codec Linux can see (its ACPI
# NHLT table is empty), so the codec's "internal mic" is electrical noise —
# any cheap USB mic works instantly and this makes it the default on boot.
sudo tee /usr/local/bin/jarvis-pick-mic >/dev/null <<'MIC'
#!/bin/sh
export XDG_RUNTIME_DIR="/run/user/$(id -u)"
jabra_source=$(pactl list short sources 2>/dev/null | grep -Ei 'jabra|phs002w|gn_audio' | grep -v monitor | head -1 | cut -f2)
usb_source=$(pactl list short sources 2>/dev/null | grep -i usb | grep -v monitor | head -1 | cut -f2)
jabra_sink=$(pactl list short sinks 2>/dev/null | grep -Ei 'jabra|phs002w|gn_audio' | head -1 | cut -f2)
usb_sink=$(pactl list short sinks 2>/dev/null | grep -i usb | head -1 | cut -f2)
source=${jabra_source:-$usb_source}
sink=${jabra_sink:-$usb_sink}
if [ -n "$source" ]; then
  pactl set-default-source "$source"
  wpctl set-volume @DEFAULT_AUDIO_SOURCE@ 1.0 2>/dev/null
  logger -t jarvis-audio "Using voice microphone: $source"
fi
if [ -n "$sink" ]; then
  pactl set-default-sink "$sink"
  wpctl set-volume @DEFAULT_AUDIO_SINK@ 0.85 2>/dev/null
  logger -t jarvis-audio "Using voice speaker: $sink"
fi
exit 0
MIC
sudo chmod +x /usr/local/bin/jarvis-pick-mic

sudo tee /etc/systemd/system/wyoming-satellite.service >/dev/null <<EOF
[Unit]
Description=Jarvis voice satellite (X1 mic + speakers)
After=network-online.target sound.target

[Service]
User=${SAT_USER}
# Route aplay/arecord through the user's PipeWire session; without this the
# ALSA default device fails with "Host is down" and Jarvis stays silent.
Environment=XDG_RUNTIME_DIR=/run/user/$(id -u "${SAT_USER}")
ExecStartPre=/usr/local/bin/jarvis-pick-mic
ExecStart=/opt/wyoming-satellite/venv/bin/python -m wyoming_satellite \\
  --name "${SAT_NAME}" \\
  --uri tcp://0.0.0.0:10700 \\
  --mic-command 'arecord -r 16000 -c 1 -f S16_LE -t raw' \\
  --snd-command 'aplay -r 22050 -c 1 -f S16_LE -t raw' \\
  --wake-uri tcp://127.0.0.1:10400 \\
  --wake-word-name hey_jarvis
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

# ---------- Webcam: go2rtc (RTSP stream for Frigate / HA) ----------
echo "==> Installing go2rtc (webcam -> RTSP)..."
sudo mkdir -p /opt/go2rtc
# Download beside the running binary, then atomically replace it. Writing
# directly to an executable that systemd is currently running can fail with
# curl error 23 (text file busy) and risks leaving a partial binary.
sudo curl -fsSL -o /opt/go2rtc/go2rtc.new \
  https://github.com/AlexxIT/go2rtc/releases/latest/download/go2rtc_linux_amd64
sudo chmod +x /opt/go2rtc/go2rtc.new
sudo mv /opt/go2rtc/go2rtc.new /opt/go2rtc/go2rtc

sudo tee /opt/go2rtc/go2rtc.yaml >/dev/null <<EOF
api:
  listen: ":1984"
rtsp:
  # 8554 belongs to Frigate's restream, so the webcam serves on 8556
  listen: ":8556"
streams:
  x1_webcam: ffmpeg:device?video=${WEBCAM_DEV}&video_size=1280x720#video=h264
EOF

if [ -n "$REAR_CAMERA_DEV" ]; then
  echo "  x1_rear: ffmpeg:device?video=${REAR_CAMERA_DEV}&video_size=1280x720#video=h264" \
    | sudo tee -a /opt/go2rtc/go2rtc.yaml >/dev/null
fi
if [ -n "$IR_CAMERA_DEV" ]; then
  echo "  x1_ir: ffmpeg:device?video=${IR_CAMERA_DEV}&video_size=640x480#video=h264" \
    | sudo tee -a /opt/go2rtc/go2rtc.yaml >/dev/null
fi

sudo tee /etc/systemd/system/go2rtc.service >/dev/null <<EOF
[Unit]
Description=Jarvis webcam stream (go2rtc)
After=network-online.target

[Service]
User=${SAT_USER}
WorkingDirectory=/opt/go2rtc
ExecStart=/opt/go2rtc/go2rtc -config /opt/go2rtc/go2rtc.yaml
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

# ---------- Hardware telemetry: camera/audio/Bluetooth/touch/power ----------
echo "==> Installing X1 hardware telemetry..."
sudo mkdir -p /opt/jarvis
sudo install -m 0755 "$REPO_DIR/bootstrap/hardware-monitor.py" \
  /opt/jarvis/hardware-monitor.py
sudo tee /etc/systemd/system/jarvis-hardware-monitor.service >/dev/null <<EOF
[Unit]
Description=Jarvis X1 hardware telemetry -> LifeOS context
After=network-online.target sound.target bluetooth.service docker.service

[Service]
User=${SAT_USER}
Environment=XDG_RUNTIME_DIR=/run/user/$(id -u "${SAT_USER}")
Environment=LIFEOS_EVENTS_URL=http://127.0.0.1:8090/api/events
EnvironmentFile=-${REPO_DIR}/.env
ExecStart=/usr/bin/python3 /opt/jarvis/hardware-monitor.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

# Sensible default levels — a fresh PipeWire profile starts the sink at ~40%,
# which makes Jarvis nearly inaudible on the X1's small speakers.
RUNDIR="/run/user/$(id -u "${SAT_USER}")"
sudo -u "${SAT_USER}" XDG_RUNTIME_DIR="$RUNDIR" wpctl set-volume @DEFAULT_AUDIO_SINK@ 1.0 2>/dev/null || true
sudo -u "${SAT_USER}" XDG_RUNTIME_DIR="$RUNDIR" wpctl set-volume @DEFAULT_AUDIO_SOURCE@ 1.0 2>/dev/null || true

echo "==> Enabling services..."
sudo systemctl daemon-reload
sudo systemctl enable --now wyoming-satellite.service go2rtc.service \
  jarvis-hardware-monitor.service

echo ""
echo "=========================================================="
echo " Satellite installed."
echo ""
echo " Mic ('hey Jarvis' on the laptop itself):"
echo "  1. HA -> Settings -> Devices & Services -> Add Integration"
echo "     -> Wyoming -> host: localhost, port: 10700"
echo "  2. The '${SAT_NAME}' satellite appears; pick your Jarvis"
echo "     pipeline for it. Say 'hey Jarvis, brief me'."
echo ""
echo " Webcam (RTSP stream at rtsp://<laptop-ip>:8556/x1_webcam):"
echo "  - Quick view in HA: Add Integration -> Generic Camera ->"
echo "    paste that RTSP URL."
echo "  - Person detection: uncomment the x1_webcam block in"
echo "    frigate/config.yml, then: docker compose --profile cameras up -d"
echo "  - Preview in a browser: http://<laptop-ip>:1984"
echo "  - If Linux exposes the rear/IR cameras, rerun with:"
echo "      REAR_CAMERA_DEV=/dev/videoN IR_CAMERA_DEV=/dev/videoM bash $0"
echo "    Discover usable capture nodes with: v4l2-ctl --list-devices"
echo ""
echo " Bluetooth + hardware context:"
echo "  - The built-in Bluetooth adapter is enabled for HA discovery/presence."
echo "  - Camera, mic, speakers, touch, battery, AC, temperature, and Bluetooth"
echo "    health are published to the LifeOS Command Center every 30 seconds."
echo ""
echo " Logs if something's off:"
echo "  journalctl -u wyoming-satellite -f    journalctl -u go2rtc -f"
echo "  journalctl -u jarvis-hardware-monitor -f"
echo "=========================================================="
