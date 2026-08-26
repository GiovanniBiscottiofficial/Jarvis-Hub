#!/usr/bin/env bash
# Rollback-safe migration from archived Wyoming Satellite to Linux Voice Assistant.
# Raw audio and transcripts are never written by this script.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ACTION="${1:-status}"
SAT_USER="${SUDO_USER:-$(id -un)}"
SAT_UID="$(id -u "${SAT_USER}")"
SAT_GID="$(id -g "${SAT_USER}")"

export LVA_USER_ID="${LVA_USER_ID:-${SAT_UID}}"
export LVA_USER_GROUP="${LVA_USER_GROUP:-${SAT_GID}}"
export LVA_RUNTIME_DIR="${LVA_RUNTIME_DIR:-/run/user/${SAT_UID}}"
export LVA_PULSE_SERVER="${LVA_PULSE_SERVER:-${LVA_RUNTIME_DIR}/pulse/native}"

compose() {
  docker compose -f "${ROOT_DIR}/docker-compose.yml" \
    --project-directory "${ROOT_DIR}" --profile conversation "$@"
}

lva_state() {
  docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' \
    linux-voice-assistant 2>/dev/null || echo "not-installed"
}

wyoming_state() {
  systemctl is-active wyoming-satellite.service 2>/dev/null || true
}

rollback() {
  echo "Restoring the known-working Wyoming voice satellite..."
  compose stop linux-voice-assistant >/dev/null 2>&1 || true
  sudo systemctl enable wyoming-satellite.service >/dev/null 2>&1 || true
  sudo systemctl restart wyoming-satellite.service
  echo "Rollback complete. Wyoming: $(wyoming_state); LVA: $(lva_state)"
}

wait_for_health() {
  local attempt state
  for attempt in {1..18}; do
    state="$(lva_state)"
    if [[ "${state}" == "healthy" ]]; then
      return 0
    fi
    if [[ "${state}" == "unhealthy" || "${state}" == "exited" ]]; then
      return 1
    fi
    sleep 5
  done
  return 1
}

case "${ACTION}" in
  status)
    echo "Wyoming satellite: $(wyoming_state)"
    echo "Linux Voice Assistant: $(lva_state)"
    echo "Audio runtime: ${LVA_RUNTIME_DIR}"
    ;;
  stage)
    test -S "${LVA_PULSE_SERVER}" || {
      echo "PipeWire/Pulse socket not found at ${LVA_PULSE_SERVER}." >&2
      exit 1
    }
    compose pull linux-voice-assistant voice-permissions
    echo "Linux Voice Assistant image staged. The working satellite was not changed."
    ;;
  activate)
    test -S "${LVA_PULSE_SERVER}" || {
      echo "PipeWire/Pulse socket not found at ${LVA_PULSE_SERVER}." >&2
      exit 1
    }
    compose pull linux-voice-assistant voice-permissions
    echo "Pausing Wyoming so only one process owns the Jabra microphone..."
    sudo systemctl stop wyoming-satellite.service
    if ! compose up -d linux-voice-assistant; then
      rollback
      exit 1
    fi
    if ! wait_for_health; then
      compose logs --tail=80 linux-voice-assistant || true
      rollback
      exit 1
    fi
    echo "Linux Voice Assistant is healthy."
    echo "Home Assistant should discover 'Jarvis X1' through ESPHome on port ${LVA_PORT:-6053}."
    echo "After it is connected and its Jarvis pipeline works, run:"
    echo "  bash bootstrap/setup-continuous-voice.sh commit"
    echo "Until commit, rebooting automatically restores the previous Wyoming satellite."
    ;;
  commit)
    if [[ "$(lva_state)" != "healthy" ]]; then
      echo "Refusing to retire Wyoming: Linux Voice Assistant is not healthy." >&2
      exit 1
    fi
    if ! ss -Htn state established "( sport = :${LVA_PORT:-6053} )" | grep -q .; then
      echo "Refusing to retire Wyoming: Home Assistant is not connected to LVA yet." >&2
      exit 1
    fi
    sudo systemctl disable --now wyoming-satellite.service
    echo "Continuous conversation committed. Wyoming remains installed for rollback."
    ;;
  rollback)
    rollback
    ;;
  *)
    echo "Usage: $0 {status|stage|activate|commit|rollback}" >&2
    exit 2
    ;;
esac
