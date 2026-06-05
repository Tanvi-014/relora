#!/usr/bin/env sh
# Run inside the pg-backup container or directly with Postgres credentials in env.
# Required env: PGHOST, PGUSER, PGDATABASE, PGPASSWORD
# Optional env: BACKUP_KEEP_DAYS (default 7), BACKUP_DIR (default /backups)
set -eu

BACKUP_DIR="${BACKUP_DIR:-/backups}"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
FILENAME="relora_${TIMESTAMP}.sql.gz"
DEST="${BACKUP_DIR}/${FILENAME}"

mkdir -p "${BACKUP_DIR}"

echo "[backup] $(date -u +%Y-%m-%dT%H:%M:%SZ) starting pg_dump → ${DEST}"
pg_dump \
  -h "${PGHOST}" \
  -U "${PGUSER}" \
  -d "${PGDATABASE}" \
  --no-password \
  --clean \
  --if-exists \
  --no-owner \
  --no-acl \
  | gzip > "${DEST}"

SIZE=$(du -sh "${DEST}" | cut -f1)
echo "[backup] done — ${DEST} (${SIZE})"

KEEP="${BACKUP_KEEP_DAYS:-7}"
PRUNED=$(find "${BACKUP_DIR}" -maxdepth 1 -name "relora_*.sql.gz" -mtime +"${KEEP}" -print)
if [ -n "${PRUNED}" ]; then
  echo "[backup] pruning backups older than ${KEEP} days:"
  echo "${PRUNED}" | while IFS= read -r f; do
    echo "  removing ${f}"
    rm -f "${f}"
  done
fi

echo "[backup] retained backups:"
ls -lh "${BACKUP_DIR}"/relora_*.sql.gz 2>/dev/null || echo "  (none)"
