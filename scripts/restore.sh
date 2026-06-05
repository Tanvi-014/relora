#!/usr/bin/env sh
# Restore a Relora Postgres backup produced by backup.sh.
#
# Usage:
#   ./scripts/restore.sh <path/to/relora_YYYYMMDD_HHMMSS.sql.gz>
#
# Required env: PGHOST, PGUSER, PGDATABASE, PGPASSWORD
# The script prompts for confirmation before touching the database.
set -eu

FILE="${1:-}"
if [ -z "${FILE}" ]; then
  echo "Usage: $0 <path/to/relora_YYYYMMDD_HHMMSS.sql.gz>" >&2
  echo "" >&2
  echo "Available backups:" >&2
  ls -lh "${BACKUP_DIR:-/backups}"/relora_*.sql.gz 2>/dev/null || echo "  (none found)" >&2
  exit 1
fi

if [ ! -f "${FILE}" ]; then
  echo "Error: file not found: ${FILE}" >&2
  exit 1
fi

PGHOST="${PGHOST:-localhost}"
PGUSER="${PGUSER:-relora}"
PGDATABASE="${PGDATABASE:-relora}"
SIZE=$(du -sh "${FILE}" | cut -f1)

echo ""
echo "  Backup : ${FILE} (${SIZE})"
echo "  Target : ${PGDATABASE} @ ${PGHOST} (user: ${PGUSER})"
echo ""
printf "Type 'yes' to continue: "
read -r CONFIRM
if [ "${CONFIRM}" != "yes" ]; then
  echo "Aborted."
  exit 0
fi

echo "[restore] $(date -u +%Y-%m-%dT%H:%M:%SZ) restoring ${FILE} → ${PGDATABASE}@${PGHOST}"
zcat "${FILE}" | psql \
  -h "${PGHOST}" \
  -U "${PGUSER}" \
  -d "${PGDATABASE}" \
  --no-password \
  -v ON_ERROR_STOP=1

echo "[restore] done."
