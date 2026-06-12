"""Task 4.3 — inventory + suppliers CRUD endpoints.

Proves The Wall holds for manual inventory management: GET /inventory returns
only the JWT tenant's products (joined to the catalog, low-stock flagged), an
upsert against another tenant's product id is a 404 (scoped lookup, no write),
and every manual change is audited. Following the suite's harness, route handlers
are called directly with the transactional db_session and a real User — the routes
have no tenant_id parameter to spoof; the scope comes from the user.
"""

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.inventory import (
    create_supplier,
    list_inventory,
    list_suppliers,
    update_supplier,
    upsert_inventory,
)
from app.api.schemas.inventory import InventoryUpsert, SupplierUpsert
from app.db.models import AuditLog, Inventory, User
from app.repositories.users import UserRepository
from tests.conftest import TwoTenants


async def _user(db: AsyncSession, tenant_id, email) -> User:
    user = await UserRepository(db).get_by_email(tenant_id, email)
    assert user is not None
    return user


async def _audit_count(db: AsyncSession, tenant_id, action: str) -> int:
    stmt = (
        select(func.count())
        .select_from(AuditLog)
        .where(AuditLog.tenant_id == tenant_id, AuditLog.action == action)
    )
    return int((await db.execute(stmt)).scalar_one())


# --------------------------------------------------------------- GET /inventory
async def test_inventory_list_is_tenant_scoped_and_joined(
    db_session: AsyncSession, two_tenants: TwoTenants
) -> None:
    a, b = two_tenants.a, two_tenants.b
    # A sets a level for one of its products; B sets one for one of its own.
    user_a = await _user(db_session, a.tenant_id, a.user_email)
    await upsert_inventory(
        product_id=a.product_ids[0],
        payload=InventoryUpsert(quantity=20, reorder_threshold=5),
        user=user_a,
        db=db_session,
    )
    user_b = await _user(db_session, b.tenant_id, b.user_email)
    await upsert_inventory(
        product_id=b.product_ids[0],
        payload=InventoryUpsert(quantity=99),
        user=user_b,
        db=db_session,
    )

    page = await list_inventory(user=user_a, db=db_session, limit=50, offset=0)

    # LEFT JOIN: ALL of A's catalog appears, not just products with a set level.
    assert page.total == len(a.product_ids)
    by_product = {row.product_id: row for row in page.items}
    assert set(by_product) == set(a.product_ids)
    assert by_product[a.product_ids[0]].quantity == 20
    # Products with no inventory row yet show as quantity 0, not low.
    for pid in a.product_ids[1:]:
        assert by_product[pid].quantity == 0
        assert by_product[pid].is_low is False
    # Joined to the catalog: the product name comes through.
    assert by_product[a.product_ids[0]].name_ar.startswith("ShopA")
    # B's products never appear for A — the Wall.
    assert all(row.product_id != b.product_ids[0] for row in page.items)


async def test_inventory_low_stock_flag(db_session: AsyncSession, two_tenants: TwoTenants) -> None:
    a = two_tenants.a
    user_a = await _user(db_session, a.tenant_id, a.user_email)
    # At/below threshold → low; above → not; null threshold → never low.
    await upsert_inventory(
        product_id=a.product_ids[0],
        payload=InventoryUpsert(quantity=3, reorder_threshold=5),
        user=user_a,
        db=db_session,
    )
    await upsert_inventory(
        product_id=a.product_ids[1],
        payload=InventoryUpsert(quantity=50, reorder_threshold=5),
        user=user_a,
        db=db_session,
    )
    await upsert_inventory(
        product_id=a.product_ids[2],
        payload=InventoryUpsert(quantity=0, reorder_threshold=None),
        user=user_a,
        db=db_session,
    )

    page = await list_inventory(user=user_a, db=db_session, limit=50, offset=0)
    by_product = {row.product_id: row for row in page.items}
    assert by_product[a.product_ids[0]].is_low is True  # 3 <= 5
    assert by_product[a.product_ids[1]].is_low is False  # 50 > 5
    assert by_product[a.product_ids[2]].is_low is False  # no threshold set


async def test_inventory_pagination(db_session: AsyncSession, two_tenants: TwoTenants) -> None:
    a = two_tenants.a
    user_a = await _user(db_session, a.tenant_id, a.user_email)
    for pid in a.product_ids:
        await upsert_inventory(
            product_id=pid, payload=InventoryUpsert(quantity=1), user=user_a, db=db_session
        )
    p1 = await list_inventory(user=user_a, db=db_session, limit=2, offset=0)
    p2 = await list_inventory(user=user_a, db=db_session, limit=2, offset=2)
    assert p1.total == 3 and len(p1.items) == 2 and len(p2.items) == 1


# --------------------------------------------------- PUT /inventory/{product_id}
async def test_upsert_creates_then_updates_and_audits(
    db_session: AsyncSession, two_tenants: TwoTenants
) -> None:
    a = two_tenants.a
    user_a = await _user(db_session, a.tenant_id, a.user_email)

    created = await upsert_inventory(
        product_id=a.product_ids[0],
        payload=InventoryUpsert(quantity=10, reorder_threshold=2, reorder_quantity=20),
        user=user_a,
        db=db_session,
    )
    assert created.quantity == 10 and created.reorder_threshold == 2

    # Exactly one inventory row exists for this (tenant, product) — upsert, not insert.
    updated = await upsert_inventory(
        product_id=a.product_ids[0],
        payload=InventoryUpsert(quantity=4),
        user=user_a,
        db=db_session,
    )
    assert updated.quantity == 4
    assert updated.reorder_threshold is None  # replaced, not merged

    count = (
        await db_session.execute(
            select(func.count())
            .select_from(Inventory)
            .where(
                Inventory.tenant_id == a.tenant_id,
                Inventory.product_id == a.product_ids[0],
            )
        )
    ).scalar_one()
    assert count == 1
    # Each manual change is audited.
    assert await _audit_count(db_session, a.tenant_id, "inventory.adjusted") == 2


async def test_upsert_against_other_tenant_product_is_404(
    db_session: AsyncSession, two_tenants: TwoTenants
) -> None:
    a, b = two_tenants.a, two_tenants.b
    user_a = await _user(db_session, a.tenant_id, a.user_email)

    with pytest.raises(HTTPException) as exc:
        await upsert_inventory(
            product_id=b.product_ids[0],  # B's product, A's scope
            payload=InventoryUpsert(quantity=5),
            user=user_a,
            db=db_session,
        )
    assert exc.value.status_code == 404
    # No write landed against B's product under A's scope.
    leaked = (
        await db_session.execute(
            select(func.count())
            .select_from(Inventory)
            .where(Inventory.product_id == b.product_ids[0])
        )
    ).scalar_one()
    assert leaked == 0


async def test_upsert_with_other_tenant_supplier_is_404(
    db_session: AsyncSession, two_tenants: TwoTenants
) -> None:
    a, b = two_tenants.a, two_tenants.b
    # B owns a supplier; A must not be able to reference it.
    user_b = await _user(db_session, b.tenant_id, b.user_email)
    b_supplier = await create_supplier(
        payload=SupplierUpsert(name="مورّد ب"), user=user_b, db=db_session
    )
    user_a = await _user(db_session, a.tenant_id, a.user_email)

    with pytest.raises(HTTPException) as exc:
        await upsert_inventory(
            product_id=a.product_ids[0],
            payload=InventoryUpsert(quantity=5, supplier_id=b_supplier.id),
            user=user_a,
            db=db_session,
        )
    assert exc.value.status_code == 404


# ------------------------------------------------------------------- suppliers
async def test_supplier_crud_scoped_and_audited(
    db_session: AsyncSession, two_tenants: TwoTenants
) -> None:
    a, b = two_tenants.a, two_tenants.b
    user_a = await _user(db_session, a.tenant_id, a.user_email)
    user_b = await _user(db_session, b.tenant_id, b.user_email)

    created = await create_supplier(
        payload=SupplierUpsert(name="مورّد الطحين", webhook_url="https://hook.test/a"),
        user=user_a,
        db=db_session,
    )
    await create_supplier(payload=SupplierUpsert(name="مورّد ب"), user=user_b, db=db_session)

    # A lists only its own supplier — never B's.
    a_list = await list_suppliers(user=user_a, db=db_session)
    assert [s.name for s in a_list] == ["مورّد الطحين"]

    updated = await update_supplier(
        supplier_id=created.id,
        payload=SupplierUpsert(name="مورّد الطحين الجديد", is_active=False),
        user=user_a,
        db=db_session,
    )
    assert updated.name == "مورّد الطحين الجديد" and updated.is_active is False
    assert await _audit_count(db_session, a.tenant_id, "supplier.created") == 1
    assert await _audit_count(db_session, a.tenant_id, "supplier.updated") == 1


async def test_update_other_tenant_supplier_is_404(
    db_session: AsyncSession, two_tenants: TwoTenants
) -> None:
    a, b = two_tenants.a, two_tenants.b
    user_b = await _user(db_session, b.tenant_id, b.user_email)
    b_supplier = await create_supplier(
        payload=SupplierUpsert(name="مورّد ب"), user=user_b, db=db_session
    )
    user_a = await _user(db_session, a.tenant_id, a.user_email)

    with pytest.raises(HTTPException) as exc:
        await update_supplier(
            supplier_id=b_supplier.id,
            payload=SupplierUpsert(name="اختراق"),
            user=user_a,
            db=db_session,
        )
    assert exc.value.status_code == 404
