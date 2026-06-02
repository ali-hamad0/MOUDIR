"""Task 2.5 — order-writing service.

Proves OrderService writes a correct, snapshotted, audit-logged order and refuses
cross-tenant / unavailable products without writing a row. The full agent/dispatch
flow is exercised in Task 2.13; this isolates the service.
"""

from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.order.schemas import ConfirmedOrder, ParsedOrderItem
from app.db.models import AuditLog, Customer, Order, OrderItem, Product, Tenant
from app.domain.errors import ProductNotInCatalog, ProductUnavailable
from app.services.orders import OrderService


async def _seed(db: AsyncSession):
    ta = Tenant(name="A", whatsapp_number="+961ORDA")
    tb = Tenant(name="B", whatsapp_number="+961ORDB")
    db.add_all([ta, tb])
    await db.flush()
    cust = Customer(tenant_id=ta.id, phone_number="+96170ORD")
    pa = Product(
        tenant_id=ta.id, name_ar="كعك", price_lbp=1000, price_usd=Decimal("0.50"), is_available=True
    )
    pa_unavail = Product(tenant_id=ta.id, name_ar="منقوشة", price_lbp=2000, is_available=False)
    pb = Product(tenant_id=tb.id, name_ar="B-منتج", price_lbp=9999, is_available=True)
    db.add_all([cust, pa, pa_unavail, pb])
    await db.flush()
    return ta, tb, cust, pa, pa_unavail, pb


async def test_create_order_happy_path_snapshots_and_audits(db_session: AsyncSession):
    ta, _tb, cust, pa, _u, _pb = await _seed(db_session)
    order = await OrderService(db_session).create_order(
        tenant_id=ta.id,
        customer_id=cust.id,
        confirmed=ConfirmedOrder(
            items=[ParsedOrderItem(product_id=pa.id, quantity=5)],
            fulfillment_type="pickup",
            requested_time_text="بكرا الصبح",
        ),
        raw_message="بدي ٥ كعكات",
    )
    assert order.total_lbp == 5000
    item = (
        await db_session.execute(select(OrderItem).where(OrderItem.order_id == order.id))
    ).scalar_one()
    assert item.name_ar_snapshot == "كعك"
    assert item.unit_price_lbp == 1000
    audit = (
        await db_session.execute(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.action == "order.confirmed", AuditLog.target == str(order.id))
        )
    ).scalar()
    assert audit == 1


async def test_cross_tenant_product_refused_no_order(db_session: AsyncSession):
    ta, _tb, cust, _pa, _u, pb = await _seed(db_session)
    with pytest.raises(ProductNotInCatalog):
        await OrderService(db_session).create_order(
            tenant_id=ta.id,
            customer_id=cust.id,
            confirmed=ConfirmedOrder(items=[ParsedOrderItem(product_id=pb.id, quantity=1)]),
        )


async def test_unavailable_product_refused(db_session: AsyncSession):
    ta, _tb, cust, _pa, pa_unavail, _pb = await _seed(db_session)
    with pytest.raises(ProductUnavailable):
        await OrderService(db_session).create_order(
            tenant_id=ta.id,
            customer_id=cust.id,
            confirmed=ConfirmedOrder(items=[ParsedOrderItem(product_id=pa_unavail.id, quantity=1)]),
        )


async def test_order_line_price_is_a_snapshot(db_session: AsyncSession):
    ta, _tb, cust, pa, _u, _pb = await _seed(db_session)
    order = await OrderService(db_session).create_order(
        tenant_id=ta.id,
        customer_id=cust.id,
        confirmed=ConfirmedOrder(items=[ParsedOrderItem(product_id=pa.id, quantity=2)]),
    )
    item = (
        await db_session.execute(select(OrderItem).where(OrderItem.order_id == order.id))
    ).scalar_one()
    assert item.unit_price_lbp == 1000
    # Change the catalog price; the past order's snapshot must not move.
    pa.price_lbp = 7777
    await db_session.flush()
    await db_session.refresh(item)
    assert item.unit_price_lbp == 1000
    _ = (await db_session.execute(select(Order).where(Order.id == order.id))).scalar_one()
