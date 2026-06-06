"""Seasonal synthetic history generator (Phase 6, Task 6.2).

Phase 6 trains on MONTHS of realistic order/inventory/customer history with Lebanese
seasonality — and the dev DB has none. This script manufactures that history, honestly
labeled, so the demand/churn/anomaly pipelines have something real-shaped to learn.

What it plants (and what each model later harvests):
  - 3 ARCHETYPE tenants (bakery, mini-market, mountain shop), each `data_source=
    "synthetic"`, each with a product catalog, customers, and a year of daily orders.
  - SEASONALITY baked into daily demand via app/ml/seasonality.py (the SAME helpers the
    feature builder uses, so the planted signal is the learnable signal): Ramadan
    (category shifts + evening lift), summer-mountain lift, day-of-week curve, and a
    payday/month-end bump.
  - A CHURN signal: a minority of customers taper off and stop part-way through the
    window (the label source for Task 6.8); the majority keep returning (the imbalance
    is intentional and realistic).
  - INVENTORY rows per product so Phase 4/5 stock context exists for forecasting.

Properties that matter:
  - DETERMINISTIC: one `--seed` → identical history (reproducible training).
  - IDEMPOTENT: keyed by a stable synthetic whatsapp_number per archetype; re-running
    skips a tenant that already exists rather than duplicating it.
  - TENANT-SCOPED: every write goes through the tenant-scoped repositories — the Wall
    holds even in a seed script (constitution I).
  - HONEST: synthetic tenants are marked `data_source="synthetic"` so metrics trained
    on them can say so (the Task 6.2 honesty rule).

Run on the HOST (Docker DNS is blocked in-container; the DB is reachable on localhost):
    cd backend
    DATABASE_URL=postgresql+asyncpg://modir:modir@127.0.0.1:5432/modir \
    REDIS_URL=redis://127.0.0.1:6379/0 VAULT_ADDR=http://127.0.0.1:8200 \
    VAULT_TOKEN=root uv run python -m app.ml.seed_history --tenants 3 --months 12 --seed 42

Default window: Jun 2024 – May 2025 (12 months) — contains summer 2024 AND Ramadan
2025, so both seasonal signals have positive examples (Task 6.2 decision).
"""

import argparse
import asyncio
import random
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Customer, Inventory, Order, OrderItem, Product, Tenant
from app.db.session import create_engine
from app.infra.logging import get_logger
from app.ml import seasonality
from app.repositories.customers import CustomerRepository
from app.repositories.inventory import InventoryRepository
from app.repositories.orders import OrderItemRepository, OrderRepository
from app.repositories.products import ProductRepository
from app.repositories.tenants import TenantRepository

log = get_logger("seed_history")

BEIRUT_TZ = ZoneInfo("Asia/Beirut")
SYNTHETIC = "synthetic"

# The default training window end (exclusive day after the last seeded day). Anchored
# so a 12-month window is Jun 2024 – May 2025 (both summer + Ramadan present).
DEFAULT_WINDOW_END = date(2025, 6, 1)


# ── Archetypes ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ProductSpec:
    name_ar: str
    name_en: str
    category: str
    price_lbp: int
    unit: str
    # Mean units/day for this product on a NEUTRAL day before seasonality multipliers.
    base_daily_units: float
    # Per-archetype seasonal sensitivities (multipliers applied when the flag is on).
    ramadan_mult: float = 1.0
    summer_mult: float = 1.0


@dataclass(frozen=True)
class Archetype:
    key: str  # stable slug → drives the deterministic synthetic whatsapp_number
    name: str
    # Whole-shop seasonal tilt layered on top of per-product sensitivities.
    weekend_mult: float
    payday_mult: float
    products: tuple[ProductSpec, ...]


# Prices in LBP reflect post-devaluation Lebanese reality (tens/hundreds of thousands).
BAKERY = Archetype(
    key="bakery",
    name="فرن الحارة",  # "the neighborhood bakery"
    weekend_mult=1.25,
    payday_mult=1.10,
    products=(
        ProductSpec("كعك", "kaak", "bread", 50_000, "piece", 40, ramadan_mult=1.4),
        ProductSpec(
            "منقوشة زعتر", "manakish zaatar", "pastry", 75_000, "piece", 60, ramadan_mult=0.8
        ),
        ProductSpec("معمول", "maamoul", "sweets", 150_000, "box", 8, ramadan_mult=3.0),
        ProductSpec("خبز عربي", "arabic bread", "bread", 30_000, "bundle", 80, ramadan_mult=1.2),
        ProductSpec("قطايف", "qatayef", "sweets", 200_000, "box", 3, ramadan_mult=6.0),
    ),
)

MINI_MARKET = Archetype(
    key="mini_market",
    name="سوبرماركت الضيعة",  # "the village supermarket"
    weekend_mult=1.15,
    payday_mult=1.35,
    products=(
        ProductSpec("حليب", "milk", "dairy", 90_000, "liter", 30, ramadan_mult=1.3),
        ProductSpec("تمر", "dates", "pantry", 250_000, "kg", 5, ramadan_mult=4.0),
        ProductSpec("أرز", "rice", "pantry", 400_000, "kg", 12, ramadan_mult=1.5),
        ProductSpec("مياه", "water", "drinks", 40_000, "pack", 50, summer_mult=1.6),
        ProductSpec(
            "بوظة", "ice cream", "drinks", 120_000, "tub", 10, summer_mult=2.2, ramadan_mult=0.7
        ),
    ),
)

MOUNTAIN_SHOP = Archetype(
    key="mountain_shop",
    name="دكان الجبل",  # "the mountain store"
    weekend_mult=1.6,  # weekenders drive up
    payday_mult=1.05,
    products=(
        ProductSpec("قهوة", "coffee", "drinks", 180_000, "kg", 6, summer_mult=1.8),
        ProductSpec("فحم", "charcoal", "outdoor", 220_000, "bag", 4, summer_mult=3.5),
        ProductSpec("ترمس", "lupini beans", "snacks", 60_000, "cup", 15, summer_mult=2.0),
        ProductSpec("عرق", "arak", "drinks", 500_000, "bottle", 3, summer_mult=2.5),
        ProductSpec("جبنة بلدية", "village cheese", "dairy", 300_000, "kg", 7, summer_mult=1.4),
    ),
)

ARCHETYPES: tuple[Archetype, ...] = (BAKERY, MINI_MARKET, MOUNTAIN_SHOP)


# ── Demand shaping ───────────────────────────────────────────────────────────


def _is_mountain(arch: Archetype) -> bool:
    return arch.key == "mountain_shop"


def daily_units(rng: random.Random, arch: Archetype, spec: ProductSpec, day: date) -> int:
    """Expected units sold of one product on one day, after seasonality + noise.

    Combines: the product's base rate, day-of-week (weekend) tilt, payday bump,
    Ramadan sensitivity, and summer sensitivity (mountain shops LIFT in summer; the
    coastal bakery/market are neutral-to-down, expressed per product). Poisson-like
    integer noise around the mean keeps days from being identical.
    """
    mean = spec.base_daily_units

    if seasonality.is_weekend(day):
        mean *= arch.weekend_mult
    if seasonality.is_payday_window(day):
        mean *= arch.payday_mult
    if seasonality.is_ramadan(day):
        mean *= spec.ramadan_mult
    if seasonality.is_summer(day):
        # Mountain shops get the product's summer sensitivity; coastal archetypes feel
        # a mild summer slowdown overall (people leave the city).
        mean *= spec.summer_mult if _is_mountain(arch) else 0.9

    # Integer draw around the mean (gaussian, clamped at 0) — cheap stand-in for a
    # Poisson, deterministic via the seeded rng.
    draw = rng.gauss(mean, max(1.0, mean * 0.25))
    return max(0, round(draw))


# ── Customers ────────────────────────────────────────────────────────────────


@dataclass
class SeededCustomer:
    record: Customer
    # Fraction of the window this customer stays active (1.0 = never churns). A
    # churner's last_active_day is start + active_fraction * span.
    active_fraction: float
    last_active_day: date = field(default=date.min)


def _phone_for(arch_key: str, idx: int) -> str:
    """Stable synthetic phone per (archetype, customer index) — keeps re-runs idempotent
    and visibly fake (961-9XX range)."""
    return f"+9619{abs(hash(arch_key)) % 100:02d}{idx:05d}"


# ── Seeding one tenant ───────────────────────────────────────────────────────


@dataclass
class SeedStats:
    tenant_id: UUID
    archetype: str
    customers: int
    orders: int
    order_items: int


async def _seed_tenant(
    session: AsyncSession,
    arch: Archetype,
    *,
    start: date,
    end: date,
    n_customers: int,
    churn_fraction: float,
    rng: random.Random,
) -> SeedStats:
    """Create one synthetic tenant with a full window of seasonal history.

    All writes go through the tenant-scoped repositories. `created_at` is set
    explicitly to backdate every row (the server default would stamp 'now').
    """
    # Stable destination number per archetype → idempotency key.
    whatsapp = f"+961700{abs(hash(arch.key)) % 1000:03d}00"
    tenants = TenantRepository(session)
    existing = await tenants.get_by_whatsapp_number(whatsapp)
    if existing is not None:
        log.info("seed_history.tenant.exists", archetype=arch.key, tenant_id=str(existing.id))
        return SeedStats(existing.id, arch.key, 0, 0, 0)

    tenant = await tenants.add(
        Tenant(
            name=arch.name,
            whatsapp_number=whatsapp,
            plan_tier="free",
            is_active=True,
            data_source=SYNTHETIC,
        )
    )
    tenant_id = tenant.id

    # Catalog + inventory.
    products_repo = ProductRepository(session)
    inventory_repo = InventoryRepository(session)
    products: list[Product] = []
    for spec in arch.products:
        product = await products_repo.add(
            tenant_id,
            Product(
                name_ar=spec.name_ar,
                name_en=spec.name_en,
                category=spec.category,
                price_lbp=spec.price_lbp,
                unit=spec.unit,
                is_available=True,
            ),
        )
        products.append(product)
        # A healthy stock level + reorder settings so Phase 4/6 context exists.
        await inventory_repo.add(
            tenant_id,
            Inventory(
                product_id=product.id,
                quantity=int(spec.base_daily_units * 14),  # ~2 weeks cover
                reorder_threshold=int(spec.base_daily_units * 3),
                reorder_quantity=int(spec.base_daily_units * 10),
            ),
        )

    # Customers — most recurring, a churning minority.
    span_days = (end - start).days
    customers: list[SeededCustomer] = []
    for i in range(n_customers):
        churns = rng.random() < churn_fraction
        active_fraction = rng.uniform(0.2, 0.6) if churns else 1.0
        cust = await CustomerRepository(session).add(
            tenant_id,
            Customer(
                phone_number=_phone_for(arch.key, i),
                display_name=f"زبون {i + 1}",  # "customer N"
                first_seen_at=datetime.combine(start, time(9, 0), tzinfo=BEIRUT_TZ),
            ),
        )
        last_active = start + timedelta(days=int(span_days * active_fraction))
        customers.append(SeededCustomer(cust, active_fraction, last_active))

    # Daily orders across the window.
    orders_repo = OrderRepository(session)
    items_repo = OrderItemRepository(session)
    order_count = 0
    item_count = 0
    specs_by_product = dict(zip(products, arch.products, strict=True))

    day = start
    while day < end:
        # Which products sell today, and how many units.
        day_demand = {p: daily_units(rng, arch, spec, day) for p, spec in specs_by_product.items()}
        total_units = sum(day_demand.values())
        if total_units > 0:
            # Distribute the day's units across customers active on this day as a
            # handful of orders (not one giant order). Active = not yet churned.
            active = [c for c in customers if day <= c.last_active_day]
            if active:
                n_orders = max(1, min(len(active), round(total_units / 8)))
                day_orders, day_items = await _emit_day_orders(
                    session,
                    orders_repo,
                    items_repo,
                    tenant_id=tenant_id,
                    day=day,
                    day_demand=day_demand,
                    specs_by_product=specs_by_product,
                    active=active,
                    n_orders=n_orders,
                    rng=rng,
                )
                order_count += day_orders
                item_count += day_items
        day += timedelta(days=1)

    await session.commit()
    # Recount items cheaply for the stat line (one scoped query).
    stats = SeedStats(tenant_id, arch.key, len(customers), order_count, item_count)
    log.info(
        "seed_history.tenant.done",
        archetype=arch.key,
        tenant_id=str(tenant_id),
        customers=stats.customers,
        orders=stats.orders,
    )
    return stats


async def _emit_day_orders(
    session: AsyncSession,
    orders_repo: OrderRepository,
    items_repo: OrderItemRepository,
    *,
    tenant_id: UUID,
    day: date,
    day_demand: dict[Product, int],
    specs_by_product: dict[Product, ProductSpec],
    active: list[SeededCustomer],
    n_orders: int,
    rng: random.Random,
) -> tuple[int, int]:
    """Split a day's product demand into `n_orders` realistic baskets, backdated to
    that day. Returns (orders_written, items_written)."""
    # Remaining units to place per product.
    remaining = {p: q for p, q in day_demand.items() if q > 0}
    if not remaining:
        return 0, 0

    written = 0
    items_written = 0
    for o in range(n_orders):
        customer = rng.choice(active)
        # Spread the day's order times across opening hours for a natural curve.
        hour = rng.randint(8, 20)
        minute = rng.randint(0, 59)
        when = datetime.combine(day, time(hour, minute), tzinfo=BEIRUT_TZ)

        # Pick 1–3 products for this basket from those with remaining demand.
        choices = [p for p, q in remaining.items() if q > 0]
        if not choices:
            break
        basket = rng.sample(choices, k=min(len(choices), rng.randint(1, 3)))

        order = await orders_repo.add(
            tenant_id,
            Order(
                customer_id=customer.record.id,
                status="confirmed",
                fulfillment_type="pickup",
                created_at=when,
            ),
        )
        total_lbp = 0
        for product in basket:
            # Take a share of this product's remaining units (last order takes the rest).
            share = (
                remaining[product]
                if o == n_orders - 1
                else max(1, round(remaining[product] / (n_orders - o)))
            )
            qty = min(share, remaining[product])
            if qty <= 0:
                continue
            remaining[product] -= qty
            spec = specs_by_product[product]
            line_total = spec.price_lbp * qty
            total_lbp += line_total
            await items_repo.add(
                tenant_id,
                OrderItem(
                    order_id=order.id,
                    product_id=product.id,
                    name_ar_snapshot=product.name_ar,
                    quantity=qty,
                    unit_price_lbp=spec.price_lbp,
                    line_total_lbp=line_total,
                    created_at=when,
                ),
            )
            items_written += 1
        order.total_lbp = total_lbp
        order.total_usd = Decimal(total_lbp) / Decimal(89_000)  # rough LBP→USD peg
        written += 1
    return written, items_written


# ── Entrypoint ───────────────────────────────────────────────────────────────


async def seed(
    *, n_tenants: int, months: int, seed_value: int, n_customers: int, churn_fraction: float
) -> list[SeedStats]:
    """Seed `n_tenants` archetype tenants with `months` of history. Deterministic in
    `seed_value`; idempotent per archetype. Returns per-tenant stats."""
    rng = random.Random(seed_value)
    end = DEFAULT_WINDOW_END
    start = end - timedelta(days=int(months * 30.4))
    log.info(
        "seed_history.start",
        tenants=n_tenants,
        months=months,
        start=start.isoformat(),
        end=end.isoformat(),
        seed=seed_value,
    )

    engine = create_engine()
    all_stats: list[SeedStats] = []
    try:
        for arch in ARCHETYPES[:n_tenants]:
            async with AsyncSession(engine, expire_on_commit=False) as session:
                stats = await _seed_tenant(
                    session,
                    arch,
                    start=start,
                    end=end,
                    n_customers=n_customers,
                    churn_fraction=churn_fraction,
                    rng=rng,
                )
                all_stats.append(stats)
    finally:
        await engine.dispose()

    total_orders = sum(s.orders for s in all_stats)
    log.info("seed_history.done", tenants=len(all_stats), total_orders=total_orders)
    return all_stats


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Seed seasonal synthetic shop history (Phase 6).")
    p.add_argument("--tenants", type=int, default=3, help="archetype tenants to seed (max 3)")
    p.add_argument("--months", type=int, default=12, help="months of history (default 12)")
    p.add_argument("--seed", type=int, default=42, help="RNG seed (deterministic output)")
    p.add_argument("--customers", type=int, default=60, help="customers per tenant")
    p.add_argument(
        "--churn-fraction",
        type=float,
        default=0.25,
        help="fraction of customers that taper off and stop (the churn label source)",
    )
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    asyncio.run(
        seed(
            n_tenants=args.tenants,
            months=args.months,
            seed_value=args.seed,
            n_customers=args.customers,
            churn_fraction=args.churn_fraction,
        )
    )


if __name__ == "__main__":
    main()
