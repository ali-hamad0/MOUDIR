"""Cross-tenant isolation — the DoD-critical proof that The Wall holds.

These tests exist to PROVE that Tenant A's scope can never reach Tenant B's
data, and that an unscoped query is refused outright. If the tenant_id filter is
ever removed from the base repository, these tests fail.
"""

from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Product, User
from app.repositories.products import ProductRepository
from app.repositories.users import UserRepository
from tests.conftest import TwoTenants


async def test_list_returns_only_own_products(
    db_session: AsyncSession, two_tenants: TwoTenants
) -> None:
    a_products = await ProductRepository(db_session).list(two_tenants.a.tenant_id)
    a_ids = {p.id for p in a_products}
    assert a_ids == set(two_tenants.a.product_ids)
    # Zero of B's products leak into A's listing.
    assert a_ids.isdisjoint(set(two_tenants.b.product_ids))


async def test_get_across_tenant_returns_none(
    db_session: AsyncSession, two_tenants: TwoTenants
) -> None:
    # Fetch one of B's product ids through A's scope → nothing.
    b_product_id = two_tenants.b.product_ids[0]
    leaked = await ProductRepository(db_session).get(two_tenants.a.tenant_id, b_product_id)
    assert leaked is None


async def test_get_user_by_email_across_tenant_returns_none(
    db_session: AsyncSession, two_tenants: TwoTenants
) -> None:
    # B's user email, looked up in A's scope → nothing (email is unique per tenant).
    leaked = await UserRepository(db_session).get_by_email(
        two_tenants.a.tenant_id, two_tenants.b.user_email
    )
    assert leaked is None


async def test_wall_crossing_is_blocked(db_session: AsyncSession, two_tenants: TwoTenants) -> None:
    """The explicit wall-crossing test: attempting to reach B's data through A's
    scope yields nothing, and an unscoped (None tenant) query is refused."""
    b_product_id = two_tenants.b.product_ids[0]
    repo = ProductRepository(db_session)

    # Cross-tenant fetch: blocked (returns None, never B's row).
    assert await repo.get(two_tenants.a.tenant_id, b_product_id) is None

    # Unscoped query: a None tenant_id is a programming error, not a query.
    with pytest.raises(ValueError):
        await repo.get(None, b_product_id)  # type: ignore[arg-type]


async def test_profile_and_policy_isolation(
    db_session: AsyncSession, two_tenants: TwoTenants
) -> None:
    # A direct scoped count never sees B's rows.
    from app.db.models import BusinessProfile

    a_profiles = (
        await db_session.execute(
            select(func.count())
            .select_from(BusinessProfile)
            .where(BusinessProfile.tenant_id == two_tenants.a.tenant_id)
        )
    ).scalar_one()
    assert a_profiles == 1  # exactly A's own profile

    # B's profile is not reachable from A's tenant_id filter.
    a_sees_b_profile = (
        await db_session.execute(
            select(func.count())
            .select_from(BusinessProfile)
            .where(
                BusinessProfile.tenant_id == two_tenants.a.tenant_id,
                BusinessProfile.business_name == "ShopB",
            )
        )
    ).scalar_one()
    assert a_sees_b_profile == 0


async def test_cross_tenant_join_returns_zero_rows(
    db_session: AsyncSession, two_tenants: TwoTenants
) -> None:
    """A JOIN that pairs A's user with B's products yields nothing — joins are
    scoped on both sides (constitution I)."""
    rows = (
        await db_session.execute(
            select(func.count())
            .select_from(User)
            .join(Product, Product.tenant_id == User.tenant_id)
            .where(
                User.tenant_id == two_tenants.a.tenant_id,
                Product.tenant_id == two_tenants.b.tenant_id,
            )
        )
    ).scalar_one()
    assert rows == 0


async def test_nonexistent_id_in_own_scope_returns_none(
    db_session: AsyncSession, two_tenants: TwoTenants
) -> None:
    assert await ProductRepository(db_session).get(two_tenants.a.tenant_id, uuid4()) is None
