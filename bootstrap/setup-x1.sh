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

# 5. Start the core stack
echo "==> Starting Home Assistant..."
sudo docker compose up -d

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
