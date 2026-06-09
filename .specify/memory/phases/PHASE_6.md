# Phase 6 — The ML Layer

> Real trained models predicting demand for next week, flagging at-risk
> customers, and catching anomalies in daily revenue. ML predicts; the LLM
> only ever explains. This is where Constitution IV stops being a promise and
> becomes code.

Read `.specify/memory/constitution.md` FIRST (Principle IV is the heart of this
phase). Then read `.specify/memory/ROADMAP.md` (the Phase 6 section). Then this
file. Work task by task; one task = one branch = one PR; pause for approval after
each task before committing.

---

## Goal

Three trained models, each behind a clean prediction seam, each loaded ONCE via
the FastAPI `lifespan` handler and served through dependency injection:

1. **Demand forecaster** — per product, next-N-days demand (regression).
2. **Churn classifier** — which customers are at risk of not returning (binary,
   imbalanced).
3. **Revenue anomaly detector** — is today's revenue weird for this tenant?
   (unsupervised + threshold).

By the end of Phase 6:
- `InventoryAgent.forecast_demand` is backed by the trained demand model behind
  the **exact same signature** it has today — no caller changes (it is a
  documented placeholder right now: `app/agents/inventory/tools.py:67`).
- Every experiment is logged to `results.csv` with CV mean ± std across ≥3
  candidate models per task.
- Lebanese seasonality (Ramadan, summer, day-of-week, paydays) is engineered into
  the features and documented.
- Models retrain from scratch with **one command** (`uv run python -m
  app.ml.train_all`), in version-controlled code — never only in a notebook.
- Golden eval sets in CI catch a regression (break a model on purpose, watch CI go
  red).

---

## Resist scope creep

This phase is ML, not agents and not UI. In particular:
- **No new agents and no supervisor.** That is Phase 7. The Finance Agent,
  Customer Agent, and Advisor Agent do NOT get built here. The only agent touched
  is `InventoryAgent`, and only to swap its placeholder forecast for the model
  behind the same signature.
- **No churn re-engagement send, no anomaly auto-response.** Those are HIL actions
  for Phase 7. Phase 6 produces predictions and serves them; it does not act on
  them. (No new Level-2 actions, no new ActionGate strings.)
- **Minimal frontend.** At most one small, read-only "Insights" panel that renders
  predictions for the logged-in tenant (optional — Task 6.13). No charts library
  spree, no dashboard polish ([[phase3-frontend-polish-pending]] still stands).
- **No LLM in the prediction path.** The LLM may later *explain* a prediction
  (Phase 7 briefings); in Phase 6 the model outputs numbers and the API returns
  numbers.

---

## Prerequisites (read before Task 6.1)

- **Phase 5 is complete and merged** — OCR pipeline, bill→stock HIL, and the two
  RAG corpora (`knowledge`, `bills`) all green; worker image builds; frontend
  builds. (Confirmed: 260 backend tests green, CI green.)
- **ML dependencies are NOT yet installed.** `app/ml/` is an empty package
  (`__init__.py` only); `pyproject.toml` has no scikit-learn / xgboost / prophet /
  joblib / pandas / numpy(direct). Task 6.1 adds them, pinned, via `uv`.
- **⚠️ The data problem — flagged up front (Task 6.2 addresses it).** Phase 6 needs
  MONTHS of realistic order/inventory/customer history with Lebanese seasonality to
  train on. The dev DB is test cruft. Before any model can train, we generate a
  deterministic, seasonal, multi-tenant synthetic history (Ramadan + summer + day-of-
  week + paydays baked in). This is the single biggest task in the phase and the one
  most likely to be wrong, so it comes early and gets its own validation.
  - **Honesty rule:** synthetic data must be LABELED as synthetic (a `data_source`
    marker on seeded tenants) and the models' reported metrics carry the caveat
    "trained on synthetic seasonal data" in `DECISIONS.md`. We do not claim real-world
    accuracy from synthetic data — we claim the *pipeline* is correct, leakage-free,
    cross-validated, and the seasonality signal is learnable.

---

## Core constraint reminder — Constitution IV, line by line

Every task in this phase is graded against these. If a task can't answer them, it
isn't done.

1. **ML predicts, LLM explains.** Forecasting/churn/anomaly use scikit-learn /
   XGBoost / Prophet — never an LLM prompt. No exceptions in this phase.
2. **No leakage.** All preprocessing lives INSIDE a `sklearn.Pipeline` (or an
   equivalent fit-on-train-only transform). Features are computed only from data
   available STRICTLY BEFORE the prediction date. Time-series splits, never random
   shuffles, for the temporal models.
3. **Cross-validation + per-class metrics.** k-fold (TimeSeriesSplit for temporal),
   ≥3 candidate models per task in `results.csv` with mean ± std. The churn model
   reports per-class precision/recall/F1 (the imbalance is real).
4. **Versioned training, one-command retrain.** Training is `.py` under `app/ml/`,
   runnable as `python -m app.ml.train_<x>` and `python -m app.ml.train_all`.
   Notebooks may explore but are never the source of truth ([[phase6-ml-colab-decision]]).
5. **Load once via lifespan, serve via DI.** `joblib.load` happens in `lifespan`
   (and in the worker's `build_pipeline` if it needs a model) — NEVER inside a route
   handler. The lifespan already reserves the spot: `app/main.py:134`
   `# Future: app.state.demand_model = joblib.load(...)`.
6. **The Wall still holds.** Training reads history through tenant-scoped
   repositories (or one explicitly-documented cross-tenant training query, like the
   worker's `tenants_with_claimable_bills`). Predictions are tenant-scoped. A model
   artifact may be shared across tenants (it's just learned weights), but every
   FEATURE row fed in and every prediction served is for one tenant.

---

## Architecture decisions (recorded — read before building)

These are decisions for THIS phase. The constitution wins on any conflict. They
also belong in `docs/DECISIONS.md` (Task 6.14; docs/ is gitignored, stays local).

- **AD-6.1 — Code-first training, Colab optional.** The pipeline lives in
  `app/ml/*.py` and is the single source of truth (Constitution IV + one-command
  retrain). Colab is allowed only as optional compute: a thin notebook imports
  `app.ml.*` and runs it; the user re-inserts the produced `.joblib`. Colab has no
  Postgres, so notebooks train from an exported CSV/parquet snapshot
  ([[phase6-ml-colab-decision]]).

- **AD-6.2 — Library choices.**
  - Demand: start with **scikit-learn** `HistGradientBoostingRegressor` / `Ridge`
    baseline; compare against **XGBoost**. Prophet is OPTIONAL and only if the
    per-product series are long enough — for short/sparse product series, a tabular
    regressor with seasonality FEATURES beats Prophet and is simpler to serve. Decide
    per the data in Task 6.5; record the choice.
  - Churn: **LogisticRegression** (interpretable baseline) vs **RandomForest** vs
    **XGBoost**, with class weighting / `scale_pos_weight` for the imbalance.
  - Anomaly: **robust z-score / IQR on a seasonal residual** as the baseline, vs
    **IsolationForest**. Unsupervised + a tunable threshold; report what the
    threshold flags on held-out days.

- **AD-6.3 — One prediction seam per model, mirroring the infra seams.** Each model
  gets a small `Predictor` class (`DemandPredictor`, `ChurnPredictor`,
  `AnomalyDetector`) that wraps the loaded pipeline and exposes a typed `predict(...)`.
  This mirrors how `OCREngine` / `EmbeddingClient` are seams: built once, injected,
  and swappable. A `stub` predictor (deterministic, no artifact) is the CI/dev
  default so tests stay offline and don't need a trained `.joblib` in the repo —
  EXACTLY like `ocr_mode=stub` / `embedding_mode=stub`.

- **AD-6.4 — Artifacts are outputs, not code. DECIDED: commit small artifacts.**
  Trained `.joblib` files + a `model_card.json` (metrics, training window, feature
  list, data_source) live under `backend/app/ml/artifacts/` and ARE committed when
  small (≲ a few MB), so the lifespan and the CI golden-eval job load REAL models
  (and a Colab-trained artifact is re-inserted via a normal PR). If a model ever grows
  too large to commit, that single model git-ignores its artifact and CI falls back to
  its stub for that one — but the default is commit. The TRAINING CODE is always
  committed regardless.

- **AD-6.5 — `forecast_demand` stays sync, same signature.** Today it is
  `forecast_demand(ctx, inventory) -> int` (sync). Phase 6 keeps that exact shape: the
  trained `DemandPredictor` is reached via `ctx` (added to `ToolContext`), the call
  stays sync (joblib/sklearn predict is CPU, fast, non-blocking for one row), and the
  fallback when no model/history exists is the SAME documented default it returns now.
  No caller in `agent.py` changes.

- **AD-6.6 — No leakage, enforced structurally.** Feature builders take an explicit
  `as_of` date and may only read rows with `created_at < as_of` (orders) or the
  equivalent. Temporal CV uses `TimeSeriesSplit`. A dedicated leakage test asserts that
  shifting `as_of` earlier strictly reduces the data the builder sees.

---

## New / changed data model (read before you build)

Phase 6 adds **almost no schema** — it trains on data that already exists. The only
additions are small and optional:

- **`prediction_runs` (optional, Task 6.10)** — an audit/observability row per
  served prediction batch: `(id, tenant_id, model, as_of, horizon, summary JSONB,
  created_at)`. Tenant-scoped (the Wall). This is the ML analogue of `order_events` /
  `purchase_order_events`: a breadcrumb so a prediction shown to the owner is
  reproducible and auditable. If we decide predictions are pure read-through (compute
  on request, don't persist), this table is dropped — decided in Task 6.10.

- **`tenants.data_source` marker (Task 6.2)** — a nullable `String(16)` column
  (`"synthetic"` for seeded demo tenants, null/`"real"` otherwise) so synthetic
  training data is honestly labeled and can be excluded from anything that must be
  real. Small migration.

Everything else is READ: `orders` + `order_items` (demand & revenue), `inventory`
movements (stock context), `customers` + their order history (churn), and the
`bills` RAG corpus (optional forecasting context, Task 6.5). No new copies of these
tables — we read the ones Phases 1–5 built.

---

## The Phase 6 shape (what we wire end-to-end)

```
                         ┌──────────────────────── training (offline, one command) ───────────────────────┐
  seasonal synthetic     │  repositories ──► feature builders (as_of, no leakage)                          │
  history (Task 6.2) ───►│        │              │                                                          │
                         │        ▼              ▼                                                          │
                         │  sklearn.Pipeline (preprocess INSIDE) ──► TimeSeriesSplit / k-fold CV           │
                         │        │              │                                                          │
                         │        ▼              ▼                                                          │
                         │  results.csv (≥3 models, mean±std)   joblib artifact + model_card.json          │
                         └────────────────────────────────────────────────────────────────────────────────┘
                                                          │ artifact
                                                          ▼
  startup (lifespan) ──► joblib.load ONCE ──► app.state.demand_predictor / churn / anomaly  (or stub)
                                                          │ DI
                            ┌─────────────────────────────┼─────────────────────────────┐
                            ▼                              ▼                             ▼
                 InventoryAgent.forecast_demand   GET /predictions/demand     GET /predictions/churn
                 (same signature, AD-6.5)         GET /predictions/anomaly    (read-only, tenant-scoped)
```

CI default everywhere is the **stub predictor** (offline, deterministic), exactly
like `ocr_mode=stub` / `embedding_mode=stub` — so the suite needs no trained
artifact and no network. Golden evals (Task 6.11) load the REAL artifacts when
present and assert they don't regress.

---

## Phase 6 — Tasks Overview

| # | Task | Branch |
|---|------|--------|
| 6.1 | ML deps + `app/ml/` layout + `results.csv` + artifacts dir + Settings seams | `feature/MOD-6-ml-scaffold` |
| 6.2 | **Seasonal synthetic history generator** (Ramadan/summer/DOW/paydays) + `data_source` marker + migration | `feature/MOD-6-seed-history` |
| 6.3 | Training data access: tenant-scoped read repos + the one documented cross-tenant training query | `feature/MOD-6-train-data` |
| 6.4 | Feature builders with `as_of` + Lebanese seasonality + the leakage test | `feature/MOD-6-features` |
| 6.5 | **Demand forecaster**: pipeline, ≥3 models, TimeSeriesSplit, results.csv, artifact + card | `feature/MOD-6-demand-train` |
| 6.6 | `DemandPredictor` seam (+ stub) + lifespan load + Settings | `feature/MOD-6-demand-serve` |
| 6.7 | Wire `forecast_demand` to `DemandPredictor` (same signature, AD-6.5) + tests | `feature/MOD-6-demand-agent` |
| 6.8 | **Churn classifier**: label rule, pipeline, ≥3 models, per-class metrics, artifact + card | `feature/MOD-6-churn` |
| 6.9 | **Revenue anomaly detector**: seasonal residual + threshold vs IsolationForest, artifact + card | `feature/MOD-6-anomaly` |
| 6.10 | `/predictions/*` read-only API (+ optional `prediction_runs`) — all tenant-scoped | `feature/MOD-6-predictions-api` |
| 6.11 | Golden eval sets (20/model) + `eval_thresholds.yaml` + CI eval job | `feature/MOD-6-golden-evals` |
| 6.12 | `train_all` one-command entrypoint + retrain-from-scratch doc | `feature/MOD-6-train-all` |
| 6.13 | *(optional)* read-only "Insights" panel (RTL, Arabic) | `feature/MOD-6-insights-ui` |
| 6.14 | CI guards (ML offline by default, no leakage test runs) + `DECISIONS.md` (model cards, labels, features) | `feature/MOD-6-ci-decisions` |

14 tasks (13 if 6.13 is skipped). One branch / one PR / approval each.

---

## Task 6.1 — ML deps + `app/ml/` layout + results.csv + artifacts + Settings

Add the ML stack via `uv` (pinned): `scikit-learn`, `xgboost`, `pandas`,
`numpy`, `joblib`; `prophet` only if Task 6.5 decides to use it (defer the dep until
then to keep the image lean). Lay out `app/ml/`:

```
app/ml/
  __init__.py
  features/        # feature builders (Task 6.4)
  demand/          # train.py, predictor.py (Tasks 6.5/6.6)
  churn/
  anomaly/
  predictors.py    # the seam: build_*_predictor(settings) → real | stub  (AD-6.3)
  results.py       # append a row to results.csv (model, params, metric, mean, std, ts)
  artifacts/       # *.joblib + model_card.json  (AD-6.4)
  results.csv      # the experiment log (header committed; rows appended by training)
```

Add Settings seams mirroring `ocr_mode` / `embedding_mode`:
`ml_mode: "stub" | "trained"` (default `"stub"` — CI/dev offline), and the artifact
paths. **DoD:** `uv sync` resolves pinned; `import app.ml` works; CI's import test +
forbidden-pattern + provider-SDK gates stay green; `results.csv` exists with a header.
Decide & record the artifact commit policy (AD-6.4).

## Task 6.2 — Seasonal synthetic history generator (the data problem)

A deterministic (seeded RNG) generator that creates N months of realistic
multi-tenant history: customers, orders, order_items, and the inventory movements
that follow — with Lebanese seasonality baked in:
- **Ramadan** (compute the window for the seeded year): demand shape shifts —
  certain categories up (sweets/dates), daytime down, evening spike.
- **Summer-mountain** (Jul–Aug): a lift for resort/mountain-shop archetypes.
- **Day-of-week**: weekend vs weekday curves.
- **Paydays / month-end**: a small recurring bump.
- **Churn signal**: some customers taper off and stop (the label source for 6.8);
  most are recurring (the imbalance is intentional and realistic).

**DECIDED: 3 archetype tenants × 12 months, window Jun 2024 – May 2025.** (Started at
8 months / Oct 2024–May 2025, but that window has no summer month, so the
summer-mountain feature would have zero positive examples; extended to 12 months so it
contains BOTH summer 2024 (Jun–Aug) AND Ramadan 2025 (Mar 1–30) and TimeSeriesSplit
gets a full year of folds.) Validated on the dev DB: bakery Ramadan sweets 39.1 vs 11.6
units/day (3.4×), mountain summer 10.5 vs 5.1 orders/day (2×), weekend 30.1 vs 24.2,
churn ~28% of customers. It runs as a script
(`python -m app.ml.seed_history --tenants 3 --months 12 --seed 42`),
is tenant-scoped through the repositories, stamps seeded tenants `data_source="synthetic"`
(new column + migration, run in the `moudir-api-1` container), and is idempotent /
re-runnable. **DoD:** running it produces months of orders for ≥3 archetype tenants;
a quick aggregate (orders per day per tenant) visibly shows the Ramadan and weekend
shapes; everything carries the right `tenant_id`; synthetic tenants are labeled.
**This is the largest, riskiest task — validate the curves before moving on.**

## Task 6.3 — Training data access (read repos + the one cross-tenant query)

Tenant-scoped read methods for training: daily demand per product, daily revenue per
tenant, per-customer order history. One explicitly-documented cross-tenant query
(`tenants_for_training(session)`, mirroring `tenants_with_claimable_bills`) that only
DISCOVERS which tenants have enough history — then training re-enters the Wall per
tenant for feature rows. No raw `text()` in repos (CI guard). Plus an exporter
(`dump training data → parquet/CSV`) so Colab can train offline (AD-6.1). **DoD:**
methods return correct tenant-scoped frames; the cross-tenant discovery query is the
only one that crosses, and it's documented; a leakage-relevant ordering (by date) is
guaranteed.

## Task 6.4 — Feature builders + Lebanese seasonality + the leakage test

Pure functions: `build_demand_features(history, as_of)`,
`build_churn_features(...)`, `build_anomaly_features(...)`. Seasonality features:
day-of-week, is_ramadan, is_summer, is_payday/month-end, lag/rolling demand
(strictly before `as_of`), recency/frequency/monetary for churn. **No leakage,
enforced by AD-6.6:** builders only read rows before `as_of`. **DoD:** a leakage
test asserts that an earlier `as_of` strictly shrinks the visible data and that no
feature uses a future row; seasonality columns documented.

## Task 6.5 — Demand forecaster training

`sklearn.Pipeline` with preprocessing INSIDE. Compare ≥3 candidates (Ridge/HGBR
baseline, XGBoost, and one more — optionally Prophet per AD-6.2). `TimeSeriesSplit`
CV; log every run to `results.csv` (model, params, MAE/MAPE, mean±std). Save the
winner as `artifacts/demand.joblib` + `model_card.json` (window, features,
data_source="synthetic", metrics). **DoD:** ≥3 models in results.csv with CV
mean±std; winner saved with a card; retrain via `python -m app.ml.train_demand`;
documented behavior for a brand-new product with no history (fall back to default).

## Task 6.6 — `DemandPredictor` seam + stub + lifespan load

`DemandPredictor.predict(...)` wrapping the loaded pipeline; a `StubDemandPredictor`
(deterministic, no artifact) for CI/dev. `build_demand_predictor(settings)` returns
real (ml_mode=trained, artifact present) or stub. Load ONCE in `lifespan`
(`app.state.demand_predictor`), filling the reserved `app/main.py:134` slot — never
in a route. **DoD:** lifespan loads it once; import test green with stub default;
a unit test covers both real-load and stub paths.

## Task 6.7 — Wire `forecast_demand` to the model (same signature)

Add the predictor to `ToolContext`; `forecast_demand(ctx, inventory)` calls
`ctx.demand_predictor.predict(...)`, keeping the EXACT sync signature and the SAME
documented fallback (owner's `reorder_quantity`, else default) when there's no
history/model (AD-6.5). `agent.py` does not change. **DoD:** existing InventoryAgent
tests still pass unchanged; a new test shows the model path drives the suggested qty
when a trained predictor is injected, and the fallback path when the stub/empty.

## Task 6.8 — Churn classifier (per-class metrics, imbalanced)

Documented label rule for "at risk" (e.g. no order in X days given their historical
cadence — write the exact rule in the card + DECISIONS). Pipeline with preprocessing
inside; compare LogisticRegression vs RandomForest vs XGBoost with class weighting;
k-fold CV; **report per-class precision/recall/F1** (not macro-only) to results.csv.
Save `artifacts/churn.joblib` + card. **DoD:** ≥3 models with per-class metrics in
results.csv; label rule documented; class imbalance handled and shown; retrain via
`python -m app.ml.train_churn`.

## Task 6.9 — Revenue anomaly detector

Seasonal-residual robust z-score / IQR baseline vs IsolationForest (AD-6.2);
unsupervised + tunable threshold. Report what it flags on held-out days and the
chosen threshold's rationale. Save `artifacts/anomaly.joblib` (or a small params
file) + card. **DoD:** detector flags an injected anomalous day on synthetic data
and stays quiet on normal days; threshold + method documented; retrain via
`python -m app.ml.train_anomaly`.

## Task 6.10 — `/predictions/*` read-only API (+ optional prediction_runs)

`GET /predictions/demand`, `/predictions/churn`, `/predictions/anomaly` — all
behind auth, all tenant-scoped via the existing `get_current_user` →
tenant-scoping path, all served from the lifespan-loaded predictors (DI). Decide
whether to persist `prediction_runs` (audit breadcrumb) or compute read-through;
record it. **No new HIL action** — these are read-only. **DoD:** each endpoint
returns this tenant's predictions only; a cross-tenant test proves Tenant A sees no
Tenant B prediction; predictors are NOT loaded in the handler.

## Task 6.11 — Golden eval sets + thresholds + CI eval job

20 hand-curated cases per model (held-out as_of windows / labeled customers /
known anomalous days). `eval_thresholds.yaml` sets the floor (e.g. demand MAPE ≤ X,
churn recall-on-positive ≥ Y, anomaly precision ≥ Z). A CI step runs the evals
against the REAL artifacts when present (skips/uses stub when absent, clearly). **DoD:**
break a model on purpose → eval job fails; thresholds documented and justified.

## Task 6.12 — `train_all` one-command retrain

`python -m app.ml.train_all` runs all three trainings from scratch (assumes seeded
history present; offers a `--seed-first` convenience that calls 6.2). Documents the
full reproduce path (seed → train_all → artifacts → restart api). **DoD:** one
command retrains all three and refreshes artifacts + results.csv; the reproduce doc
is in DECISIONS.

## Task 6.13 — *(optional)* Insights panel (frontend)

A small read-only panel rendering the three predictions for the logged-in tenant —
RTL, Lebanese Arabic, dual currency, works at 360px. Functional, not polished
([[phase3-frontend-polish-pending]]). `npm run lint` + `npm run typecheck` +
`npm run build` green. Skip if time-boxed; the API (6.10) is the deliverable.

## Task 6.14 — CI guards + DECISIONS.md

CI: ML stays offline by default (ml_mode=stub), the leakage test runs in the suite,
the new ML deps don't trip the provider-SDK / forbidden-pattern gates, and (if
artifacts are committed) the golden-eval job runs. `docs/DECISIONS.md` (local-only,
docs/ is gitignored) captures: every model's label rule + feature justification +
strongest predictor, the 3-classifier comparison per task, the seasonality features,
the synthetic-data honesty caveat, and the artifact commit policy. **DoD:** CI green;
every Phase 6 defend-it question is answerable from DECISIONS + the cards.

---

## Phase 6 — Definition of Done

- [ ] Three trained models, all loaded at startup via lifespan, all serving predictions through DI (never loaded in a route).
- [ ] `results.csv` shows ≥3 candidate models per task with CV mean ± std.
- [ ] Each model has a documented labeling rule and feature justification (model card + DECISIONS.md).
- [ ] Lebanese seasonality features (Ramadan, summer, day-of-week, payday) are in the training data and documented.
- [ ] A leakage test exists and passes; temporal models use TimeSeriesSplit, not random shuffle.
- [ ] The churn model reports per-class precision/recall/F1 (not macro-only) on the imbalanced problem.
- [ ] Golden evals in CI — break a model intentionally, watch CI fail.
- [ ] `python -m app.ml.train_all` retrains all three from scratch in one command.
- [ ] `InventoryAgent.forecast_demand` is backed by the trained demand model behind the SAME signature; existing InventoryAgent tests pass unchanged.
- [ ] `/predictions/*` are tenant-scoped; a cross-tenant test proves no leak.
- [ ] CI green (backend suite offline via stub; worker image builds; frontend builds); host-verified with `uv run pytest` (Docker DNS blocked in-container).
- [ ] Synthetic data is honestly labeled (`data_source`) and the synthetic caveat is in DECISIONS.md.

## Phase 6 — Defend-it preparation

- What is your label for the churn model? How did you define "at risk", and why?
- Walk me through your features. Which is the strongest predictor and how do you know?
- Why this classifier and not the other two you compared? (point to results.csv)
- Show me where each model is loaded. How many times per process? (lifespan, once)
- What does the demand model do for a brand-new product with no history?
- How do you prevent leakage? Show me the test and the `as_of` boundary.
- Why TimeSeriesSplit and not k-fold shuffle for demand?
- The data is synthetic — what can you honestly claim, and what can't you?
- Where does the InventoryAgent get its forecast now, and what changed for callers? (nothing)
- How is a prediction kept inside the Wall — training AND serving?

## Ready for Phase 7?

You are ready when:
- Every checkbox above is checked and all defend-it questions answer fluently.
- `cd backend && uv run pytest tests` is green (incl. leakage, per-class churn, and
  cross-tenant prediction tests); the golden-eval job, worker image, and frontend
  jobs are green.
- One command retrains all three models from scratch and the api serves the refreshed
  predictions after a restart.
- A demo shows: the InventoryAgent drafting a reorder whose quantity comes from the
  trained demand model, and `/predictions/*` returning demand/churn/anomaly for one
  tenant — and ONLY that tenant.

Phase 7 is the Full Agent System — the LangGraph supervisor wiring all five agents,
which will CALL these Phase 6 predictions (and the Phase 5 RAG) as tools. Do not start
it until the three models train, load once, and serve cleanly.
