#!/usr/bin/env bash
# Turn the X1's own hardware into a sensor node:
#   - built-in mic  -> a "hey Jarvis" voice satellite (wyoming-satellite)
#   - built-in webcam -> a local RTSP camera stream (go2rtc) that Frigate
#     or Home Assistant can watch like any security camera.
# Run AFTER setup-x1.sh and the voice profile:  bash bootstrap/setup-satellite.sh
set -euo pipefail

SAT_USER="${SAT_USER:-$USER}"
SAT_NAME="${SAT_NAME:-X1}"
WEBCAM_DEV="${WEBCAM_DEV:-/dev/video0}"

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

sudo tee /etc/systemd/system/wyoming-satellite.service >/dev/null <<EOF
[Unit]
Description=Jarvis voice satellite (X1 mic + speakers)
After=network-online.target sound.target

[Service]
User=${SAT_USER}
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
