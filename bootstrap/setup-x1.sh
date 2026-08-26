#!/usr/bin/env bash
# One-time setup for the ThinkPad X1 hub (run on a fresh Ubuntu Server install).
# Usage: bash bootstrap/setup-x1.sh
set -euo pipefail

echo "==> Jarvis Hub bootstrap"

# 1. Keep the laptop awake when the lid is closed
if ! grep -q '^HandleLidSwitch=ignore' /etc/systemd/logind.conf 2>/dev/null; then
  echo "==> Configuring lid-close to NOT suspend..."
  sudo sed -i 's/^#\?HandleLidSwitch=.*/HandleLidSwitch=ignore/' /etc/systemd/logind.conf
  grep -q '^HandleLidSwitch=' /etc/systemd/logind.conf || \
    echo 'HandleLidSwitch=ignore' | sudo tee -a /etc/systemd/logind.conf >/dev/null
  sudo systemctl restart systemd-logind
fi

# 2. Disable sleep/hibernate targets entirely (it's a server now)
echo "==> Disabling suspend/hibernate..."
sudo systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target

# 2b. Battery longevity: the hub lives on the charger, so cap charging at 80%
# (ThinkPads expose this natively; huge difference for an always-plugged battery)
if compgen -G '/sys/class/power_supply/BAT*/charge_control_end_threshold' >/dev/null; then
  echo "==> Setting battery charge cap (60-80%) for longevity..."
  sudo tee /etc/systemd/system/battery-charge-cap.service >/dev/null << 'UNIT'
[Unit]
Description=Cap battery charging at 80% (always-on hub longevity)

[Service]
Type=oneshot
ExecStart=/bin/sh -c 'for b in /sys/class/power_supply/BAT*; do \
  [ -f "$b/charge_control_start_threshold" ] && echo 60 > "$b/charge_control_start_threshold"; \
  [ -f "$b/charge_control_end_threshold" ] && echo 80 > "$b/charge_control_end_threshold"; done'

[Install]
WantedBy=multi-user.target
UNIT
  sudo systemctl daemon-reload
  sudo systemctl enable --now battery-charge-cap.service
else
  echo "==> (battery charge-cap not supported by this kernel/firmware — skipping)"
fi

# 2b2. Audio: the X1 Tablet Gen 3's speakers/mic are silent (or noise-only)
# under the default snd_hda_intel driver. Intel's AVS DSP stack drives the
# ALC295 codec correctly on this machine. Takes effect after a reboot.
if grep -qi "X1 Tablet Gen 3" /sys/class/dmi/id/product_family 2>/dev/null || \
   grep -qi "X1 Tablet Gen 3" /sys/class/dmi/id/product_version 2>/dev/null; then
  echo "==> X1 Tablet Gen 3 detected: enabling Intel AVS audio driver..."
  sudo tee /etc/modprobe.d/jarvis-avs-audio.conf >/dev/null <<'AVSEOF'
# Jarvis: use Intel AVS DSP stack (fixes silent speakers/mic on X1 Tablet Gen 3)
options snd_intel_dspcfg dsp_driver=4
AVSEOF
  sudo update-initramfs -u || true
fi

# 2c. SSD health: periodic TRIM
echo "==> Enabling weekly SSD trim..."
sudo systemctl enable --now fstrim.timer 2>/dev/null || true

# 2d. Safe-shutdown watchdog: during an outage, once the battery hits 10%
# the hub stops the containers cleanly and powers off instead of dying
# mid-write. Runs host-side every 2 minutes, independent of Home Assistant.
echo "==> Installing low-battery safe-shutdown watchdog..."
JARVIS_DIR="$(pwd)"
sudo tee /usr/local/bin/jarvis-battwatch >/dev/null << WATCH
#!/bin/sh
# Power off safely when on battery and nearly empty.
ONLINE=\$(head -qn1 /sys/class/power_supply/A*/online 2>/dev/null || echo 1)
CAP=\$(head -qn1 /sys/class/power_supply/BAT*/capacity 2>/dev/null || echo 100)
if [ "\$ONLINE" = "0" ] && [ "\$CAP" -le 10 ]; then
  logger -t jarvis-battwatch "Battery \${CAP}% on battery power — safe shutdown"
  cd "$JARVIS_DIR" && docker compose stop || true
  systemctl poweroff
fi
WATCH
sudo chmod +x /usr/local/bin/jarvis-battwatch
sudo tee /etc/systemd/system/jarvis-battwatch.service >/dev/null << 'UNIT'
[Unit]
Description=Jarvis low-battery safe shutdown check

[Service]
Type=oneshot
ExecStart=/usr/local/bin/jarvis-battwatch
UNIT
sudo tee /etc/systemd/system/jarvis-battwatch.timer >/dev/null << 'UNIT'
[Unit]
Description=Run Jarvis low-battery check every 2 minutes

[Timer]
OnBootSec=2min
OnUnitActiveSec=2min

[Install]
WantedBy=timers.target
UNIT
sudo systemctl daemon-reload
sudo systemctl enable --now jarvis-battwatch.timer

# 3. Install Docker if missing
if ! command -v docker >/dev/null 2>&1; then
  echo "==> Installing Docker..."
  curl -fsSL https://get.docker.com | sh
  sudo usermod -aG docker "$USER"
  echo "NOTE: log out and back in for docker group membership to apply."
fi

# 4. Create the .env file if missing
if [ ! -f .env ]; then
  echo "==> Creating .env (edit TZ if needed)..."
  cp .env.example .env
fi

# If configuration.yaml protects HA→LifeOS requests, ensure HA's secret and
# the LifeOS container receive the same token before Home Assistant starts.
if grep -q '!secret lifeos_api_authorization' ha-config/configuration.yaml; then
  LIFEOS_TOKEN=$(sed -n 's/^LIFEOS_API_TOKEN=//p' .env | tail -1)
  if [ -z "$LIFEOS_TOKEN" ]; then
    LIFEOS_TOKEN=$(openssl rand -hex 32 2>/dev/null || cat /proc/sys/kernel/random/uuid)
    if grep -q '^LIFEOS_API_TOKEN=' .env; then
      sed -i "s|^LIFEOS_API_TOKEN=.*$|LIFEOS_API_TOKEN=${LIFEOS_TOKEN}|" .env
    else
      printf '\nLIFEOS_API_TOKEN=%s\n' "$LIFEOS_TOKEN" >>.env
    fi
  fi
  if grep -q '^lifeos_api_authorization:' ha-config/secrets.yaml 2>/dev/null; then
    sed -i "s|^lifeos_api_authorization:.*$|lifeos_api_authorization: \"Bearer ${LIFEOS_TOKEN}\"|" \
      ha-config/secrets.yaml
  else
    printf 'lifeos_api_authorization: "Bearer %s"\n' "$LIFEOS_TOKEN" >>ha-config/secrets.yaml
  fi
  chmod 600 ha-config/secrets.yaml
fi

# 5. Start the core stack
echo "==> Starting Home Assistant..."
sudo docker compose up -d

# Install the bounded self-healing supervisor after the core services exist.
bash bootstrap/setup-supervisor.sh

IP=$(hostname -I | awk '{print $1}')
echo ""
echo "=========================================================="
echo " Home Assistant is starting."
echo " Open:  http://${IP}:8123   (give it 1-2 minutes on first boot)"
echo ""
echo " Next steps:"
echo "   - Voice pipeline:  sudo docker compose --profile voice up -d"
echo "   - Grocy inventory: sudo docker compose --profile grocy up -d"
echo "   - Local LLM:       sudo docker compose --profile llm up -d"
echo "     then: sudo docker exec -it ollama ollama pull llama3.2:3b"
echo ""
echo " See README.md for the full phase-by-phase guide."
echo "=========================================================="
