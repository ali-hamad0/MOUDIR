"""Task 6.10 — the read-only /predictions/* API.

Proves the Wall holds at the prediction layer and the predictors are served from app.state
(loaded once in lifespan), never in the handler:
  - each endpoint returns ONLY the JWT tenant's entities (cross-tenant isolation);
  - with the offline stub (CI default) the tenant's entities are still enumerated, just with
    null values — exactly what the isolation test needs;
  - when a real (here, fake) predictor is injected on app.state, its values flow through and
    `mode` reports "trained";
  - an empty tenant yields empty items, not an error.

Handlers are invoked directly (the project's harness) with the transactional db_session, a
real User, and a stand-in `request` whose `app.state` carries the predictors — mirroring how
lifespan injects them, without spinning up the ASGI app.
"""

from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.predictions import predict_anomaly, predict_churn, predict_demand
from app.db.models import Customer, Order, OrderItem, User
from app.ml.predictors import StubAnomalyDetector, StubChurnPredictor, StubDemandPredictor
from app.repositories.tenants import TenantRepository
from app.repositories.users import UserRepository
from tests.conftest import TwoTenants


async def _user(db: AsyncSession, tenant_id, email) -> User:
    """The calling user — with their tenant upgraded to Pro, since insights are
    Pro-gated (Phase 11). These tests prove isolation + predictor plumbing; the
    gate itself is proven in test_plan_gate.py."""
    tenant = await TenantRepository(db).get_by_id(tenant_id)
    assert tenant is not None
    tenant.plan_tier = "pro"
    tenant.subscription_status = "active"
    tenant.current_period_end = date.today() + timedelta(days=30)

    user = await UserRepository(db).get_by_email(tenant_id, email)
    assert user is not None
    return user


async def _seed_history(
    db: AsyncSession, *, tenant_id: UUID, product_id: UUID, phone: str, n_days: int, revenue: int
) -> UUID:
    """A customer with one order per day (an order_item on `product_id`) — enough to give
    the demand, churn, and revenue reads something tenant-scoped to return. Returns the
    customer id so a test can assert isolation by id."""
    customer = Customer(tenant_id=tenant_id, phone_number=phone)
    db.add(customer)
    await db.flush()
    base = datetime(2025, 1, 1, 12, tzinfo=UTC)
    for i in range(n_days):
        order = Order(
            tenant_id=tenant_id,
            customer_id=customer.id,
            status="confirmed",
            total_lbp=revenue,
            created_at=base + timedelta(days=i),
        )
        db.add(order)
        await db.flush()
        db.add(
            OrderItem(
                tenant_id=tenant_id,
                order_id=order.id,
                product_id=product_id,
                name_ar_snapshot="كعك",
                quantity=3,
                line_total_lbp=revenue,
            )
        )
    await db.flush()
    return customer.id


def _request(*, demand=None, churn=None, anomaly=None) -> SimpleNamespace:
    state = SimpleNamespace(
        demand_predictor=demand or StubDemandPredictor(),
        churn_predictor=churn or StubChurnPredictor(),
        anomaly_detector=anomaly or StubAnomalyDetector(),
    )
    return SimpleNamespace(app=SimpleNamespace(state=state))


# ── fakes standing in for trained predictors (prove the plumbing, offline) ───


class _FakeDemand:
    def predict_quantity(self, tenant_id, product_id, history, *, as_of=None):
        return 7


class _FakeChurn:
    def predict_risks(self, tenant_id, orders, *, as_of=None):
        return {str(cid): 0.9 for cid, *_ in orders}


class _FakeAnomaly:
    def flag_days(self, tenant_id, revenue_history, *, window=14):
        return {day: True for day, _ in revenue_history[-window:]}


# ── demand ───────────────────────────────────────────────────────────────────


async def test_demand_is_tenant_scoped(db_session: AsyncSession, two_tenants: TwoTenants) -> None:
    a, b = two_tenants.a, two_tenants.b
    await _seed_history(
        db_session,
        tenant_id=a.tenant_id,
        product_id=a.product_ids[0],
        phone="+9613A",
        n_days=5,
        revenue=1000,
    )
    await _seed_history(
        db_session,
        tenant_id=b.tenant_id,
        product_id=b.product_ids[0],
        phone="+9613B",
        n_days=5,
        revenue=9999,
    )
    user_a = await _user(db_session, a.tenant_id, a.user_email)

    out = await predict_demand(user=user_a, db=db_session, request=_request())

    returned = {item.product_id for item in out.items}
    assert returned == {a.product_ids[0]}  # only A's product with history
    assert not (returned & set(b.product_ids))  # none of B's products leak
    assert out.mode == "stub"
    assert all(item.predicted_units is None for item in out.items)  # stub → no signal


async def test_demand_uses_injected_predictor(
    db_session: AsyncSession, two_tenants: TwoTenants
) -> None:
    a = two_tenants.a
    await _seed_history(
        db_session,
        tenant_id=a.tenant_id,
        product_id=a.product_ids[0],
        phone="+9613A",
        n_days=5,
        revenue=1000,
    )
    user_a = await _user(db_session, a.tenant_id, a.user_email)

    out = await predict_demand(user=user_a, db=db_session, request=_request(demand=_FakeDemand()))

    assert out.mode == "trained"
    assert out.items and all(item.predicted_units == 7 for item in out.items)


# ── churn ──────────────────────────────────────────────────────────────────


async def test_churn_is_tenant_scoped(db_session: AsyncSession, two_tenants: TwoTenants) -> None:
    a, b = two_tenants.a, two_tenants.b
    cust_a = await _seed_history(
        db_session,
        tenant_id=a.tenant_id,
        product_id=a.product_ids[0],
        phone="+9614A",
        n_days=4,
        revenue=1000,
    )
    cust_b = await _seed_history(
        db_session,
        tenant_id=b.tenant_id,
        product_id=b.product_ids[0],
        phone="+9614B",
        n_days=4,
        revenue=2000,
    )
    user_a = await _user(db_session, a.tenant_id, a.user_email)

    out = await predict_churn(user=user_a, db=db_session, request=_request(churn=_FakeChurn()))

    returned = {item.customer_id for item in out.items}
    assert returned == {cust_a}  # only A's customer
    assert cust_b not in returned  # B's customer never appears — the Wall
    assert out.mode == "trained"
    assert all(item.risk == 0.9 for item in out.items)


async def test_churn_stub_returns_null_risk(
    db_session: AsyncSession, two_tenants: TwoTenants
) -> None:
    a = two_tenants.a
    await _seed_history(
        db_session,
        tenant_id=a.tenant_id,
        product_id=a.product_ids[0],
        phone="+9615A",
        n_days=4,
        revenue=1000,
    )
    user_a = await _user(db_session, a.tenant_id, a.user_email)

    out = await predict_churn(user=user_a, db=db_session, request=_request())
    assert out.mode == "stub"
    assert out.items and all(item.risk is None for item in out.items)


# ── anomaly ──────────────────────────────────────────────────────────────────


async def test_anomaly_is_tenant_scoped(db_session: AsyncSession, two_tenants: TwoTenants) -> None:
    a, b = two_tenants.a, two_tenants.b
    await _seed_history(
        db_session,
        tenant_id=a.tenant_id,
        product_id=a.product_ids[0],
        phone="+9616A",
        n_days=6,
        revenue=1111,
    )
    await _seed_history(
        db_session,
        tenant_id=b.tenant_id,
        product_id=b.product_ids[0],
        phone="+9616B",
        n_days=6,
        revenue=8888,
    )
    user_a = await _user(db_session, a.tenant_id, a.user_email)

    out = await predict_anomaly(
        user=user_a, db=db_session, request=_request(anomaly=_FakeAnomaly())
    )

    # Only A's revenue is visible (1111/day); B's 8888/day never appears.
    assert out.items and all(item.revenue_lbp == 1111 for item in out.items)
    assert out.mode == "trained"
    assert all(item.is_anomalous is True for item in out.items)


async def test_anomaly_window_caps_items(db_session: AsyncSession, two_tenants: TwoTenants) -> None:
    a = two_tenants.a
    await _seed_history(
        db_session,
        tenant_id=a.tenant_id,
        product_id=a.product_ids[0],
        phone="+9617A",
        n_days=20,
        revenue=1000,
    )
    user_a = await _user(db_session, a.tenant_id, a.user_email)

    out = await predict_anomaly(user=user_a, db=db_session, request=_request(), window=7)
    assert len(out.items) == 7  # only the most recent `window` days
    assert out.mode == "stub"
    assert all(item.is_anomalous is None for item in out.items)


# ── empty tenant ─────────────────────────────────────────────────────────────


async def test_empty_tenant_returns_empty_items(
    db_session: AsyncSession, two_tenants: TwoTenants
) -> None:
    a = two_tenants.a  # seeded with a catalog but NO orders
    user_a = await _user(db_session, a.tenant_id, a.user_email)
    req = _request()

    assert (await predict_demand(user=user_a, db=db_session, request=req)).items == []
    assert (await predict_churn(user=user_a, db=db_session, request=req)).items == []
    assert (await predict_anomaly(user=user_a, db=db_session, request=req)).items == []
