"""Task 6.12 — `python -m app.ml.train_all` retrains all three models in one command.

Proves the orchestration without touching the DB: the three trainers (and the optional
seeder) are monkeypatched, and we assert train_all runs demand → churn → anomaly in order,
maps each trainer's result to trained?/not, and seeds first only when asked. The real
trainings themselves are covered by their own task tests (6.5/6.8/6.9).
"""

from app.ml import train_all as train_all_mod


def _recording_trainer(name: str, calls: list, *, card: object | None):
    async def _train():
        calls.append(name)
        return card

    return _train


async def test_runs_all_three_in_order(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        "app.ml.demand.train.train", _recording_trainer("demand", calls, card=object())
    )
    monkeypatch.setattr(
        "app.ml.churn.train.train", _recording_trainer("churn", calls, card=object())
    )
    monkeypatch.setattr(
        "app.ml.anomaly.train.train", _recording_trainer("anomaly", calls, card=object())
    )

    results = await train_all_mod.train_all()

    assert calls == ["demand", "churn", "anomaly"]  # demand first, anomaly last
    assert results == {"demand": True, "churn": True, "anomaly": True}


async def test_none_card_marks_not_trained(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr("app.ml.demand.train.train", _recording_trainer("demand", calls, card=None))
    monkeypatch.setattr(
        "app.ml.churn.train.train", _recording_trainer("churn", calls, card=object())
    )
    monkeypatch.setattr(
        "app.ml.anomaly.train.train", _recording_trainer("anomaly", calls, card=object())
    )

    results = await train_all_mod.train_all()
    assert results["demand"] is False  # nothing to train on → not trained, no crash
    assert results["churn"] is True and results["anomaly"] is True


async def test_seed_first_seeds_then_trains(monkeypatch) -> None:
    calls: list[str] = []
    seeded: list[dict] = []

    async def _fake_seed(**kwargs):
        seeded.append(kwargs)

    monkeypatch.setattr("app.ml.seed_history.seed", _fake_seed)
    monkeypatch.setattr(
        "app.ml.demand.train.train", _recording_trainer("demand", calls, card=object())
    )
    monkeypatch.setattr(
        "app.ml.churn.train.train", _recording_trainer("churn", calls, card=object())
    )
    monkeypatch.setattr(
        "app.ml.anomaly.train.train", _recording_trainer("anomaly", calls, card=object())
    )

    await train_all_mod.train_all(seed_first=True, seed_kwargs={"n_tenants": 1, "months": 2})

    assert seeded == [{"n_tenants": 1, "months": 2}]  # seeded with the passed kwargs
    assert calls == ["demand", "churn", "anomaly"]  # and still trained, after seeding


async def test_no_seed_when_flag_false(monkeypatch) -> None:
    seeded: list[dict] = []

    async def _fake_seed(**kwargs):
        seeded.append(kwargs)

    monkeypatch.setattr("app.ml.seed_history.seed", _fake_seed)
    for name in ("demand", "churn", "anomaly"):
        monkeypatch.setattr(
            f"app.ml.{name}.train.train", _recording_trainer(name, [], card=object())
        )

    await train_all_mod.train_all(seed_first=False)
    assert seeded == []  # the seeder is never called without --seed-first
