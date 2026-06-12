"""Demo seed script — populates the stack with a Lebanese bakery demo tenant.

Clears and reseeds all transactional data (orders, inventory, suppliers,
agent runs) on every run so the demo always reflects the intended data shape.
Idempotent on structural records (tenant, user, products, customers, policies).

After running, the following AI features are demonstrable:
  - Advisor morning briefing  → today has 3 real orders + revenue
  - Inventory low-stock alert → 4 products below reorder threshold
  - Finance revenue analysis  → 60 days of varied daily revenue
  - Customer churn list       → 3 customers with last order 35-55 days ago

Run inside the api container (recommended — env vars already set):
    docker compose exec api python -m scripts.seed_demo

Or on the host with overridden hostnames:
    cd backend
    DATABASE_URL=postgresql+asyncpg://modir:modir@127.0.0.1:5432/modir \\
    REDIS_URL=redis://127.0.0.1:6379/0 VAULT_ADDR=http://127.0.0.1:8200 \\
    VAULT_TOKEN=root uv run python -m scripts.seed_demo
"""

import asyncio
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from random import Random

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    AgentRun,
    BusinessPolicy,
    BusinessProfile,
    Customer,
    Inventory,
    OperatingHours,
    Order,
    OrderEvent,
    OrderItem,
    Product,
    PurchaseOrder,
    PurchaseOrderEvent,
    Supplier,
    SupplierBill,
    SupplierBillEvent,
    SupplierBillLine,
    Tenant,
    TenantOwner,
    User,
)
from app.db.session import create_engine
from app.infra.logging import get_logger
from app.infra.security import hash_password

log = get_logger("seed_demo")

# ── Demo constants ───────────────────────────────────────────────────────────

DEMO_WHATSAPP = "+15556544382"
DEMO_OWNER_PHONE = "+96176860456"
DEMO_EMAIL = "demo@modir.app"
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

# name_ar → (quantity, reorder_threshold, reorder_quantity, supplier_key)
# Items where quantity <= reorder_threshold show up as low-stock.
_INVENTORY = {
    "كعك بالسمسم": (6, 20, 50, "bread"),  # LOW ⚠️  6 ≤ 20
    "بقلاوة بالفستق": (15, 8, 20, "sweets"),  # OK     15 > 8
    "معروك رمضان": (3, 10, 30, "bread"),  # LOW ⚠️  3 ≤ 10
    "كنافة": (4, 12, 20, "sweets"),  # LOW ⚠️  4 ≤ 12
    "خبز صج": (5, 30, 100, "bread"),  # LOW ⚠️  5 ≤ 30
    "تشريبة عيش": (60, 20, 80, "bread"),  # OK     60 > 20
    "بزورة": (18, 10, 25, "sweets"),  # OK     18 > 10
    "قريبة بالقشطة": (25, 10, 40, "sweets"),  # OK     25 > 10
}

_SUPPLIERS = {
    "bread": dict(name="مطاحن الجنوب", contact_email="orders@matahen-janoub.lb"),
    "sweets": dict(name="شركة الشام للحلويات", contact_email="orders@sham-sweets.lb"),
}

# last_order_days_ago: how many days ago was this customer's MOST RECENT order.
# Customers 3-5 are intentionally "at risk" (no order in 35-55 days).
_CUSTOMERS = [
    dict(phone="+96171111001", display_name="أم خالد", last_order_days_ago=0),
    dict(phone="+96171111002", display_name="أبو جورج", last_order_days_ago=3),
    dict(phone="+96171111003", display_name="رنا الحسن", last_order_days_ago=8),
    dict(phone="+96171111004", display_name="طوني نصر", last_order_days_ago=35),  # AT RISK
    dict(phone="+96171111005", display_name="سمر عبدالله", last_order_days_ago=42),  # AT RISK
    dict(phone="+96171111006", display_name="نادين فرحات", last_order_days_ago=55),  # AT RISK
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

# Products ordered more often (appear 3× in the weighted draw pool).
_POPULAR = {"خبز صج", "كعك بالسمسم", "كنافة"}


# ── Helpers ──────────────────────────────────────────────────────────────────


async def _get_or_skip(session: AsyncSession, model, **where):
    stmt = select(model)
    for col, val in where.items():
        stmt = stmt.where(getattr(model, col) == val)
    return (await session.execute(stmt)).scalar_one_or_none()


# ── Main seeder ──────────────────────────────────────────────────────────────


async def seed() -> None:
    engine = create_engine()
    rng = Random(42)
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
                log.info("seed_demo.tenant_created", name=tenant.name)
            else:
                log.info(
                    "seed_demo.tenant_exists", name=tenant.name, note="reseeding transactional data"
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
            products_by_name: dict[str, Product] = {}
            for p in _PRODUCTS:
                existing = await _get_or_skip(session, Product, tenant_id=tid, name_ar=p["name_ar"])
                if existing is None:
                    prod = Product(tenant_id=tid, is_available=True, **p)
                    session.add(prod)
                    await session.flush()
                    products_by_name[p["name_ar"]] = prod
                else:
                    products_by_name[p["name_ar"]] = existing

            # ── 6. Operating hours ───────────────────────────────────────────
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
                            note_ar="مغلق يوم الأحد" if dow == 6 else "من 7 صباحاً حتى 9 مساءً",
                        )
                    )

            # ── 7. Policies ──────────────────────────────────────────────────
            for key, value in _POLICIES.items():
                if await _get_or_skip(session, BusinessPolicy, tenant_id=tid, key=key) is None:
                    session.add(BusinessPolicy(tenant_id=tid, key=key, value=value))

            await session.flush()

            # ── 8. Customers (upsert) ────────────────────────────────────────
            customer_ids: list = []
            for c in _CUSTOMERS:
                existing = await _get_or_skip(
                    session, Customer, tenant_id=tid, phone_number=c["phone"]
                )
                if existing is None:
                    cust = Customer(
                        tenant_id=tid,
                        phone_number=c["phone"],
                        display_name=c["display_name"],
                        first_seen_at=now - timedelta(days=60),
                    )
                    session.add(cust)
                    await session.flush()
                    customer_ids.append(cust.id)
                else:
                    customer_ids.append(existing.id)

            # ── CLEAR transactional data — always reseed ─────────────────────
            # FK-safe order: event/line children first, then their parents:
            # PO events → POs → order events → order items → orders →
            # bill events → bill lines → bills → inventory → suppliers → agent runs
            await session.execute(
                delete(PurchaseOrderEvent).where(PurchaseOrderEvent.tenant_id == tid)
            )
            await session.execute(delete(PurchaseOrder).where(PurchaseOrder.tenant_id == tid))
            await session.execute(delete(OrderEvent).where(OrderEvent.tenant_id == tid))
            await session.execute(delete(OrderItem).where(OrderItem.tenant_id == tid))
            await session.execute(delete(Order).where(Order.tenant_id == tid))
            await session.execute(
                delete(SupplierBillEvent).where(SupplierBillEvent.tenant_id == tid)
            )
            await session.execute(delete(SupplierBillLine).where(SupplierBillLine.tenant_id == tid))
            await session.execute(delete(SupplierBill).where(SupplierBill.tenant_id == tid))
            await session.execute(delete(Inventory).where(Inventory.tenant_id == tid))
            await session.execute(delete(Supplier).where(Supplier.tenant_id == tid))
            await session.execute(delete(AgentRun).where(AgentRun.tenant_id == tid))
            await session.flush()
            log.info("seed_demo.transactional_data_cleared", tenant_id=str(tid))

            # ── 9. Suppliers ─────────────────────────────────────────────────
            supplier_by_key: dict[str, Supplier] = {}
            for key, s in _SUPPLIERS.items():
                sup = Supplier(
                    tenant_id=tid,
                    name=s["name"],
                    dispatch_type="webhook",
                    contact_email=s["contact_email"],
                    is_active=True,
                )
                session.add(sup)
                await session.flush()
                supplier_by_key[key] = sup
            log.info("seed_demo.suppliers_created", count=len(supplier_by_key))

            # ── 10. Inventory (with 4 items below reorder threshold) ─────────
            for name_ar, (qty, threshold, reorder_qty, sup_key) in _INVENTORY.items():
                prod = products_by_name.get(name_ar)
                if prod is None:
                    continue
                session.add(
                    Inventory(
                        tenant_id=tid,
                        product_id=prod.id,
                        quantity=qty,
                        reorder_threshold=threshold,
                        reorder_quantity=reorder_qty,
                        supplier_id=supplier_by_key[sup_key].id,
                    )
                )
            await session.flush()
            low_stock_count = sum(1 for (q, t, *_) in _INVENTORY.values() if q <= t)
            log.info(
                "seed_demo.inventory_created", total=len(_INVENTORY), low_stock=low_stock_count
            )

            # ── 11. Orders — 60 days of history ──────────────────────────────
            # Strategy:
            #   - Customer i is eligible at days_ago D iff D >= their last_order_days_ago.
            #     This makes their most-recent order fall on exactly their last_order_days_ago.
            #   - On a customer's exact last_order_days_ago day they are "guaranteed" an order.
            #   - Day 45 ago: revenue spike (catering event) — 12 orders.
            #   - Day 10 ago: low-revenue day (partial closure) — 1 order.
            #   - Today (days_ago=0): always at least 3 orders.

            last_order_days = [c["last_order_days_ago"] for c in _CUSTOMERS]

            # Build weighted product pool (popular items appear 3×)
            weighted_pool = []
            for name_ar, prod in products_by_name.items():
                weight = 3 if name_ar in _POPULAR else 1
                weighted_pool.extend([(name_ar, prod)] * weight)

            total_orders = 0
            for days_ago in range(60, -1, -1):
                # Base order count for this day
                order_dt = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(
                    days=days_ago
                )
                weekday = order_dt.weekday()  # 0=Mon, 4=Fri, 5=Sat

                if days_ago == 45:
                    n_orders = 12  # catering spike
                elif days_ago == 10:
                    n_orders = 1  # low day
                elif days_ago == 0:
                    n_orders = 3  # today — guaranteed visible on dashboard
                elif weekday in (4, 5):
                    n_orders = rng.randint(5, 8)
                else:
                    n_orders = rng.randint(2, 4)

                # Eligible customers (days_ago >= their last order day)
                eligible = [i for i in range(len(customer_ids)) if days_ago >= last_order_days[i]]
                if not eligible:
                    eligible = [0]

                # Customers that MUST get their last-ever order on this exact day
                guaranteed = [i for i in range(len(customer_ids)) if last_order_days[i] == days_ago]

                for order_idx in range(n_orders):
                    if order_idx < len(guaranteed):
                        cust_idx = guaranteed[order_idx]
                    else:
                        cust_idx = rng.choice(eligible)

                    # Pick 1-3 products from weighted pool (deduplicated)
                    n_items = rng.randint(1, 3)
                    chosen_raw = rng.sample(weighted_pool, k=min(n_items * 2, len(weighted_pool)))
                    seen_ids: set = set()
                    line_items = []
                    for name_ar, prod in chosen_raw:
                        if prod.id in seen_ids or len(line_items) >= n_items:
                            continue
                        seen_ids.add(prod.id)
                        qty = rng.randint(1, 5) if name_ar in _POPULAR else rng.randint(1, 3)
                        line_items.append(
                            {
                                "product_id": prod.id,
                                "name_ar": name_ar,
                                "quantity": qty,
                                "unit_price_lbp": prod.price_lbp,
                            }
                        )
                    if not line_items:
                        continue

                    total_lbp = sum(it["quantity"] * it["unit_price_lbp"] for it in line_items)

                    # Today's orders at morning hours (9-11am UTC = noon-2pm Beirut).
                    # Other days: spread through business hours.
                    if days_ago == 0:
                        hour = 9 + order_idx  # 9, 10, 11 am UTC
                    else:
                        hour = rng.randint(8, 20)
                    order_time = order_dt.replace(hour=hour, minute=rng.randint(0, 59))

                    order = Order(
                        tenant_id=tid,
                        customer_id=customer_ids[cust_idx],
                        status="delivered",
                        fulfillment_type="delivery" if order_idx % 3 == 0 else "pickup",
                        total_lbp=total_lbp,
                        source="agent",
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
                    total_orders += 1

            log.info("seed_demo.orders_created", total=total_orders, days=61)

            # ── 12. AgentRun rows (30 days of cost history) ──────────────────
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

    print("\n✅ Demo seed complete.")
    print(f"   Tenant:     مخبز أبو خالد  (WhatsApp: {DEMO_WHATSAPP})")
    print(f"   Dashboard:  {DEMO_EMAIL} / {DEMO_PASSWORD}")
    print(f"   Owner WA:   {DEMO_OWNER_PHONE}")
    print()
    print("📦 Inventory — 4 products below reorder threshold:")
    for name, (qty, threshold, *_) in _INVENTORY.items():
        status = "⚠️  LOW" if qty <= threshold else "✅ OK "
        print(f"   {status}  {name}: {qty} (threshold {threshold})")
    print()
    print("👥 Customers — 3 at churn risk (no order in 35-55 days):")
    for c in _CUSTOMERS:
        risk = "🔴 AT RISK" if c["last_order_days_ago"] >= 35 else "🟢 active "
        print(f"   {risk}  {c['display_name']} — last order {c['last_order_days_ago']} days ago")
    print()
    print("💬 Try these prompts in the owner chat:")
    print("   كيف يومي؟")
    print("   شو ناقص من المخزون؟")
    print("   كيف مبيعاتي هالأسبوع؟")
    print("   مين زبائن ما رجعوا؟")


if __name__ == "__main__":
    asyncio.run(seed())
