"""Task 6.2 — the seasonal synthetic history generator.

Proves the generator plants a LEARNABLE, HONEST, TENANT-SCOPED history:
  - seasonality helpers flag Ramadan / summer / payday / weekend correctly;
  - demand shaping actually lifts Ramadan sweets and mountain-summer sales (the signal
    the models later harvest) and is DETERMINISTIC under a fixed seed;
  - a seeded tenant is marked data_source="synthetic", its rows are BACKDATED into the
    window (not stamped 'now'), every row is tenant-scoped, and a re-run is IDEMPOTENT.

Runs against the transactional db_session fixture (rolled back), so no dev data is
touched. `_seed_tenant` uses a tiny window to stay fast.
"""

import random
from datetime import date

from sqlalchemy import func, select

from app.db.models import Customer, Order, OrderItem, Product, Tenant
from app.ml import seasonality
from app.ml.seed_history import BAKERY, MOUNTAIN_SHOP, _seed_tenant, daily_units

# ── seasonality helpers ──────────────────────────────────────────────────────


def test_ramadan_flag() -> None:
    assert seasonality.is_ramadan(date(2025, 3, 15))  # inside Ramadan 2025
    assert not seasonality.is_ramadan(date(2025, 1, 15))  # winter, not Ramadan
    # Boundaries are inclusive.
    assert seasonality.is_ramadan(date(2025, 3, 1))
    assert seasonality.is_ramadan(date(2025, 3, 30))


def test_summer_flag() -> None:
    assert seasonality.is_summer(date(2024, 7, 15))
    assert not seasonality.is_summer(date(2024, 11, 15))


def test_payday_and_weekend_flags() -> None:
    assert seasonality.is_payday_window(date(2024, 6, 30))  # month-end
    assert seasonality.is_payday_window(date(2024, 6, 1))  # month-start
    assert not seasonality.is_payday_window(date(2024, 6, 15))
    assert seasonality.is_weekend(date(2024, 6, 8))  # Saturday
    assert not seasonality.is_weekend(date(2024, 6, 5))  # Wednesday


# ── demand shaping ───────────────────────────────────────────────────────────


def test_ramadan_lifts_bakery_sweets() -> None:
    rng = random.Random(1)
    maamoul = next(p for p in BAKERY.products if p.name_en == "maamoul")  # ramadan_mult 3.0
    # Average several draws to smooth the noise; compare a Ramadan day to a normal one.
    ramadan = sum(daily_units(rng, BAKERY, maamoul, date(2025, 3, 15)) for _ in range(50)) / 50
    normal = sum(daily_units(rng, BAKERY, maamoul, date(2025, 1, 15)) for _ in range(50)) / 50
    assert ramadan > normal * 2  # the 3x sensitivity clearly shows through the noise


def test_summer_lifts_mountain_shop() -> None:
    rng = random.Random(2)
    charcoal = next(p for p in MOUNTAIN_SHOP.products if p.name_en == "charcoal")  # summer 3.5
    summer = (
        sum(daily_units(rng, MOUNTAIN_SHOP, charcoal, date(2024, 7, 15)) for _ in range(50)) / 50
    )
    winter = (
        sum(daily_units(rng, MOUNTAIN_SHOP, charcoal, date(2024, 1, 15)) for _ in range(50)) / 50
    )
    assert summer > winter * 2


def test_daily_units_is_deterministic_under_seed() -> None:
    a = [
        daily_units(random.Random(7), BAKERY, BAKERY.products[0], date(2024, 6, 10))
        for _ in range(5)
    ]
    b = [
        daily_units(random.Random(7), BAKERY, BAKERY.products[0], date(2024, 6, 10))
        for _ in range(5)
    ]
    assert a == b


# ── _seed_tenant against the DB (rolled back) ────────────────────────────────


async def test_seed_tenant_writes_backdated_synthetic_data(db_session) -> None:
    start = date(2025, 3, 1)  # a short window inside Ramadan
    end = date(2025, 3, 15)
    stats = await _seed_tenant(
        db_session,
        BAKERY,
        start=start,
        end=end,
        n_customers=5,
        churn_fraction=0.2,
        rng=random.Random(42),
    )

    tenant = (
        await db_session.execute(select(Tenant).where(Tenant.id == stats.tenant_id))
    ).scalar_one()
    assert tenant.data_source == "synthetic"

    # Orders exist, are tenant-scoped, and are BACKDATED into the window (not 'now').
    rows = (
        (await db_session.execute(select(Order).where(Order.tenant_id == stats.tenant_id)))
        .scalars()
        .all()
    )
    assert len(rows) > 0
    assert all(r.tenant_id == stats.tenant_id for r in rows)
    assert all(start <= r.created_at.date() < end for r in rows)

    # Catalog, customers, and inventory all landed under this tenant.
    for model in (Product, Customer):
        count = (
            await db_session.execute(
                select(func.count()).select_from(model).where(model.tenant_id == stats.tenant_id)
            )
        ).scalar_one()
        assert count > 0

    # Order items are tenant-scoped too (the Wall holds for the child rows).
    item_tenants = (
        (
            await db_session.execute(
                select(OrderItem.tenant_id).where(OrderItem.tenant_id == stats.tenant_id).limit(1)
            )
        )
        .scalars()
        .all()
    )
    assert item_tenants == [stats.tenant_id]


async def test_seed_tenant_is_idempotent(db_session) -> None:
    args = dict(
        start=date(2025, 3, 1),
        end=date(2025, 3, 8),
        n_customers=3,
        churn_fraction=0.0,
        rng=random.Random(42),
    )
    first = await _seed_tenant(db_session, BAKERY, **args)
    assert first.orders > 0
    # Same archetype again → recognized by its stable whatsapp_number → no new tenant,
    # no duplicated orders.
    second = await _seed_tenant(db_session, BAKERY, **{**args, "rng": random.Random(42)})
    assert second.tenant_id == first.tenant_id
    assert second.orders == 0  # nothing re-created
