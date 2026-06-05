#!/usr/bin/env sh
# Verify that the most recent Relora backup can be restored successfully.
#
# What it does:
#   1. Finds the newest backup in BACKUP_DIR (default /backups)
#   2. Creates a throw-away Postgres database "relora_verify_<timestamp>"
#   3. Restores the backup into it
#   4. Runs a table-count sanity check (webhooks, users, destinations must exist)
#   5. Drops the throw-away database
#   6. Exits 0 on success, 1 on any failure
#
# Required env:   PGHOST, PGUSER, PGPASSWORD
# Optional env:   PGDATABASE (source name, used only for super-user auth; default relora)
#                 BACKUP_DIR (default /backups)
#
# Usage:
#   PGHOST=localhost PGUSER=relora PGPASSWORD=secret ./scripts/verify-backup.sh
#
# In production run as a weekly cron via docker exec or a separate restore-test container.
set -eu

BACKUP_DIR="${BACKUP_DIR:-/backups}"
PGDATABASE="${PGDATABASE:-relora}"
VERIFY_DB="relora_verify_$(date +%Y%m%d_%H%M%S)"

# ── Find the most recent backup ───────────────────────────────────────────────
LATEST=$(ls -t "${BACKUP_DIR}"/relora_*.sql.gz 2>/dev/null | head -n 1 || true)
if [ -z "${LATEST}" ]; then
  echo "[verify-backup] ERROR: no backup files found in ${BACKUP_DIR}" >&2
  exit 1
fi
echo "[verify-backup] Using backup: ${LATEST}"

# ── Create throw-away database ────────────────────────────────────────────────
echo "[verify-backup] Creating verification database: ${VERIFY_DB}"
PGPASSWORD="${PGPASSWORD}" psql \
  -h "${PGHOST}" -U "${PGUSER}" -d "${PGDATABASE}" \
  -c "CREATE DATABASE \"${VERIFY_DB}\";" 2>&1

# ── Restore ───────────────────────────────────────────────────────────────────
echo "[verify-backup] Restoring backup into ${VERIFY_DB}…"
RESTORE_EXIT=0
gzip -dc "${LATEST}" | PGPASSWORD="${PGPASSWORD}" psql \
  -h "${PGHOST}" -U "${PGUSER}" -d "${VERIFY_DB}" \
  --set ON_ERROR_STOP=1 \
  --quiet 2>&1 || RESTORE_EXIT=$?

if [ "${RESTORE_EXIT}" -ne 0 ]; then
  echo "[verify-backup] ERROR: restore failed (exit ${RESTORE_EXIT})" >&2
  echo "[verify-backup] Dropping verification database…"
  PGPASSWORD="${PGPASSWORD}" psql \
    -h "${PGHOST}" -U "${PGUSER}" -d "${PGDATABASE}" \
    -c "DROP DATABASE IF EXISTS \"${VERIFY_DB}\";" 2>&1
  exit 1
fi

# ── Sanity checks ─────────────────────────────────────────────────────────────
echo "[verify-backup] Running sanity checks…"
CHECK_FAILED=0

for TABLE in webhooks users destinations delivery_attempts; do
  COUNT=$(PGPASSWORD="${PGPASSWORD}" psql \
    -h "${PGHOST}" -U "${PGUSER}" -d "${VERIFY_DB}" \
    -At -c "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='public' AND table_name='${TABLE}';" 2>&1)
  if [ "${COUNT}" = "1" ]; then
    ROW_COUNT=$(PGPASSWORD="${PGPASSWORD}" psql \
      -h "${PGHOST}" -U "${PGUSER}" -d "${VERIFY_DB}" \
      -At -c "SELECT COUNT(*) FROM \"${TABLE}\";" 2>&1)
    echo "[verify-backup]   ${TABLE}: ${ROW_COUNT} rows — OK"
  else
    echo "[verify-backup]   ${TABLE}: TABLE MISSING — FAIL" >&2
    CHECK_FAILED=1
  fi
done

# ── Drop verification database ────────────────────────────────────────────────
echo "[verify-backup] Dropping verification database ${VERIFY_DB}…"
PGPASSWORD="${PGPASSWORD}" psql \
  -h "${PGHOST}" -U "${PGUSER}" -d "${PGDATABASE}" \
  -c "DROP DATABASE IF EXISTS \"${VERIFY_DB}\";" 2>&1

if [ "${CHECK_FAILED}" -ne 0 ]; then
  echo "[verify-backup] RESULT: FAILED — one or more tables missing from restored backup" >&2
  exit 1
fi

echo "[verify-backup] RESULT: OK — backup ${LATEST} restores cleanly"
