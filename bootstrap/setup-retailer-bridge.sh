#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="${JARVIS_REPO_DIR:-$HOME/Jarvis-Hub}"
ENV_FILE="$REPO_DIR/.env"
SERVICE_DIR="$HOME/.config/systemd/user"
SERVICE_FILE="$SERVICE_DIR/jarvis-retailer-bridge.service"

if [[ ! -f "$REPO_DIR/bootstrap/retailer-bridge.py" ]]; then
  echo "Jarvis repo not found at $REPO_DIR" >&2
  exit 1
fi
if [[ ! -f "$ENV_FILE" ]] || ! grep -q '^RETAILER_BRIDGE_SECRET=.' "$ENV_FILE"; then
  echo "Set RETAILER_BRIDGE_SECRET to a long random value in $ENV_FILE first." >&2
  exit 1
fi

mkdir -p "$SERVICE_DIR"
cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=Jarvis authenticated retailer bridge
After=graphical-session.target

[Service]
Type=simple
WorkingDirectory=$REPO_DIR
EnvironmentFile=$ENV_FILE
ExecStart=/usr/bin/python3 $REPO_DIR/bootstrap/retailer-bridge.py
Restart=on-failure
RestartSec=3

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now jarvis-retailer-bridge.service
echo "Retailer bridge commissioned. Restart LifeOS so it receives the bridge secret."
