#!/usr/bin/env bash
# Back up the LifeOS database + Home Assistant config.
# Run any time:            bash bootstrap/backup.sh
# Nightly at 3 AM:         bash bootstrap/setup-backups.sh
# To a USB drive instead:  BACKUP_DIR=/media/usb bash bootstrap/backup.sh
set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-$HOME/jarvis-backups}"
KEEP="${KEEP:-14}"   # keep the newest N of each
STAMP="$(date +%Y%m%d-%H%M%S)"

mkdir -p "$BACKUP_DIR"

# Disk-space guard: refuse to back up onto a nearly-full disk
MIN_FREE_MB="${MIN_FREE_MB:-500}"
FREE_MB="$(df -Pm "$BACKUP_DIR" | awk 'NR==2 {print $4}')"
if [ "$FREE_MB" -lt "$MIN_FREE_MB" ]; then
  echo "!! Only ${FREE_MB}MB free at $BACKUP_DIR (need ${MIN_FREE_MB}MB) — aborting backup" >&2
  exit 1
fi

# LifeOS DB — consistent snapshot even while the container is running
if docker ps --format '{{.Names}}' | grep -q '^lifeos$'; then
  docker exec lifeos python3 -c \
    "import sqlite3; sqlite3.connect('/data/lifeos.db').execute(\"VACUUM INTO '/data/backup-tmp.db'\")"
  docker cp lifeos:/data/backup-tmp.db "$BACKUP_DIR/lifeos-$STAMP.db"
  docker exec lifeos rm -f /data/backup-tmp.db
  echo "==> LifeOS DB -> $BACKUP_DIR/lifeos-$STAMP.db"
else
  echo "!! lifeos container not running — skipping DB backup"
fi

# Home Assistant config (includes root-owned .storage files). Build the archive
# inside the container so every mounted config file is readable, then copy it
# into place atomically. A host-side tar silently misses protected registry and
# authentication files on standard Home Assistant container installs.
if docker ps --format '{{.Names}}' | grep -q '^homeassistant$'; then
  HA_CONTAINER_ARCHIVE="/tmp/jarvis-ha-$STAMP.tar.gz"
  HA_PARTIAL_ARCHIVE="$BACKUP_DIR/.ha-config-$STAMP.tar.gz.part"
  docker exec homeassistant tar -czf "$HA_CONTAINER_ARCHIVE" -C /config .
  docker cp "homeassistant:$HA_CONTAINER_ARCHIVE" "$HA_PARTIAL_ARCHIVE"
  docker exec homeassistant rm -f "$HA_CONTAINER_ARCHIVE"
  mv "$HA_PARTIAL_ARCHIVE" "$BACKUP_DIR/ha-config-$STAMP.tar.gz"
  echo "==> HA config -> $BACKUP_DIR/ha-config-$STAMP.tar.gz"
else
  echo "!! homeassistant container not running — skipping HA config backup"
fi

# Prune old backups
{ ls -1t "$BACKUP_DIR"/lifeos-*.db 2>/dev/null || true; } | tail -n +$((KEEP + 1)) | xargs -r rm --
{ ls -1t "$BACKUP_DIR"/ha-config-*.tar.gz 2>/dev/null || true; } | tail -n +$((KEEP + 1)) | xargs -r rm --

echo "==> Backup complete (keeping newest $KEEP of each)"
