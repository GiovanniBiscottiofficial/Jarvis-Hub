#!/usr/bin/env bash
# Install a nightly 3 AM cron job that runs backup.sh.
# Optional: BACKUP_DIR=/media/usb bash bootstrap/setup-backups.sh
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DIR="${BACKUP_DIR:-$HOME/jarvis-backups}"
CRON_LINE="0 3 * * * BACKUP_DIR=$DIR bash $REPO_DIR/bootstrap/backup.sh >> $DIR/backup.log 2>&1"

mkdir -p "$DIR"
( crontab -l 2>/dev/null | grep -v 'bootstrap/backup.sh' || true; echo "$CRON_LINE" ) | crontab -

echo "==> Nightly backup installed (3:00 AM) -> $DIR"
echo "    Run one now to test: bash $REPO_DIR/bootstrap/backup.sh"
