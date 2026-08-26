#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
echo "==> Installing the conservative Jarvis self-healing supervisor..."
sudo install -d -m 0750 /var/lib/jarvis-supervisor /opt/jarvis
sudo install -m 0755 "$REPO_DIR/bootstrap/jarvis-supervisor.py" /opt/jarvis/jarvis-supervisor.py

sudo tee /etc/systemd/system/jarvis-supervisor.service >/dev/null <<EOF
[Unit]
Description=Jarvis self-healing service supervisor
After=network-online.target docker.service
Wants=network-online.target

[Service]
Type=oneshot
Environment=LIFEOS_EVENTS_URL=http://127.0.0.1:8090/api/events
EnvironmentFile=-${REPO_DIR}/.env
Environment=JARVIS_SUPERVISOR_STATE=/var/lib/jarvis-supervisor/state.json
ExecStart=/usr/bin/python3 /opt/jarvis/jarvis-supervisor.py
Nice=10
IOSchedulingClass=idle
EOF

sudo tee /etc/systemd/system/jarvis-supervisor.timer >/dev/null <<'EOF'
[Unit]
Description=Run Jarvis self-healing checks

[Timer]
OnBootSec=90
OnUnitActiveSec=30
AccuracySec=5
Persistent=true

[Install]
WantedBy=timers.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now jarvis-supervisor.timer
sudo systemctl start jarvis-supervisor.service
echo "==> Supervisor active. Inspect with: systemctl status jarvis-supervisor.timer"
echo "    Safe simulation: /opt/jarvis/jarvis-supervisor.py --dry-run --json"
echo "    Maintenance:     /opt/jarvis/jarvis-supervisor.py --pause|--resume"
