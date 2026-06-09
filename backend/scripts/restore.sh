#!/usr/bin/env bash
# Modir restore script (Phase 8, Task 8.8)
# Usage: docker compose --profile backup run --rm backup /app/scripts/restore.sh
#
# Restores the most recent Postgres dump and MinIO backup. DESTRUCTIVE — it
# drops and re-creates all data. Only run against a stopped or empty instance.
# Spot-check after restore: compare order count against the pre-backup count.
set -euo pipefail

PG_URL="${DATABASE_URL/+asyncpg/}"

LATEST_PG=$(ls -t backups/postgres/*.sql.gz 2>/dev/null | head -1)
if [[ -z "$LATEST_PG" ]]; then
    echo "ERROR: no Postgres backup found in backups/postgres/" >&2
    exit 1
fi

LATEST_MINIO=$(ls -td backups/minio/*/ 2>/dev/null | head -1)
if [[ -z "$LATEST_MINIO" ]]; then
    echo "ERROR: no MinIO backup found in backups/minio/" >&2
    exit 1
fi

echo "Restoring Postgres from: ${LATEST_PG}"
gunzip -c "$LATEST_PG" | psql "$PG_URL"
echo "Postgres restore complete."

echo "Restoring MinIO from: ${LATEST_MINIO}"
python /app/scripts/minio_mirror.py restore "$LATEST_MINIO"
echo "MinIO restore complete."

echo "Restore complete from: ${LATEST_PG}"
