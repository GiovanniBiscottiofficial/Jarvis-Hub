#!/usr/bin/env bash
# Reach Jarvis from anywhere (work, cellular) without exposing anything to
# the internet: Tailscale builds a private, encrypted tunnel between your
# devices. Free for personal use.
# Run on the X1:  bash bootstrap/setup-remote-access.sh
set -euo pipefail

echo "==> Installing Tailscale..."
if ! command -v tailscale >/dev/null 2>&1; then
  curl -fsSL https://tailscale.com/install.sh | sh
fi

echo "==> Starting Tailscale (a login link will appear — open it on your phone)..."
sudo tailscale up

TSIP=$(tailscale ip -4 2>/dev/null | head -1 || true)
echo ""
echo "=========================================================="
echo " Done. Install the Tailscale app on your phone and log in"
echo " with the SAME account."
echo ""
echo " From anywhere in the world you can then open:"
echo "   Home Assistant:  http://${TSIP:-<tailscale-ip>}:8123"
echo "   LifeOS:          http://${TSIP:-<tailscale-ip>}:8090"
echo "   Grocy:           http://${TSIP:-<tailscale-ip>}:9283"
echo ""
echo " In the HA Companion app, set that HA URL as the"
echo " 'external URL' — cameras, lights, and Assist (ask Jarvis"
echo " anything by voice) all work from work or cellular."
echo "=========================================================="
