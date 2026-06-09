# Modir — 5-Minute Demo Script

This script walks through the full Modir flow end-to-end. All cURL commands
use the seeded demo data. Run `seed_demo` first if you haven't already:

```bash
docker compose exec api python -m scripts.seed_demo
```

Then start the dashboard:

```bash
cd frontend && npm run dev   # http://localhost:5173
```

---

## Act 1 — Customer places an order (2 min)

A customer texts the bakery's WhatsApp number in Lebanese Arabic. Modir parses
the order, validates it against the live catalog, and confirms.

```bash
curl -s -X POST http://localhost:8000/webhooks/whatsapp \
  -H "Content-Type: application/json" \
  -d '{
    "from": "+96171111001",
    "to":   "+96170000001",
    "text": "مرحبا، بدي ٣ كعكات بالسمسم وبقلاوة كيلو بكرا الصبح",
    "message_id": "demo-msg-001",
    "timestamp": "2026-06-09T09:00:00Z"
  }' | python -m json.tool
```

**What to show:**
- The `reply` field in the JSON response — a Lebanese Arabic confirmation with
  the total in LBP.
- The structured log line in `docker compose logs api` — shows `tenant_id`,
  `customer_id`, which tools fired (`get_products`, `parse_order`,
  `confirm_order`), and token usage.

**Try an unavailable product** to show the guardrail:

```bash
curl -s -X POST http://localhost:8000/webhooks/whatsapp \
  -H "Content-Type: application/json" \
  -d '{
    "from": "+96171111002",
    "to":   "+96170000001",
    "text": "بدي بيتزا من فضلك",
    "message_id": "demo-msg-002",
    "timestamp": "2026-06-09T09:01:00Z"
  }' | python -m json.tool
```

The agent replies that pizza is not in the catalog — it does not hallucinate
a confirmation.

---

## Act 2 — Owner dashboard (1 min)

1. Open `http://localhost:5173` in your browser.
2. Log in with `demo@modir.test` / `DemoPassword1`.
3. Navigate to **الطلبات** (Orders) — the order from Act 1 is at the top.
4. Navigate to **الزبائن** (Customers) — 5 seeded customers with Arabic names.
5. Navigate to **التكاليف** (Costs) — 30-day bar chart from the seeded
   `agent_runs` data, showing per-agent cost breakdown.

**Optional:** send a request from the registered owner phone to show the
dispatcher routes to the AI assistant, not the OrderAgent:

```bash
curl -s -X POST http://localhost:8000/webhooks/whatsapp \
  -H "Content-Type: application/json" \
  -d '{
    "from": "+96170000002",
    "to":   "+96170000001",
    "text": "كيف مبيعاتي اليوم؟",
    "message_id": "demo-msg-003",
    "timestamp": "2026-06-09T09:02:00Z"
  }' | python -m json.tool
```

---

## Act 3 — ML demand forecast (30 sec)

First, get a product ID (ka'ak) from the API:

```bash
TOKEN=$(curl -s -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"demo@modir.test","password":"DemoPassword1"}' \
  | python -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

# List products and note the id for كعك بالسمسم
curl -s http://localhost:8000/profile/products \
  -H "Authorization: Bearer $TOKEN" | python -m json.tool
```

Then call the demand forecast endpoint with that product ID:

```bash
PRODUCT_ID="<paste-kak-id-here>"

curl -s "http://localhost:8000/predictions/demand?product_id=${PRODUCT_ID}&days=7" \
  -H "Authorization: Bearer $TOKEN" | python -m json.tool
```

**What to show:** the `predicted_units_7d` array — one number per day.
The forecast is from the trained HGBR model (loaded at startup, never
re-loaded per request). Point to `DECISIONS.md AD-6.1` for the model choice.

---

## Act 4 — Graceful degradation (30 sec)

Show that the business keeps running when AI is entirely down.

**Check the AI health endpoint:**

```bash
curl -s http://localhost:8000/health/ai | python -m json.tool
# {"available": true}
```

**Simulate AI unavailability** (chaos test equivalent — mock at the test level;
for a live demo, restart the api with a bad GEMINI_API_KEY in .env and re-seed):

```bash
# Easier live demo: just show the manual order form directly
# Navigate to http://localhost:5173/orders/manual
```

**Create a manual order through the dashboard form:**
- Customer phone: `+96171111003`
- Product: كعك بالسمسم × 2, بقلاوة بالفستق × 1

Or via API:

```bash
curl -s -X POST http://localhost:8000/orders/manual \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "customer_phone": "+96171111003",
    "items": [
      {"product_id": "'${PRODUCT_ID}'", "quantity": 2}
    ]
  }' | python -m json.tool
```

**What to show:** the response has `"source": "manual"`. Navigate to the
Orders page — the manual order appears alongside agent-created orders.
No AI was involved. Constitution V in practice: the human is the operator.

---

## Act 5 — Observability (30 sec)

```bash
# Start the observability stack (separate terminal)
LOKI_URL=http://loki:3100 docker compose --profile observability up loki grafana -d
```

1. Open `http://localhost:3000` (Grafana, anonymous read).
2. Navigate to **Dashboards → Modir**.
3. Show the 6 panels: request rate, error rate, LLM fallback activations,
   rate-limit hits, per-tenant cost, agent latency.
4. Send one more webhook request (any of the Act 1 commands) and watch the
   request-rate panel update within ~10 seconds.
5. In **Explore → Loki**, query:
   ```
   {app="modir"} | json | tenant_id != ""
   ```
   Every structured log line from the API appears here, filterable by
   `tenant_id`, `level`, or any field.

---

## Defend-it prompts for Q&A after the demo

> "Show me where tenant isolation is enforced."

`backend/app/repositories/base.py` — `_require_tenant_scope`. Every query
passes through this one method.

> "How do you know the red-team injection rate is above 92%?"

`docker compose exec api python -m app.agents.eval.evaluate_agents`
exits 0 when the block rate meets the threshold in `agent_thresholds.yaml`.
CI runs this on every push.

> "Walk me through a restore."

`RUNBOOK.md` → Scenario 1. Two commands: `backup.sh` then `restore.sh`.
Elapsed time is recorded in the runbook.

> "What happens when all three LLM providers fail?"

`tests/test_chaos.py::test_all_llm_providers_exhausted` — asserts HTTP 200
with an Arabic apology message and a `WARNING` log line. No 500, no crash.

---

*Full defend-it Q&A: `docs/FOR_REVIEWERS.md`*
*Architecture decisions: `DECISIONS.md`*
*Failure playbooks: `RUNBOOK.md`*
