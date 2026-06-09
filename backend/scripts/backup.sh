#!/usr/bin/env bash
# Modir backup script (Phase 8, Task 8.8)
# Usage: docker compose --profile backup run --rm backup
#
# Dumps the Postgres database and mirrors the MinIO bucket to local backups/.
# Both pg_dump and the Python MinIO mirror use environment variables injected
# by docker-compose — no secrets are written to the backup itself.
set -euo pipefail

TS=$(date +%Y%m%d_%H%M%S)

mkdir -p backups/postgres backups/minio

# pg_dump requires a plain postgresql:// URL; strip the +asyncpg driver prefix
# that SQLAlchemy adds to the DATABASE_URL we receive from the environment.
PG_URL="${DATABASE_URL/+asyncpg/}"

echo "[${TS}] Starting Postgres backup…"
pg_dump "$PG_URL" | gzip > "backups/postgres/${TS}.sql.gz"
echo "[${TS}] Postgres backup: backups/postgres/${TS}.sql.gz"

echo "[${TS}] Starting MinIO mirror…"
python /app/scripts/minio_mirror.py backup "backups/minio/${TS}"
echo "[${TS}] MinIO backup: backups/minio/${TS}/"

echo "Backup complete: ${TS}"
