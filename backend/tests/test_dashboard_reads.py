"""Task 3.5 — dashboard read endpoints (/orders/today, /customers, /me).

Proves The Wall holds at the READ layer: each endpoint returns only the JWT
tenant's data, paginates correctly, and derives setup_complete server-side.
Following the project's harness, route handlers are called directly with the
transactional db_session and a real User (tenant scope comes from the user, never
a request body — the routes have no tenant_id parameter to spoof).
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.customers import list_customers
from app.api.me import whoami
from app.api.orders import orders_today
from app.db.models import BusinessProfile, Customer, Order, OrderItem, Product, Tenant, User
from app.infra.security import hash_password
from app.repositories.users import UserRepository
from tests.conftest import TwoTenants


async def _user(db: AsyncSession, tenant_id, email) -> User:
    user = await UserRepository(db).get_by_email(tenant_id, email)
    assert user is not None
    return user


async def _add_order(db: AsyncSession, *, tenant_id, customer_id, product_id, total_lbp, qty=1):
    order = Order(
        tenant_id=tenant_id,
        customer_id=customer_id,
        status="confirmed",
        fulfillment_type="pickup",
        total_lbp=total_lbp,
        total_usd=None,
    )
    db.add(order)
    await db.flush()
    db.add(
        OrderItem(
            tenant_id=tenant_id,
            order_id=order.id,
            product_id=product_id,
            name_ar_snapshot="كعك",
            quantity=qty,
            unit_price_lbp=total_lbp // qty if qty else total_lbp,
            line_total_lbp=total_lbp,
        )
    )
    await db.flush()
    return order


# ---------------------------------------------------------------- /orders/today
async def test_orders_today_is_tenant_scoped(
    db_session: AsyncSession, two_tenants: TwoTenants
) -> None:
    a, b = two_tenants.a, two_tenants.b
    ca = Customer(tenant_id=a.tenant_id, phone_number="+9613000001", display_name="زبون أ")
    cb = Customer(tenant_id=b.tenant_id, phone_number="+9613000002", display_name="زبون ب")
    db_session.add_all([ca, cb])
    await db_session.flush()
    oa = await _add_order(
        db_session,
        tenant_id=a.tenant_id,
        customer_id=ca.id,
        product_id=a.product_ids[0],
        total_lbp=1000,
    )
    ob = await _add_order(
        db_session,
        tenant_id=b.tenant_id,
        customer_id=cb.id,
        product_id=b.product_ids[0],
        total_lbp=5000,
    )

    user_a = await _user(db_session, a.tenant_id, a.user_email)
    page = await orders_today(user=user_a, db=db_session, limit=50, offset=0)

    assert page.total == 1
    assert [o.id for o in page.items] == [oa.id]
    # B's order never appears for A — the Wall.
    assert all(o.id != ob.id for o in page.items)
    assert page.items[0].customer_display_name == "زبون أ"
    # items joined, and B's order is unreachable from A's session view
    assert page.items[0].items[0].name_ar_snapshot == "كعك"


async def test_orders_today_pagination(db_session: AsyncSession, two_tenants: TwoTenants) -> None:
    a = two_tenants.a
    ca = Customer(tenant_id=a.tenant_id, phone_number="+9613000010", display_name="زبون")
    db_session.add(ca)
    await db_session.flush()
    for i in range(3):
        await _add_order(
            db_session,
            tenant_id=a.tenant_id,
            customer_id=ca.id,
            product_id=a.product_ids[0],
            total_lbp=1000 * (i + 1),
        )
    user_a = await _user(db_session, a.tenant_id, a.user_email)

    full = await orders_today(user=user_a, db=db_session, limit=50, offset=0)
    assert full.total == 3 and len(full.items) == 3

    p1 = await orders_today(user=user_a, db=db_session, limit=1, offset=0)
    p2 = await orders_today(user=user_a, db=db_session, limit=1, offset=1)
    assert len(p1.items) == 1 and len(p2.items) == 1
    assert p1.items[0].id != p2.items[0].id
    assert p1.total == 3  # total is the full count, not the page size


# ------------------------------------------------------------------- /customers
async def test_customers_list_scoped_with_stats(
    db_session: AsyncSession, two_tenants: TwoTenants
) -> None:
    a, b = two_tenants.a, two_tenants.b
    ca1 = Customer(tenant_id=a.tenant_id, phone_number="+9613000011", display_name="مع طلبات")
    ca2 = Customer(tenant_id=a.tenant_id, phone_number="+9613000012", display_name="بلا طلبات")
    cb1 = Customer(tenant_id=b.tenant_id, phone_number="+9613000021", display_name="زبون ب")
    db_session.add_all([ca1, ca2, cb1])
    await db_session.flush()
    await _add_order(
        db_session,
        tenant_id=a.tenant_id,
        customer_id=ca1.id,
        product_id=a.product_ids[0],
        total_lbp=1000,
    )
    await _add_order(
        db_session,
        tenant_id=a.tenant_id,
        customer_id=ca1.id,
        product_id=a.product_ids[0],
        total_lbp=2500,
    )
    await _add_order(
        db_session,
        tenant_id=b.tenant_id,
        customer_id=cb1.id,
        product_id=b.product_ids[0],
        total_lbp=9999,
    )

    user_a = await _user(db_session, a.tenant_id, a.user_email)
    page = await list_customers(user=user_a, db=db_session, limit=50, offset=0)

    assert page.total == 2  # only A's customers
    by_name = {c.display_name: c for c in page.items}
    assert by_name["مع طلبات"].order_count == 2
    assert by_name["مع طلبات"].total_spent_lbp == 3500  # 1000 + 2500
    assert by_name["مع طلبات"].last_order_at is not None
    # A customer with no orders still appears, totals zero/null.
    assert by_name["بلا طلبات"].order_count == 0
    assert by_name["بلا طلبات"].total_spent_lbp == 0
    assert by_name["بلا طلبات"].last_order_at is None
    # None of B's customers leak into A's list.
    assert all(c.display_name != "زبون ب" for c in page.items)


async def test_customers_pagination(db_session: AsyncSession, two_tenants: TwoTenants) -> None:
    a = two_tenants.a
    db_session.add_all(
        [
            Customer(tenant_id=a.tenant_id, phone_number=f"+96130001{i}", display_name=f"c{i}")
            for i in range(3)
        ]
    )
    await db_session.flush()
    user_a = await _user(db_session, a.tenant_id, a.user_email)
    p1 = await list_customers(user=user_a, db=db_session, limit=2, offset=0)
    p2 = await list_customers(user=user_a, db=db_session, limit=2, offset=2)
    assert p1.total == 3 and len(p1.items) == 2 and len(p2.items) == 1


# -------------------------------------------------------------------------- /me
async def test_me_setup_complete_flips(db_session: AsyncSession) -> None:
    tenant = Tenant(name="Fresh", whatsapp_number="+961ME0001", plan_tier="free")
    db_session.add(tenant)
    await db_session.flush()
    db_session.add(BusinessProfile(tenant_id=tenant.id))  # blank, no business_name
    db_session.add(
        User(
            tenant_id=tenant.id,
            email="me@me.com",
            hashed_password=hash_password("password123"),
            role="owner",
        )
    )
    await db_session.flush()
    user = await _user(db_session, tenant.id, "me@me.com")

    before = await whoami(user=user, db=db_session)
    assert before.setup_complete is False
    assert before.product_count == 0
    assert before.plan_tier == "free"
    assert before.tenant_id == tenant.id

    # Name the profile + add a product → setup_complete becomes True. Fetch the
    # profile via the same repo path the route uses.
    from app.repositories.business_profile import BusinessProfileRepository

    prof = await BusinessProfileRepository(db_session).get_for_tenant(tenant.id)
    prof.business_name = "فرن أبو خالد"
    db_session.add(Product(tenant_id=tenant.id, name_ar="كعك", price_lbp=1000))
    await db_session.flush()

    after = await whoami(user=user, db=db_session)
    assert after.setup_complete is True
    assert after.product_count == 1
    assert after.business_name == "فرن أبو خالد"


async def test_me_returns_only_callers_tenant(
    db_session: AsyncSession, two_tenants: TwoTenants
) -> None:
    a, b = two_tenants.a, two_tenants.b
    user_a = await _user(db_session, a.tenant_id, a.user_email)
    me = await whoami(user=user_a, db=db_session)
    assert me.tenant_id == a.tenant_id
    assert me.tenant_id != b.tenant_id
    assert me.email == a.user_email


# -------------------------------------------------------------------- /products
async def test_list_products_is_tenant_scoped(
    db_session: AsyncSession, two_tenants: TwoTenants
) -> None:
    """GET /products returns this tenant's full catalog (the bill-review picker
    source, 5.18) and never another tenant's — the Wall."""
    from app.api.profile import list_products

    a, b = two_tenants.a, two_tenants.b
    user_a = await _user(db_session, a.tenant_id, a.user_email)
    products = await list_products(user=user_a, db=db_session)

    ids = {p.id for p in products}
    assert ids == set(a.product_ids)  # exactly A's three products
    assert not (ids & set(b.product_ids))  # none of B's leak in
