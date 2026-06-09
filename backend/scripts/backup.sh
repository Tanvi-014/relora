#!/usr/bin/env bash
# Relora database backup & restore helpers.
#
# Usage:
#   ./backup.sh backup            — dump to ./backups/relora_YYYYMMDD_HHMMSS.dump
#   ./backup.sh restore FILE      — restore from a .dump file
#   ./backup.sh list              — list available backups
#
# Requires pg_dump / pg_restore (PostgreSQL client tools).
# Reads DATABASE_URL from the environment or .env in the repo root.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
BACKUP_DIR="${BACKUP_DIR:-$REPO_ROOT/backups}"

# Load .env if present and DATABASE_URL not already set
if [[ -z "${DATABASE_URL:-}" && -f "$REPO_ROOT/.env" ]]; then
  # shellcheck disable=SC2046
  export $(grep -v '^#' "$REPO_ROOT/.env" | grep '=' | xargs)
fi

if [[ -z "${DATABASE_URL:-}" ]]; then
  echo "ERROR: DATABASE_URL is not set." >&2
  exit 1
fi

# Strip asyncpg driver prefix so pg_dump can use it
PG_URL="${DATABASE_URL/postgresql+asyncpg:\/\//postgresql://}"

cmd="${1:-}"

case "$cmd" in
  backup)
    mkdir -p "$BACKUP_DIR"
    STAMP="$(date +%Y%m%d_%H%M%S)"
    OUT="$BACKUP_DIR/relora_${STAMP}.dump"
    echo "→ Dumping database to $OUT"
    pg_dump --format=custom --compress=9 --file="$OUT" "$PG_URL"
    SIZE="$(du -sh "$OUT" | cut -f1)"
    echo "✓ Backup complete — $OUT ($SIZE)"
    ;;

  restore)
    FILE="${2:-}"
    if [[ -z "$FILE" ]]; then
      echo "Usage: $0 restore <path-to-file.dump>" >&2
      exit 1
    fi
    if [[ ! -f "$FILE" ]]; then
      echo "ERROR: file not found: $FILE" >&2
      exit 1
    fi
    echo "→ Restoring from $FILE"
    echo "  WARNING: this will overwrite existing data. Ctrl-C within 5s to abort."
    sleep 5
    pg_restore --clean --if-exists --no-acl --no-owner --dbname="$PG_URL" "$FILE"
    echo "✓ Restore complete."
    ;;

  list)
    if [[ ! -d "$BACKUP_DIR" ]]; then
      echo "No backups directory found at $BACKUP_DIR"
      exit 0
    fi
    echo "Backups in $BACKUP_DIR:"
    ls -lh "$BACKUP_DIR"/*.dump 2>/dev/null || echo "  (none)"
    ;;

  *)
    echo "Usage: $0 {backup|restore <file>|list}" >&2
    exit 1
    ;;
esac
