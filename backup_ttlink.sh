#!/bin/bash
set -euo pipefail

BACKUP_DIR=/opt/ttlink/backups
RETENTION_DAYS=7

# Passphrase de cifrado: archivo aparte con permisos 600
PASSPHRASE_FILE=/opt/ttlink/.backup_passphrase
if [ ! -s "$PASSPHRASE_FILE" ]; then
  echo "Falta $PASSPHRASE_FILE (o esta vacio). Crea una passphrase antes de hacer backups." >&2
  exit 1
fi

mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"

# Copia consistente con la API de backup de SQLite (no un cp en caliente,
# que puede capturar el archivo a mitad de una escritura)
docker exec ttlink-app python -c \
  "import sqlite3; s=sqlite3.connect('/data/ttlink.db'); d=sqlite3.connect('/tmp/ttlink_backup.db'); s.backup(d); d.close(); s.close()"

DB_COPY="${BACKUP_DIR}/ttlink_db_$(date +%F_%H%M).db"
docker cp ttlink-app:/tmp/ttlink_backup.db "$DB_COPY"
docker exec ttlink-app rm -f /tmp/ttlink_backup.db
gzip "$DB_COPY"

gpg --batch --yes --symmetric --cipher-algo AES256 \
    --passphrase-file "$PASSPHRASE_FILE" \
    --output "${DB_COPY}.gz.gpg" "${DB_COPY}.gz"
chmod 600 "${DB_COPY}.gz.gpg"
rm -f "${DB_COPY}.gz"

find "$BACKUP_DIR" -name 'ttlink_db_*.gpg' -mtime +"$RETENTION_DAYS" -delete
echo "$(date '+%F %T') backup OK: ${DB_COPY}.gz.gpg"
