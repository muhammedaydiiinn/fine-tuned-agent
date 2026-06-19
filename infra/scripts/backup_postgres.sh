#!/usr/bin/env bash
# Postgres veritabanını sıkıştırılmış dump olarak yedekler
set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-/opt/fine-tuned-agent/backups}"
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
BACKUP_FILE="${BACKUP_DIR}/fine_tuned_agent_${TIMESTAMP}.sql.gz"

source "$(dirname "$0")/../../.env" 2>/dev/null || true

DB="${POSTGRES_DB:-fine_tuned_agent}"
USER="${POSTGRES_USER:-fine_tuned_agent}"
HOST="${POSTGRES_HOST:-localhost}"
PORT="${POSTGRES_PORT:-5432}"

mkdir -p "$BACKUP_DIR"

echo "Yedekleniyor: $DB → $BACKUP_FILE"
PGPASSWORD="${POSTGRES_PASSWORD}" pg_dump \
    -h "$HOST" -p "$PORT" -U "$USER" "$DB" \
    | gzip > "$BACKUP_FILE"

echo "Tamamlandı: $BACKUP_FILE ($(du -sh "$BACKUP_FILE" | cut -f1))"

# 30 günden eski yedekleri temizle
find "$BACKUP_DIR" -name "fine_tuned_agent_*.sql.gz" -mtime +30 -delete
echo "30 günden eski yedekler temizlendi."
