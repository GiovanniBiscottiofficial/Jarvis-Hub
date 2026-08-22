#!/usr/bin/env bash
# Repair the installed X1 kiosk without deleting Chromium cookies, HA login
# state, LifeOS data, or Home Assistant configuration.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

echo "==> Jarvis kiosk recovery"
if docker info >/dev/null 2>&1; then
  DC=(docker compose)
else
  DC=(sudo docker compose)
fi
echo "==> Checking compose configuration..."
"${DC[@]}" config --quiet

stamp=$(date +%Y%m%d-%H%M%S)
config_file="ha-config/configuration.yaml"
secrets_file="ha-config/secrets.yaml"

echo "==> Repairing known Home Assistant configuration breakages..."
cp -a "$config_file" "${config_file}.before-kiosk-repair-${stamp}"

# A single-file include is wrong for this repository: automations are kept as
# one YAML list per file under ha-config/automations/.
if grep -Eq '^automation:[[:space:]]*!include[[:space:]]+automations\.yaml[[:space:]]*$' \
    "$config_file"; then
  sed -i -E \
    's|^automation:[[:space:]]*!include[[:space:]]+automations\.yaml[[:space:]]*$|automation: !include_dir_merge_list automations/|' \
    "$config_file"
  echo "    Fixed automation include: automations/ directory merge enabled."
fi
if grep -Eq '^script:[[:space:]]*!include[[:space:]]+scripts\.yaml[[:space:]]*$' \
    "$config_file"; then
  sed -i -E \
    's|^script:[[:space:]]*!include[[:space:]]+scripts\.yaml[[:space:]]*$|script: !include_dir_merge_named scripts/|' \
    "$config_file"
  echo "    Fixed script include: scripts/ directory merge enabled."
fi

# Current HA config protects LifeOS requests with this secret. Keep the HA
# header and LifeOS server token synchronized, creating both when absent.
if grep -q '!secret lifeos_api_authorization' "$config_file"; then
  token=$(sed -n 's/^LIFEOS_API_TOKEN=//p' .env 2>/dev/null | tail -1)
  if [ -z "$token" ]; then
    token=$(openssl rand -hex 32 2>/dev/null || cat /proc/sys/kernel/random/uuid)
    if grep -q '^LIFEOS_API_TOKEN=' .env 2>/dev/null; then
      sed -i "s|^LIFEOS_API_TOKEN=.*$|LIFEOS_API_TOKEN=${token}|" .env
    else
      printf '\nLIFEOS_API_TOKEN=%s\n' "$token" >>.env
    fi
    echo "    Generated LIFEOS_API_TOKEN in .env."
  fi
  [ ! -f "$secrets_file" ] || cp -a "$secrets_file" "${secrets_file}.before-kiosk-repair-${stamp}"
  if grep -q '^lifeos_api_authorization:' "$secrets_file" 2>/dev/null; then
    sed -i "s|^lifeos_api_authorization:.*$|lifeos_api_authorization: \"Bearer ${token}\"|" \
      "$secrets_file"
  else
    printf 'lifeos_api_authorization: "Bearer %s"\n' "$token" >>"$secrets_file"
  fi
  chmod 600 "$secrets_file"
  echo "    Synchronized lifeos_api_authorization in HA secrets."
fi

echo "==> Validating Home Assistant configuration (not just HTTP recovery mode)..."
"${DC[@]}" up -d homeassistant
if ! "${DC[@]}" exec -T homeassistant \
    python -m homeassistant --script check_config -c /config; then
  echo "    Edited configuration is still invalid; restoring committed configuration..."
  cp -a "$config_file" "${config_file}.failed-${stamp}"
  git show HEAD:ha-config/configuration.yaml >"$config_file"
  if ! "${DC[@]}" exec -T homeassistant \
      python -m homeassistant --script check_config -c /config; then
    echo "ERROR: Even the committed Home Assistant configuration is invalid." >&2
    echo "       Original edits: ${config_file}.before-kiosk-repair-${stamp}" >&2
    echo "       Failed repaired copy: ${config_file}.failed-${stamp}" >&2
    "${DC[@]}" logs --tail=100 homeassistant >&2
    exit 1
  fi
  echo "    Committed Home Assistant configuration validated successfully."
fi

echo "==> Restarting Home Assistant and LifeOS with synchronized configuration..."
"${DC[@]}" up -d --build lifeos homeassistant
"${DC[@]}" restart homeassistant

echo "==> Waiting for normal Home Assistant startup..."
ha_code=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 5 \
  http://localhost:8123/ 2>/dev/null || true)
case "$ha_code" in
  2*|3*|401|403)
    echo "    Home Assistant HTTP $ha_code — backend reachable."
    ;;
  *)
    echo "    Home Assistant returned '${ha_code:-no response}'; waiting/restarting it..."
    "${DC[@]}" up -d homeassistant
    for _ in $(seq 1 60); do
      ha_code=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 3 \
        http://localhost:8123/ 2>/dev/null || true)
      case "$ha_code" in 2*|3*|401|403) break ;; esac
      sleep 2
    done
    case "$ha_code" in
      2*|3*|401|403) echo "    Home Assistant recovered (HTTP $ha_code)." ;;
      *)
        echo "ERROR: Home Assistant is still unavailable. Recent logs:" >&2
        "${DC[@]}" logs --tail=80 homeassistant >&2
        exit 1
        ;;
    esac
    ;;
esac

echo "==> Reinstalling the known-good fullscreen kiosk session..."
# Run the committed script from HEAD, not a locally edited working-tree copy.
# This specifically recovers from accidental edits such as a broad pkill that
# kills setup-kiosk itself. The Chromium profile remains intact.
clean_setup=$(mktemp)
trap 'rm -f "$clean_setup"' EXIT
git show HEAD:bootstrap/setup-kiosk.sh >"$clean_setup"
chmod +x "$clean_setup"
bash "$clean_setup"

# Weston/Wayland proved unreliable on the X1 Tablet's 3000x2000 panel. Return
# to the deterministic X11/Openbox session that fills the physical display.
bash bootstrap/revert-kiosk-to-x11.sh --no-restart

echo "==> Restarting tty1 to launch the repaired display..."
sudo systemctl restart getty@tty1.service

echo "==> Repair dispatched. The fullscreen dashboard should return shortly."
echo "    If it does not, press Ctrl+Alt+F2 and run:"
echo "    journalctl -b --no-pager | tail -120"
