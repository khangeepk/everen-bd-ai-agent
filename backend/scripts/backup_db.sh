#!/usr/bin/env bash
# Dump the production database with pg_dump.
#
# Portable by design: works whether Render's built-in automated Postgres
# backups (see DEPLOYMENT.md -- the default, zero-setup mechanism on the
# "Basic" plan) are enabled or not, and is not tied to any one cloud
# provider, so switching hosts later doesn't strand your backup story.
#
# Usage:
#   DATABASE_URL=postgresql+asyncpg://user:pass@host:5432/db ./backup_db.sh
#
# Optional S3-compatible upload (AWS S3, Backblaze B2, Cloudflare R2, ...):
#   set BACKUP_S3_BUCKET (and AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY /
#   AWS_ENDPOINT_URL for non-AWS providers) and the dump is uploaded via the
#   AWS CLI after being written locally. If BACKUP_S3_BUCKET is unset, the
#   script just leaves the dump in --out-dir (the scheduled GitHub Actions
#   workflow then uploads it as a build artifact instead -- see
#   .github/workflows/db-backup.yml).
set -euo pipefail

OUT_DIR="${1:-./backups}"
mkdir -p "$OUT_DIR"

if [ -z "${DATABASE_URL:-}" ]; then
  echo "ERROR: DATABASE_URL is not set." >&2
  exit 1
fi

# pg_dump doesn't understand the SQLAlchemy "+asyncpg" driver suffix -- strip
# it to get a plain libpq-compatible URL.
PG_URL="${DATABASE_URL/postgresql+asyncpg:\/\//postgresql://}"

TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
DUMP_FILE="$OUT_DIR/everen_db_${TIMESTAMP}.dump"

echo "Dumping database to $DUMP_FILE ..."
pg_dump "$PG_URL" --format=custom --no-owner --no-privileges --file="$DUMP_FILE"
echo "Dump complete: $(du -h "$DUMP_FILE" | cut -f1)"

if [ -n "${BACKUP_S3_BUCKET:-}" ]; then
  if ! command -v aws >/dev/null 2>&1; then
    echo "ERROR: BACKUP_S3_BUCKET is set but the aws CLI is not installed." >&2
    exit 1
  fi
  DEST="s3://${BACKUP_S3_BUCKET}/db-backups/$(basename "$DUMP_FILE")"
  echo "Uploading to $DEST ..."
  aws s3 cp "$DUMP_FILE" "$DEST"
  echo "Upload complete."
else
  echo "BACKUP_S3_BUCKET not set -- dump left at $DUMP_FILE for the caller to archive."
fi

# Restore with:
#   pg_restore --clean --if-exists --no-owner --no-privileges -d "$PG_URL" "$DUMP_FILE"
