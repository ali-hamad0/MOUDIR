"""Demo seed script — populates the stack with a Lebanese bakery demo tenant.

Idempotent: detects the demo tenant by its WhatsApp number and skips creation
if it already exists, updating only products and policies.  Running it twice
creates no duplicate records.

Run inside the api container (recommended — env vars already set):
    docker compose exec api python -m scripts.seed_demo

Or on the host with overridden hostnames:
    cd backend
    DATABASE_URL=postgresql+asyncpg://modir:modir@127.0.0.1:5432/modir \\
    REDIS_URL=redis://127.0.0.1:6379/0 VAULT_ADDR=http://127.0.0.1:8200 \\
    VAULT_TOKEN=root uv run python -m scripts.seed_demo

After running, follow docs/DEMO_SCRIPT.md for the full 5-minute walkthrough.
"""

import asyncio
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from random import Random

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    AgentRun,
    BusinessPolicy,
    BusinessProfile,
    Customer,
    OperatingHours,
    Order,
    OrderItem,
    Product,
    Tenant,
    TenantOwner,
    User,
)
from app.db.session import create_engine
from app.infra.logging import get_logger
from app.infra.security import hash_password

log = get_logger("seed_demo")

# ── Demo constants ───────────────────────────────────────────────────────────

DEMO_WHATSAPP = "+96170000001"
DEMO_OWNER_PHONE = "+96170000002"
DEMO_EMAIL = "demo@modir.test"
DEMO_PASSWORD = "DemoPassword1"  # DEV ONLY — never use in production

_PRODUCTS = [
    dict(
        name_ar="كعك بالسمسم",
        name_en="Sesame Ka'ak",
        price_lbp=15_000,
        unit="حبة",
        category="مخبوزات",
    ),
    dict(
        name_ar="بقلاوة بالفستق",
        name_en="Pistachio Baklawa",
        price_lbp=120_000,
        unit="كيلو",
        category="حلويات",
    ),
    dict(
        name_ar="معروك رمضان",
        name_en="Ramadan Maarouk",
        price_lbp=20_000,
        unit="حبة",
        category="مخبوزات",
    ),
    dict(name_ar="كنافة", name_en="Knafeh", price_lbp=90_000, unit="كيلو", category="حلويات"),
    dict(name_ar="خبز صج", name_en="Saj Bread", price_lbp=5_000, unit="ربطة", category="خبز"),
    dict(
        name_ar="تشريبة عيش", name_en="Aish Tashreeba", price_lbp=3_000, unit="حبة", category="خبز"
    ),
    dict(name_ar="بزورة", name_en="Mixed Nuts", price_lbp=50_000, unit="كيلو", category="مكسرات"),
    dict(
        name_ar="قريبة بالقشطة",
        name_en="Ghraybeh with Cream",
        price_lbp=8_000,
        unit="حبة",
        category="حلويات",
    ),
]

_CUSTOMERS = [
    dict(phone="+96171111001", display_name="أم خالد"),
    dict(phone="+96171111002", display_name="أبو جورج"),
    dict(phone="+96171111003", display_name="رنا الحسن"),
    dict(phone="+96171111004", display_name="طوني نصر"),
    dict(phone="+96171111005", display_name="سمر عبدالله"),
]

_POLICIES = {
    "min_order_lbp": "50000",
    "delivery_fee_lbp": "10000",
    "payment_methods": "كاش، OMT، بنك",
    "rate_limit_rpm": "30",
    "daily_llm_budget_usd": "5.00",
    "alert_webhook_url": "",
}

_AGENTS = ["order", "inventory", "finance", "customer", "advisor"]


# ── Helpers ──────────────────────────────────────────────────────────────────


async def _get_or_skip(session: AsyncSession, model, **where):
    """Return the first matching row or None; avoids IntegrityError on re-runs."""
    stmt = select(model)
    for col, val in where.items():
        stmt = stmt.where(getattr(model, col) == val)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def _any_rows(session: AsyncSession, model, tenant_id) -> bool:
    """True if at least one row with this tenant_id already exists."""
    result = await session.execute(select(model).where(model.tenant_id == tenant_id).limit(1))
    return result.scalar_one_or_none() is not None


# ── Main seeder ──────────────────────────────────────────────────────────────


async def seed() -> None:
    engine = create_engine()
    rng = Random(42)  # fixed seed → deterministic data shape on every re-run
    now = datetime.now(tz=UTC)

    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:

            # ── 1. Tenant ────────────────────────────────────────────────────
            tenant = await _get_or_skip(session, Tenant, whatsapp_number=DEMO_WHATSAPP)
            if tenant is None:
                tenant = Tenant(
                    name="مخبز أبو خالد",
                    whatsapp_number=DEMO_WHATSAPP,
                    plan_tier="free",
                    is_active=True,
                )
                session.add(tenant)
                await session.flush()
                log.info("seed_demo.tenant_created", name=tenant.name, tenant_id=str(tenant.id))
            else:
                log.info(
                    "seed_demo.tenant_exists",
                    name=tenant.name,
                    tenant_id=str(tenant.id),
                    note="updating products and policies only",
                )

            tid = tenant.id

            # ── 2. Owner phone ───────────────────────────────────────────────
            if (
                await _get_or_skip(
                    session, TenantOwner, tenant_id=tid, phone_number=DEMO_OWNER_PHONE
                )
                is None
            ):
                session.add(
                    TenantOwner(
                        tenant_id=tid,
                        phone_number=DEMO_OWNER_PHONE,
                        name="أبو خالد",
                        verified_at=now,
                        verification_status="verified",
                    )
                )

            # ── 3. Dashboard user ────────────────────────────────────────────
            if await _get_or_skip(session, User, tenant_id=tid, email=DEMO_EMAIL) is None:
                session.add(
                    User(
                        tenant_id=tid,
                        email=DEMO_EMAIL,
                        hashed_password=hash_password(DEMO_PASSWORD),
                        role="owner",
                        activated_at=now,
                    )
                )

            # ── 4. Business profile ──────────────────────────────────────────
            if await _get_or_skip(session, BusinessProfile, tenant_id=tid) is None:
                session.add(
                    BusinessProfile(
                        tenant_id=tid,
                        business_name="مخبز أبو خالد",
                        description="أفضل كعك وبقلاوة في بيروت",
                        location="الحمرا، بيروت",
                        delivery_radius_km=5,
                        accepts_delivery=True,
                        accepts_pickup=True,
                    )
                )

            await session.flush()

            # ── 5. Products (upsert by name_ar) ─────────────────────────────
            # Keep {product_id: product_dict} for later use in order seeding.
            products_by_id: dict = {}
            for p in _PRODUCTS:
                existing = await _get_or_skip(session, Product, tenant_id=tid, name_ar=p["name_ar"])
                if existing is None:
                    prod = Product(tenant_id=tid, is_available=True, **p)
                    session.add(prod)
                    await session.flush()
                    products_by_id[prod.id] = p
                else:
                    products_by_id[existing.id] = p

            # ── 6. Operating hours (Mon–Sat open, Sun closed) ────────────────
            for dow in range(7):
                if (
                    await _get_or_skip(session, OperatingHours, tenant_id=tid, day_of_week=dow)
                    is None
                ):
                    session.add(
                        OperatingHours(
                            tenant_id=tid,
                            day_of_week=dow,
                            open_time=time(7, 0) if dow < 6 else None,
                            close_time=time(21, 0) if dow < 6 else None,
                            is_closed=(dow == 6),
                            note_ar=(
                                "مغلق يوم الأحد"
                                if dow == 6
                                else "رمضان: بعد الإفطار حتى منتصف الليل"
                            ),
                        )
                    )

            # ── 7. Policies (upsert by key) ──────────────────────────────────
            for key, value in _POLICIES.items():
                if await _get_or_skip(session, BusinessPolicy, tenant_id=tid, key=key) is None:
                    session.add(BusinessPolicy(tenant_id=tid, key=key, value=value))

            await session.flush()

            # ── 8. Customers ─────────────────────────────────────────────────
            customer_ids: list = []
            for i, c in enumerate(_CUSTOMERS):
                existing = await _get_or_skip(
                    session, Customer, tenant_id=tid, phone_number=c["phone"]
                )
                if existing is None:
                    cust = Customer(
                        tenant_id=tid,
                        phone_number=c["phone"],
                        display_name=c["display_name"],
                        first_seen_at=now - timedelta(days=30 - i * 5),
                    )
                    session.add(cust)
                    await session.flush()
                    customer_ids.append(cust.id)
                else:
                    customer_ids.append(existing.id)

            # ── 9. Orders (20, spread over 7 days) — skip if any exist ───────
            if not await _any_rows(session, Order, tid):
                statuses = ["confirmed", "preparing", "ready", "delivered"]
                sources = ["agent", "manual"]
                product_id_list = list(products_by_id.keys())

                for i in range(20):
                    cust_id = customer_ids[i % len(customer_ids)]
                    chosen_ids = rng.sample(product_id_list, k=rng.randint(1, 3))
                    line_items = [
                        {
                            "product_id": pid,
                            "name_ar": products_by_id[pid]["name_ar"],
                            "quantity": rng.randint(1, 5),
                            "unit_price_lbp": products_by_id[pid]["price_lbp"],
                        }
                        for pid in chosen_ids
                    ]
                    total_lbp = sum(it["quantity"] * it["unit_price_lbp"] for it in line_items)
                    order_time = now - timedelta(days=rng.randint(0, 6), hours=rng.randint(0, 23))
                    order = Order(
                        tenant_id=tid,
                        customer_id=cust_id,
                        status=statuses[i % len(statuses)],
                        fulfillment_type="delivery" if i % 3 == 0 else "pickup",
                        total_lbp=total_lbp,
                        source=sources[i % len(sources)],
                        created_at=order_time,
                        updated_at=order_time,
                    )
                    session.add(order)
                    await session.flush()

                    for it in line_items:
                        session.add(
                            OrderItem(
                                tenant_id=tid,
                                order_id=order.id,
                                product_id=it["product_id"],
                                name_ar_snapshot=it["name_ar"],
                                quantity=it["quantity"],
                                unit_price_lbp=it["unit_price_lbp"],
                                line_total_lbp=it["quantity"] * it["unit_price_lbp"],
                            )
                        )

            # ── 10. AgentRun rows (30 days of cost data) — skip if any exist ─
            if not await _any_rows(session, AgentRun, tid):
                for day in range(30):
                    for _ in range(rng.randint(3, 8)):
                        agent = rng.choice(_AGENTS)
                        prompt_tok = rng.randint(300, 1200)
                        comp_tok = rng.randint(80, 400)
                        cost = Decimal(str(round(prompt_tok * 0.000001 + comp_tok * 0.000003, 6)))
                        run_time = now - timedelta(days=day, hours=rng.randint(0, 23))
                        session.add(
                            AgentRun(
                                tenant_id=tid,
                                agent_name=agent,
                                model_name="gemini-2.5-flash",
                                prompt_tokens=prompt_tok,
                                completion_tokens=comp_tok,
                                cost_usd=cost,
                                created_at=run_time,
                                updated_at=run_time,
                            )
                        )

            await session.commit()

    finally:
        await engine.dispose()

    print("\nDemo seed complete.")
    print(f"  Tenant:    مخبز أبو خالد  (WhatsApp: {DEMO_WHATSAPP})")
    print(f"  Dashboard: {DEMO_EMAIL} / {DEMO_PASSWORD}")
    print(f"  Owner WA:  {DEMO_OWNER_PHONE}")
    print("\nNext: follow docs/DEMO_SCRIPT.md for the full 5-minute walkthrough.")


if __name__ == "__main__":
    asyncio.run(seed())
