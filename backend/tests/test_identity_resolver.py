"""Identity resolver — owner vs customer routing, and tenant scoping of identity.

Reuses the two_tenants fixture. Proves the destination number selects the tenant,
the sender number selects the role, and the same phone to two shops is two
distinct identities.
"""

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Customer, Tenant, TenantOwner
from app.services.identity_resolver import IdentityResolver
from tests.conftest import TwoTenants


async def _wa_for(db: AsyncSession, tenant_id) -> str:
    return (
        await db.execute(select(Tenant.whatsapp_number).where(Tenant.id == tenant_id))
    ).scalar_one()


async def test_verified_owner_resolves_as_owner(
    db_session: AsyncSession, two_tenants: TwoTenants
) -> None:
    wa = await _wa_for(db_session, two_tenants.a.tenant_id)
    result = await IdentityResolver(db_session).resolve(to=wa, from_=two_tenants.a.owner_phone)
    assert result.role == "owner"
    assert isinstance(result.actor, TenantOwner)
    assert result.tenant.id == two_tenants.a.tenant_id


async def test_unknown_phone_creates_customer(
    db_session: AsyncSession, two_tenants: TwoTenants
) -> None:
    wa = await _wa_for(db_session, two_tenants.a.tenant_id)
    result = await IdentityResolver(db_session).resolve(
        to=wa, from_="+96170000001", display_name="Zaynab"
    )
    assert result.role == "customer"
    assert isinstance(result.actor, Customer)
    # A customers row now exists for that phone in A's scope.
    count = (
        await db_session.execute(
            select(func.count())
            .select_from(Customer)
            .where(
                Customer.tenant_id == two_tenants.a.tenant_id,
                Customer.phone_number == "+96170000001",
            )
        )
    ).scalar_one()
    assert count == 1


async def test_unknown_phone_reused_on_second_message(
    db_session: AsyncSession, two_tenants: TwoTenants
) -> None:
    wa = await _wa_for(db_session, two_tenants.a.tenant_id)
    resolver = IdentityResolver(db_session)
    first = await resolver.resolve(to=wa, from_="+96170000002")
    second = await resolver.resolve(to=wa, from_="+96170000002")
    assert first.actor.id == second.actor.id  # same customer, no duplicate
    count = (
        await db_session.execute(
            select(func.count())
            .select_from(Customer)
            .where(
                Customer.tenant_id == two_tenants.a.tenant_id,
                Customer.phone_number == "+96170000002",
            )
        )
    ).scalar_one()
    assert count == 1


async def test_unknown_destination_raises_404(
    db_session: AsyncSession, two_tenants: TwoTenants
) -> None:
    with pytest.raises(HTTPException) as exc:
        await IdentityResolver(db_session).resolve(to="+961999999999", from_="+96170000003")
    assert exc.value.status_code == 404


async def test_same_phone_to_two_tenants_is_two_identities(
    db_session: AsyncSession, two_tenants: TwoTenants
) -> None:
    wa_a = await _wa_for(db_session, two_tenants.a.tenant_id)
    wa_b = await _wa_for(db_session, two_tenants.b.tenant_id)
    resolver = IdentityResolver(db_session)
    in_a = await resolver.resolve(to=wa_a, from_="+96170000004")
    in_b = await resolver.resolve(to=wa_b, from_="+96170000004")
    # Same sender, different shops → different tenants and different actor rows.
    assert in_a.tenant.id != in_b.tenant.id
    assert in_a.actor.id != in_b.actor.id


async def test_pending_owner_resolves_as_customer(
    db_session: AsyncSession, two_tenants: TwoTenants
) -> None:
    # Add an UNVERIFIED owner phone to A.
    pending_phone = "+96170000005"
    db_session.add(
        TenantOwner(
            tenant_id=two_tenants.a.tenant_id,
            phone_number=pending_phone,
            verification_status="pending",
        )
    )
    await db_session.flush()

    wa = await _wa_for(db_session, two_tenants.a.tenant_id)
    result = await IdentityResolver(db_session).resolve(to=wa, from_=pending_phone)
    # An unverified owner phone is NOT trusted — routed as customer.
    assert result.role == "customer"
    assert isinstance(result.actor, Customer)
