"""Founder tenant directory + suspend/reactivate.

Proves, at the service/route layer (the project's harness convention):
the directory lists every tenant; the drill-down counts are tenant-scoped
(The Wall holds on the founder surface); suspension actually cuts WhatsApp
routing and is audited with a reason; double-suspend is a 409; reactivation
restores routing; and the per-tenant cost endpoint returns the owner-dashboard
shape.
"""

from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin import (
    get_tenant_costs_dashboard,
    get_tenant_detail,
    list_tenants,
)
from app.db.models import Admin, AuditLog, Customer
from app.repositories.admins import AdminRepository
from app.repositories.agent_runs import AgentRunRepository
from app.repositories.customers import CustomerRepository
from app.repositories.tenants import TenantRepository
from app.services.identity_resolver import IdentityResolver
from app.services.tenant_admin import set_tenant_active
from tests.conftest import TwoTenants


async def _admin(db: AsyncSession) -> Admin:
    from app.infra.security import hash_password

    admin = Admin(email="founder@test.com", hashed_password=hash_password("x"), is_active=True)
    return await AdminRepository(db).add(admin)


# ------------------------------------------------------------------ directory
async def test_list_tenants_returns_directory(
    db_session: AsyncSession, two_tenants: TwoTenants
) -> None:
    admin = await _admin(db_session)
    views = await list_tenants(admin, db_session)

    by_id = {v.id: v for v in views}
    assert two_tenants.a.tenant_id in by_id
    assert two_tenants.b.tenant_id in by_id
    a = by_id[two_tenants.a.tenant_id]
    assert a.name == "ShopA"
    assert a.whatsapp_number == "+961000000A1"
    assert a.is_active is True


# ---------------------------------------------------- drill-down counts (Wall)
async def test_tenant_detail_counts_are_tenant_scoped(
    db_session: AsyncSession, two_tenants: TwoTenants
) -> None:
    admin = await _admin(db_session)
    customers = CustomerRepository(db_session)
    await customers.add(two_tenants.a.tenant_id, Customer(phone_number="+96170000001"))
    await customers.add(two_tenants.a.tenant_id, Customer(phone_number="+96170000002"))
    await customers.add(two_tenants.b.tenant_id, Customer(phone_number="+96170000001"))

    detail_a = await get_tenant_detail(two_tenants.a.tenant_id, admin, db_session)
    detail_b = await get_tenant_detail(two_tenants.b.tenant_id, admin, db_session)

    # The same phone exists in both tenants; counts must NOT bleed across.
    assert detail_a.customers_count == 2
    assert detail_b.customers_count == 1
    assert detail_a.orders_today == 0


async def test_tenant_detail_unknown_tenant_404(db_session: AsyncSession) -> None:
    admin = await _admin(db_session)
    with pytest.raises(HTTPException) as exc:
        await get_tenant_detail(uuid4(), admin, db_session)
    assert exc.value.status_code == 404


# ------------------------------------------------- suspend / reactivate + audit
async def test_suspend_cuts_whatsapp_and_is_audited_then_reactivate_restores(
    db_session: AsyncSession, two_tenants: TwoTenants
) -> None:
    admin = await _admin(db_session)
    a = two_tenants.a

    # Before: a customer message to Shop A's number resolves.
    resolved = await IdentityResolver(db_session).resolve(to="+961000000A1", from_="+96171234567")
    assert resolved.role == "customer"

    # Suspend → flag flips, audit row lands with the reason.
    tenant = await set_tenant_active(
        db_session, admin=admin, tenant_id=a.tenant_id, active=False, reason="non-payment"
    )
    assert tenant.is_active is False
    audit = (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.tenant_id == a.tenant_id, AuditLog.action == "tenant.suspended"
            )
        )
    ).scalar_one()
    assert audit.actor_id == admin.id
    assert audit.target == "non-payment"

    # Suspended shop: WhatsApp routing now refuses the destination.
    with pytest.raises(HTTPException) as exc:
        await IdentityResolver(db_session).resolve(to="+961000000A1", from_="+96171234567")
    assert exc.value.status_code == 404

    # Double-suspend is a 409, not a silent re-audit.
    with pytest.raises(HTTPException) as exc:
        await set_tenant_active(
            db_session, admin=admin, tenant_id=a.tenant_id, active=False, reason="again"
        )
    assert exc.value.status_code == 409

    # Shop B is untouched — suspension never crosses the Wall.
    b = await TenantRepository(db_session).get_by_id(two_tenants.b.tenant_id)
    assert b is not None and b.is_active is True

    # Reactivate → audited, and routing works again.
    tenant = await set_tenant_active(
        db_session, admin=admin, tenant_id=a.tenant_id, active=True, reason="paid"
    )
    assert tenant.is_active is True
    reaudit = (
        await db_session.execute(
            select(AuditLog).where(
                AuditLog.tenant_id == a.tenant_id, AuditLog.action == "tenant.reactivated"
            )
        )
    ).scalar_one()
    assert reaudit.target == "paid"
    resolved = await IdentityResolver(db_session).resolve(to="+961000000A1", from_="+96171234567")
    assert resolved.role == "customer"


async def test_suspend_unknown_tenant_404(db_session: AsyncSession) -> None:
    admin = await _admin(db_session)
    with pytest.raises(HTTPException) as exc:
        await set_tenant_active(
            db_session, admin=admin, tenant_id=uuid4(), active=False, reason="x"
        )
    assert exc.value.status_code == 404


# ------------------------------------------------------------- per-tenant costs
async def test_tenant_costs_matches_owner_dashboard_shape(
    db_session: AsyncSession, two_tenants: TwoTenants
) -> None:
    admin = await _admin(db_session)
    a = two_tenants.a
    await AgentRunRepository(db_session).create(
        tenant_id=a.tenant_id,
        agent_name="supervisor",
        model_name="gemini-flash",
        prompt_tokens=100,
        completion_tokens=50,
        cost_usd=0.001234,
    )

    data = await get_tenant_costs_dashboard(a.tenant_id, admin, db_session)

    assert set(data.keys()) == {"days", "budget_usd"}
    assert len(data["days"]) == 30
    today = data["days"][-1]
    assert set(today.keys()) == {"date", "total_usd", "by_agent"}
    assert today["total_usd"] == pytest.approx(0.001234)
    assert today["by_agent"]["supervisor"] == pytest.approx(0.001234)

    # Shop B sees none of Shop A's spend.
    data_b = await get_tenant_costs_dashboard(two_tenants.b.tenant_id, admin, db_session)
    assert all(d["total_usd"] == 0 for d in data_b["days"])
