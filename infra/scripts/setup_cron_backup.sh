#!/usr/bin/env bash
# Her gece 02:00'de Postgres yedeği alacak cron job kurar.
# deploy kullanıcısı olarak çalıştır.
set -euo pipefail

SCRIPT_PATH="$(realpath "$(dirname "$0")/backup_postgres.sh")"
BACKUP_DIR="/opt/anrufblocker/backups"
CRON_USER="${SUDO_USER:-$USER}"
CRON_JOB="0 2 * * * BACKUP_DIR=$BACKUP_DIR bash $SCRIPT_PATH >> $BACKUP_DIR/backup.log 2>&1"

echo "=== Otomatik yedekleme cron job'u kuruluyor ==="
echo "Script : $SCRIPT_PATH"
echo "Hedef  : $BACKUP_DIR"
echo "Zamanlama: Her gece 02:00"
echo ""

# Mevcut crontab'a ekle (tekrar ekleme yapma)
EXISTING_CRON=$(crontab -l -u "$CRON_USER" 2>/dev/null || true)

if echo "$EXISTING_CRON" | grep -q "backup_postgres.sh"; then
    echo "Cron job zaten mevcut, güncelleniyor..."
    EXISTING_CRON=$(echo "$EXISTING_CRON" | grep -v "backup_postgres.sh")
fi

echo "$EXISTING_CRON
$CRON_JOB" | crontab -u "$CRON_USER" -

echo "Cron job kuruldu:"
crontab -l -u "$CRON_USER" | grep backup_postgres

echo ""
echo "Yedekler: $BACKUP_DIR"
echo "Log     : $BACKUP_DIR/backup.log"
echo ""
echo "Manuel test: bash $SCRIPT_PATH"
