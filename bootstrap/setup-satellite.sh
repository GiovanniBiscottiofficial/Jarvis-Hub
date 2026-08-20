#!/usr/bin/env bash
# Turn the X1's own hardware into a sensor node:
#   - built-in mic  -> a "hey Jarvis" voice satellite (wyoming-satellite)
#   - built-in webcam -> a local RTSP camera stream (go2rtc) that Frigate
#     or Home Assistant can watch like any security camera.
# Run AFTER setup-x1.sh and the voice profile:  bash bootstrap/setup-satellite.sh
set -euo pipefail

SAT_USER="${SAT_USER:-$USER}"
SAT_NAME="${SAT_NAME:-X1}"
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

echo "==> Jarvis satellite setup (user: $SAT_USER, webcam: $WEBCAM_DEV)"

echo "==> Installing audio/video tools..."
sudo apt-get update
sudo apt-get install -y --no-install-recommends \
  alsa-utils ffmpeg python3-venv python3-pip curl
sudo usermod -aG audio,video "$SAT_USER"

# ---------- Mic: wyoming-satellite (hey Jarvis on the laptop itself) ----------
echo "==> Installing wyoming-satellite..."
sudo mkdir -p /opt/wyoming-satellite
sudo python3 -m venv /opt/wyoming-satellite/venv
sudo /opt/wyoming-satellite/venv/bin/pip install --upgrade pip wyoming-satellite

# Prefer a USB microphone whenever one is plugged in. The X1 Tablet Gen 3's
# built-in mic array is not wired to the audio codec Linux can see (its ACPI
# NHLT table is empty), so the codec's "internal mic" is electrical noise —
# any cheap USB mic works instantly and this makes it the default on boot.
sudo tee /usr/local/bin/jarvis-pick-mic >/dev/null <<'MIC'
#!/bin/sh
export XDG_RUNTIME_DIR="/run/user/$(id -u)"
usb=$(pactl list short sources 2>/dev/null | grep -i usb | grep -v monitor | head -1 | cut -f2)
if [ -n "$usb" ]; then
  pactl set-default-source "$usb"
  wpctl set-volume @DEFAULT_AUDIO_SOURCE@ 1.0 2>/dev/null
  logger -t jarvis-pick-mic "Using USB mic: $usb"
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
sudo curl -fsSL -o /opt/go2rtc/go2rtc \
  https://github.com/AlexxIT/go2rtc/releases/latest/download/go2rtc_linux_amd64
sudo chmod +x /opt/go2rtc/go2rtc

sudo tee /opt/go2rtc/go2rtc.yaml >/dev/null <<EOF
api:
  listen: ":1984"
rtsp:
  # 8554 belongs to Frigate's restream, so the webcam serves on 8556
  listen: ":8556"
streams:
  x1_webcam: ffmpeg:device?video=${WEBCAM_DEV}&video_size=1280x720#video=h264
EOF

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

# Sensible default levels — a fresh PipeWire profile starts the sink at ~40%,
# which makes Jarvis nearly inaudible on the X1's small speakers.
RUNDIR="/run/user/$(id -u "${SAT_USER}")"
sudo -u "${SAT_USER}" XDG_RUNTIME_DIR="$RUNDIR" wpctl set-volume @DEFAULT_AUDIO_SINK@ 1.0 2>/dev/null || true
sudo -u "${SAT_USER}" XDG_RUNTIME_DIR="$RUNDIR" wpctl set-volume @DEFAULT_AUDIO_SOURCE@ 1.0 2>/dev/null || true

echo "==> Enabling services..."
sudo systemctl daemon-reload
sudo systemctl enable --now wyoming-satellite.service go2rtc.service

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
echo ""
echo " Logs if something's off:"
echo "  journalctl -u wyoming-satellite -f    journalctl -u go2rtc -f"
echo "=========================================================="
