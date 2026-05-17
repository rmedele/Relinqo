#!/usr/bin/env bash
# Daily SQLite backup script for Relinqo
# Usage: crontab -e → 0 2 * * * /path/to/backup_db.sh
#
# Keeps the last 14 daily backups.

set -euo pipefail

DB_PATH="${RELINQO_DB_PATH:-/home/reese/lead-recovery-v1/lead-recovery-v1/data/leadrelay.db}"
BACKUP_DIR="${RELINQO_BACKUP_DIR:-/home/reese/lead-recovery-v1/lead-recovery-v1/backups}"
KEEP_DAYS=14

mkdir -p "$BACKUP_DIR"

TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="$BACKUP_DIR/relinqo_${TIMESTAMP}.db"

# Use SQLite's online backup API (safe even if the db is in use)
sqlite3 "$DB_PATH" ".backup '$BACKUP_FILE'"

echo "Backup created: $BACKUP_FILE ($(du -h "$BACKUP_FILE" | cut -f1))"

# Prune old backups
find "$BACKUP_DIR" -name "relinqo_*.db" -mtime +$KEEP_DAYS -delete

echo "Pruned backups older than $KEEP_DAYS days."
