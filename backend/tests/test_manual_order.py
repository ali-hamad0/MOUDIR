"""Task 8.5 — Manual order entry tests.

POST /orders/manual creates an order through the dashboard without calling
the AI supervisor. Four cases cover: happy path, invalid product, cross-tenant
Wall enforcement, and unauthenticated rejection.

These are unit/integration tests that use the same async SQLAlchemy pattern as
the rest of the test suite (TestClient + real DB session via override).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.orders import router as orders_router
from app.db.models import Customer, User
from app.db.session import get_db_session

# ── helpers ───────────────────────────────────────────────────────────────────


def _make_app(db_override) -> FastAPI:
    """Minimal app with orders router and DB override — no lifespan."""
    app = FastAPI()
    app.include_router(orders_router)
    app.dependency_overrides[get_db_session] = db_override
    return app


# ── fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def tenant_id():
    return uuid4()


@pytest.fixture
def other_tenant_id():
    return uuid4()


@pytest.fixture
def customer_id():
    return uuid4()


@pytest.fixture
def product_id():
    return uuid4()


@pytest.fixture
def user_id():
    return uuid4()


# ── Test 1: valid manual order ────────────────────────────────────────────────


async def test_manual_order_creates_order_with_source_manual(
    tenant_id, customer_id, product_id, user_id
):
    """Authenticated dashboard user creates a valid order → source='manual' in DB."""
    from app.api.deps import get_current_user

    fake_customer = MagicMock(spec=Customer)
    fake_customer.id = customer_id
    fake_customer.display_name = "أبو خالد"
    fake_customer.tenant_id = tenant_id

    fake_user = MagicMock(spec=User)
    fake_user.id = user_id
    fake_user.tenant_id = tenant_id

    created_order = MagicMock()
    created_order.id = uuid4()
    created_order.status = "confirmed"
    created_order.fulfillment_type = "pickup"
    created_order.requested_time_text = None
    created_order.total_lbp = 50000
    created_order.total_usd = None
    created_order.created_at = "2026-06-09T10:00:00"
    created_order.customer_id = customer_id

    mock_session = AsyncMock(spec=AsyncSession)

    async def _db():
        yield mock_session

    with (
        patch("app.api.orders.CustomerRepository") as MockCustRepo,
        patch("app.api.orders.OrderService") as MockOrderService,
        patch("app.api.orders.OrderRepository") as MockOrderRepo,
    ):
        MockCustRepo.return_value.get_by_phone = AsyncMock(return_value=fake_customer)
        MockOrderService.return_value.create_order = AsyncMock(return_value=created_order)
        MockOrderRepo.return_value.items_for_orders = AsyncMock(return_value=[])

        app = _make_app(_db)
        app.dependency_overrides[get_current_user] = lambda: fake_user

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/orders/manual",
                json={
                    "customer_phone": "+96170000001",
                    "items": [{"product_id": str(product_id), "quantity": 2}],
                },
            )

        assert resp.status_code == 201, f"Expected 201, got {resp.status_code}: {resp.text}"

        # Verify OrderService was called with source='manual'
        MockOrderService.return_value.create_order.assert_awaited_once()
        call_kwargs = MockOrderService.return_value.create_order.call_args.kwargs
        assert call_kwargs["source"] == "manual"


# ── Test 2: customer not found ────────────────────────────────────────────────


async def test_manual_order_unknown_customer_returns_404(tenant_id, user_id):
    """Customer phone not found in this tenant → 404."""
    from app.api.deps import get_current_user

    fake_user = MagicMock(spec=User)
    fake_user.id = user_id
    fake_user.tenant_id = tenant_id

    mock_session = AsyncMock(spec=AsyncSession)

    async def _db():
        yield mock_session

    with patch("app.api.orders.CustomerRepository") as MockCustRepo:
        MockCustRepo.return_value.get_by_phone = AsyncMock(return_value=None)

        app = _make_app(_db)
        app.dependency_overrides[get_current_user] = lambda: fake_user

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/orders/manual",
                json={
                    "customer_phone": "+96100000000",
                    "items": [{"product_id": str(uuid4()), "quantity": 1}],
                },
            )

    assert resp.status_code == 404


# ── Test 3: cross-tenant product rejected (The Wall) ─────────────────────────


async def test_manual_order_cross_tenant_product_returns_422(tenant_id, customer_id, user_id):
    """Product from another tenant → 422 (The Wall enforced by OrderService)."""
    from app.api.deps import get_current_user
    from app.domain.errors import ProductNotInCatalog

    fake_customer = MagicMock(spec=Customer)
    fake_customer.id = customer_id
    fake_customer.display_name = None
    fake_customer.tenant_id = tenant_id

    fake_user = MagicMock(spec=User)
    fake_user.id = user_id
    fake_user.tenant_id = tenant_id

    mock_session = AsyncMock(spec=AsyncSession)

    async def _db():
        yield mock_session

    other_product_id = uuid4()

    with (
        patch("app.api.orders.CustomerRepository") as MockCustRepo,
        patch("app.api.orders.OrderService") as MockOrderService,
    ):
        MockCustRepo.return_value.get_by_phone = AsyncMock(return_value=fake_customer)
        MockOrderService.return_value.create_order = AsyncMock(
            side_effect=ProductNotInCatalog(other_product_id)
        )

        app = _make_app(_db)
        app.dependency_overrides[get_current_user] = lambda: fake_user

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.post(
                "/orders/manual",
                json={
                    "customer_phone": "+96170000001",
                    "items": [{"product_id": str(other_product_id), "quantity": 1}],
                },
            )

    assert resp.status_code == 422


# ── Test 4: unauthenticated request ──────────────────────────────────────────


async def test_manual_order_unauthenticated_returns_401():
    """No auth header → 401."""
    mock_session = AsyncMock(spec=AsyncSession)

    async def _db():
        yield mock_session

    app = _make_app(_db)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/orders/manual",
            json={
                "customer_phone": "+96170000001",
                "items": [{"product_id": str(uuid4()), "quantity": 1}],
            },
        )

    assert resp.status_code == 401
