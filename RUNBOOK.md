# Modir Operations Runbook

On-call guide for the six most likely failure scenarios. Each section gives:
**Symptoms → Immediate action → Recovery command**.

---

## 1. Vault is down at startup

**Symptoms**
- `api` or `worker` container exits immediately with a non-zero code.
- Structured log contains `"event": "modir.vault.unreachable"` or a Python
  `RuntimeError: Vault unreachable` traceback.
- `docker compose ps` shows the `api` container in `Exited` state.

**Immediate action**
```bash
docker compose logs vault | tail -30          # Is Vault itself up?
docker compose ps vault                        # Check health status
```

**Recovery**
```bash
# Restart Vault (dev mode — data is in-memory, must re-seed secrets)
docker compose restart vault
docker compose run --rm vault-seed             # Re-inject all secrets
docker compose restart api worker              # Now Vault is healthy
```

> **Note:** In Vault dev mode all secrets are lost on container restart.
> Production Vault (integrated storage / Raft) survives restarts without re-seeding.

---

## 2. All LLM providers fail mid-request

**Symptoms**
- WhatsApp customers receive an Arabic apology message:
  *"الخدمة مش متاحة هلّق. حاول مرة تانية بعد شوي، أو ادخل الطلب يدوياً من اللوحة."*
- Structured log contains `"event": "supervisor.handle.error"` at WARNING level.
- `GET /health/ai` returns `{"available": false}`.
- Dashboard shows the AI-unavailable banner.

**Immediate action**
```bash
docker compose logs api | grep "supervisor.handle.error" | tail -10
```

Check the provider API keys are valid (Gemini quota, Grok/Anthropic billing).

**Recovery**
```bash
# Re-seed corrected API keys into Vault
GEMINI_API_KEY=<new-key> docker compose run --rm vault-seed

# The circuit breaker resets automatically 60 seconds after the last failure.
# Or restart the api to reset immediately:
docker compose restart api
```

**While LLM is down:** Abu Khaled can still accept orders manually via the
dashboard → **طلب يدوي** (`POST /orders/manual`). No AI involved.

---

## 3. Postgres connection lost

**Symptoms**
- `POST /webhooks/whatsapp` and other write endpoints return HTTP 503 with body
  `{"detail": "الخدمة غير متاحة مؤقتاً. حاول بعد قليل."}`.
- Structured log contains `"event": "db.connection.error"` at ERROR level.
- `GET /health` still returns 200 (health probe does not hit the DB).

**Immediate action**
```bash
docker compose ps db                           # Is Postgres up?
docker compose logs db | tail -20
docker compose exec db pg_isready -U modir    # Can we connect?
```

**Recovery**
```bash
docker compose restart db
# Wait for the db healthcheck to go green, then:
docker compose restart api worker
```

If data was lost (e.g., disk full, corruption):
```bash
docker compose --profile backup run --rm backup /app/scripts/restore.sh
```

---

## 4. Redis connection lost

**Symptoms**
- Rate-limiter dependency logs a warning; the webhook endpoint falls back to
  **allow-all** (no 429 is returned to any tenant). This is intentional — Redis
  failure degrades rate limiting but does not block orders.
- Structured log: `"event": "rate_limiter.redis_unavailable"` at WARNING level.

**Immediate action**
```bash
docker compose ps redis
docker compose logs redis | tail -10
```

**Recovery**
```bash
docker compose restart redis
docker compose restart api          # Re-connects the Redis client
```

Redis holds only ephemeral rate-limit counters — no persistent data to restore.

---

## 5. MinIO connection lost (OCR upload fails)

**Symptoms**
- `POST /bills` (supplier bill upload) returns HTTP 503.
- OCR worker logs: `"event": "worker.bill.failed"` for each bill that was in
  `uploaded` state when MinIO went down.
- Bill status stays `uploaded`; it will be retried automatically on the next
  worker pass once MinIO is back.

**Immediate action**
```bash
docker compose ps minio
docker compose logs minio | tail -10
```

**Recovery**
```bash
docker compose restart minio
# Worker retries automatically within worker_poll_seconds (default 5s).
# No manual intervention needed for bills stuck in `uploaded` state.
```

---

## 6. Worker crashes (cost alerts + OCR worker stop)

**Symptoms**
- `docker compose ps worker` shows `Exited`.
- New supplier bills pile up in `uploaded` status (no OCR processing).
- Cost alerts are not firing even when budgets are exceeded.

**Immediate action**
```bash
docker compose logs worker | tail -30          # Find the crash traceback
```

**Recovery**
```bash
docker compose restart worker
# The worker resumes from where it left off — no jobs are lost (bill rows
# are the source of truth; missed cost-alert sweeps resume on the next hour).
```

---

## Backup & Restore

### Run a backup
```bash
docker compose --profile backup run --rm backup
# Archives are written to backups/postgres/YYYYMMDD_HHMMSS.sql.gz
# and backups/minio/YYYYMMDD_HHMMSS/
```

### Restore from the most recent backup
```bash
# Stop write traffic first (or accept brief inconsistency during restore)
docker compose --profile backup run --rm backup /app/scripts/restore.sh
```

**Spot-check after restore:**
```bash
docker compose exec db psql -U modir -c "SELECT COUNT(*) FROM orders;"
# Compare against the pre-backup count recorded below.
```

### Proven restore time

| Date | Pre-backup order count | Restore duration | Outcome |
|------|------------------------|------------------|---------|
| *(run once manually and record here)* | | | |

Target: restore completes in **≤ 15 minutes** on a local dev stack with < 10k orders.

### Load test before a production deploy
```bash
uv run pytest tests/load/ -m load -v
# Requires the full stack running (db, redis, vault, api) with mocked LLM.
# Verifies: 100 concurrent requests, 10 tenants, zero cross-tenant data leak.
```
