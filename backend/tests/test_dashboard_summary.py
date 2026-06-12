"""Phase 10 — dashboard summary endpoint (home-page charts).

Proves the aggregates are correct AND tenant-scoped: daily revenue/order
trends count only this tenant's orders, gap days are zero-filled so the chart
axis is continuous, top products rank by units, and inventory health reflects
this tenant's catalog only (the Wall).
"""

from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dashboard import dashboard_summary
from app.db.models import Customer, Inventory, Order, OrderItem, User
from app.repositories.orders import BEIRUT_TZ
from app.repositories.users import UserRepository
from tests.conftest import TwoTenants


async def _user(db: AsyncSession, tenant_id, email) -> User:
    user = await UserRepository(db).get_by_email(tenant_id, email)
    assert user is not None
    return user


async def _seed_orders(db: AsyncSession, tenant_id, product_id):
    customer = Customer(tenant_id=tenant_id, phone_number=f"+961DASH{str(tenant_id)[:6]}")
    db.add(customer)
    await db.flush()

    now = datetime.now(BEIRUT_TZ)
    # Two orders today (5000 + 7000) and one three days ago (2000).
    rows = [
        Order(tenant_id=tenant_id, customer_id=customer.id, total_lbp=5000, created_at=now),
        Order(tenant_id=tenant_id, customer_id=customer.id, total_lbp=7000, created_at=now),
        Order(
            tenant_id=tenant_id,
            customer_id=customer.id,
            total_lbp=2000,
            created_at=now - timedelta(days=3),
        ),
    ]
    db.add_all(rows)
    await db.flush()
    db.add_all(
        [
            OrderItem(
                tenant_id=tenant_id,
                order_id=rows[0].id,
                product_id=product_id,
                name_ar_snapshot="كعك",
                quantity=3,
            ),
            OrderItem(
                tenant_id=tenant_id,
                order_id=rows[1].id,
                product_id=product_id,
                name_ar_snapshot="كعك",
                quantity=2,
            ),
        ]
    )
    await db.flush()


async def test_summary_aggregates_and_gap_fill(db_session: AsyncSession, two_tenants: TwoTenants):
    a = two_tenants.a
    user = await _user(db_session, a.tenant_id, a.user_email)
    await _seed_orders(db_session, a.tenant_id, a.product_ids[0])
    db_session.add(
        Inventory(
            tenant_id=a.tenant_id, product_id=a.product_ids[0], quantity=2, reorder_threshold=5
        )
    )
    await db_session.flush()

    out = await dashboard_summary(user=user, db=db_session)

    assert len(out.daily) == 14  # continuous axis, gap days zero-filled
    assert out.today_orders == 2
    assert out.today_revenue_lbp == 12000
    three_days_ago = out.daily[-4]
    assert three_days_ago.orders == 1
    assert three_days_ago.revenue_lbp == 2000
    assert sum(1 for p in out.daily if p.orders == 0) == 12  # the gaps are real zeros

    assert out.top_products[0].name == "كعك"
    assert out.top_products[0].units == 5
    assert out.order_status[0].status == "confirmed"
    assert out.order_status[0].count == 3
    assert out.low_stock_count == 1
    assert out.total_products == len(a.product_ids)


async def test_summary_is_tenant_scoped(db_session: AsyncSession, two_tenants: TwoTenants):
    a, b = two_tenants.a, two_tenants.b
    await _seed_orders(db_session, b.tenant_id, b.product_ids[0])  # only B has orders

    user_a = await _user(db_session, a.tenant_id, a.user_email)
    out = await dashboard_summary(user=user_a, db=db_session)

    # A sees none of B's activity — the Wall.
    assert out.today_orders == 0
    assert out.today_revenue_lbp == 0
    assert out.top_products == []
    assert out.order_status == []
