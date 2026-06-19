#!/usr/bin/env bash
# Sıkıştırılmış bir dump dosyasından Postgres'i geri yükler
# Kullanım: ./restore_postgres.sh /path/to/backup.sql.gz
set -euo pipefail

BACKUP_FILE="${1:-}"
if [[ -z "$BACKUP_FILE" ]]; then
    echo "Kullanım: $0 <backup_dosyasi.sql.gz>"
    exit 1
fi

if [[ ! -f "$BACKUP_FILE" ]]; then
    echo "HATA: Dosya bulunamadı: $BACKUP_FILE"
    exit 1
fi

source "$(dirname "$0")/../../.env" 2>/dev/null || true

DB="${POSTGRES_DB:-anrufblocker}"
USER="${POSTGRES_USER:-anrufblocker}"
HOST="${POSTGRES_HOST:-localhost}"
PORT="${POSTGRES_PORT:-5432}"

echo "UYARI: '$DB' veritabanı '$BACKUP_FILE' ile geri yüklenecek."
read -r -p "Devam etmek istiyor musunuz? [e/H] " confirm
if [[ "$confirm" != "e" && "$confirm" != "E" ]]; then
    echo "İptal edildi."
    exit 0
fi

echo "Geri yükleniyor..."
gunzip -c "$BACKUP_FILE" | PGPASSWORD="${POSTGRES_PASSWORD}" psql \
    -h "$HOST" -p "$PORT" -U "$USER" "$DB"

echo "Geri yükleme tamamlandı."
