#!/usr/bin/env bash
# Back up the LifeOS database + Home Assistant config.
# Run any time:            bash bootstrap/backup.sh
# Nightly at 3 AM:         bash bootstrap/setup-backups.sh
# To a USB drive instead:  BACKUP_DIR=/media/usb bash bootstrap/backup.sh
set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-$HOME/jarvis-backups}"
KEEP="${KEEP:-14}"   # keep the newest N of each
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
STAMP="$(date +%Y%m%d-%H%M%S)"

mkdir -p "$BACKUP_DIR"

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

# Home Assistant config (includes .storage: users, dashboards, integrations)
tar -czf "$BACKUP_DIR/ha-config-$STAMP.tar.gz" -C "$REPO_DIR" ha-config
echo "==> HA config -> $BACKUP_DIR/ha-config-$STAMP.tar.gz"

# Prune old backups
{ ls -1t "$BACKUP_DIR"/lifeos-*.db 2>/dev/null || true; } | tail -n +$((KEEP + 1)) | xargs -r rm --
{ ls -1t "$BACKUP_DIR"/ha-config-*.tar.gz 2>/dev/null || true; } | tail -n +$((KEEP + 1)) | xargs -r rm --

echo "==> Backup complete (keeping newest $KEEP of each)"
