#!/usr/bin/env bash
set -euo pipefail

BACKUP_DIR="${1:-/tmp/backups}"
DB_NAME="${2:-appdb}"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
OUTFILE="$BACKUP_DIR/${DB_NAME}_${TIMESTAMP}.sql.gz"

mkdir -p "$BACKUP_DIR"

echo "Starting backup of $DB_NAME..."
pg_dump "$DB_NAME" | gzip > "$OUTFILE"

if [ $? -eq 0 ]; then
    echo "Backup succeeded: $OUTFILE"
else
    echo "Backup failed!" >&2
    exit 1
fi
