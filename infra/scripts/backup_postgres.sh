#!/usr/bin/env bash
# Postgres veritabanını sıkıştırılmış dump olarak yedekler
set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-/opt/anrufblocker/backups}"
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
BACKUP_FILE="${BACKUP_DIR}/anrufblocker_${TIMESTAMP}.sql.gz"

source "$(dirname "$0")/../../.env" 2>/dev/null || true

DB="${POSTGRES_DB:-anrufblocker}"
USER="${POSTGRES_USER:-anrufblocker}"
HOST="${POSTGRES_HOST:-localhost}"
PORT="${POSTGRES_PORT:-5432}"

mkdir -p "$BACKUP_DIR"

echo "Yedekleniyor: $DB → $BACKUP_FILE"
PGPASSWORD="${POSTGRES_PASSWORD}" pg_dump \
    -h "$HOST" -p "$PORT" -U "$USER" "$DB" \
    | gzip > "$BACKUP_FILE"

echo "Tamamlandı: $BACKUP_FILE ($(du -sh "$BACKUP_FILE" | cut -f1))"

# 30 günden eski yedekleri temizle
find "$BACKUP_DIR" -name "anrufblocker_*.sql.gz" -mtime +30 -delete
echo "30 günden eski yedekler temizlendi."
