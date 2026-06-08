"""One-command retrain of ALL three Phase 6 models (Task 6.12).

    cd backend
    DATABASE_URL=... REDIS_URL=... VAULT_ADDR=... VAULT_TOKEN=... \
    uv run python -m app.ml.train_all            # seeded DB already present
    uv run python -m app.ml.train_all --seed-first   # also (re)seed synthetic history first

Runs demand → churn → anomaly training from scratch, each refreshing its artifact +
model_card and appending to results.csv (Constitution IV: "retrain from scratch with a single
command"). Trainings are sequential (each opens and disposes its own engine; sklearn fits are
CPU-bound), so one event loop drives the whole thing.

Full reproduce path (also recorded in DECISIONS.md — Task 6.14):
  1. docker compose up -d db                      # Postgres 16 + pgvector
  2. cd backend
  3a. fresh DB:   uv run python -m app.ml.train_all --seed-first   # seed (Task 6.2) + train
  3b. seeded DB:  uv run python -m app.ml.train_all                # just retrain
  4. uv run python -m app.ml.eval                 # golden gate over the refreshed artifacts
  5. restart the api (ml_mode=trained) so the lifespan reloads the new *.joblib (served via DI)

Synthetic data caveat stands: this reproduces a correct, leakage-free, cross-validated
PIPELINE — not real-world accuracy. Swapping in real history needs no code change, just a
retrain + artifact commit.
"""

import argparse
import asyncio

from app.infra.logging import configure_logging, get_logger

log = get_logger("train_all")


async def train_all(
    *, seed_first: bool = False, seed_kwargs: dict | None = None
) -> dict[str, bool]:
    """Retrain all three models in order; returns {model: trained?} (False = no data to
    train on). Trainer/seed functions are looked up at call time so they stay swappable."""
    # Imported as module globals (resolved here, call-time) so a test can monkeypatch them.
    from app.ml.anomaly.train import train as train_anomaly
    from app.ml.churn.train import train as train_churn
    from app.ml.demand.train import train as train_demand
    from app.ml.seed_history import seed

    if seed_first:
        log.info("train_all.seed.start", **(seed_kwargs or {}))
        await seed(**(seed_kwargs or _DEFAULT_SEED))
        log.info("train_all.seed.done")

    results: dict[str, bool] = {}
    for name, trainer in (
        ("demand", train_demand),
        ("churn", train_churn),
        ("anomaly", train_anomaly),
    ):
        log.info("train_all.train.start", model=name)
        card = await trainer()
        results[name] = card is not None
        log.info("train_all.train.done", model=name, trained=results[name])

    log.info("train_all.done", results=results)
    return results


_DEFAULT_SEED = {
    "n_tenants": 3,
    "months": 12,
    "seed_value": 42,
    "n_customers": 60,
    "churn_fraction": 0.25,
}


def main() -> None:
    configure_logging()
    p = argparse.ArgumentParser(description="Retrain all Phase 6 models (demand/churn/anomaly).")
    p.add_argument(
        "--seed-first",
        action="store_true",
        help="(re)seed synthetic history before training (Task 6.2) — use on a fresh DB",
    )
    p.add_argument("--tenants", type=int, default=3, help="archetype tenants to seed (max 3)")
    p.add_argument("--months", type=int, default=12, help="months of history to seed")
    p.add_argument("--seed", type=int, default=42, help="seeding RNG seed (deterministic)")
    p.add_argument("--customers", type=int, default=60, help="customers per tenant to seed")
    p.add_argument("--churn-fraction", type=float, default=0.25, help="fraction that churns")
    args = p.parse_args()

    seed_kwargs = {
        "n_tenants": args.tenants,
        "months": args.months,
        "seed_value": args.seed,
        "n_customers": args.customers,
        "churn_fraction": args.churn_fraction,
    }
    asyncio.run(train_all(seed_first=args.seed_first, seed_kwargs=seed_kwargs))


if __name__ == "__main__":
    main()
