from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Customer
from app.domain.identity import ResolvedIdentity
from app.repositories.customers import CustomerRepository
from app.repositories.tenant_owners import TenantOwnerRepository
from app.repositories.tenants import TenantRepository
from app.services.audit import AuditService


class IdentityResolver:
    """Resolves an inbound WhatsApp message to a tenant + role + actor.

    The destination number ('to') is the ONLY thing that selects the tenant.
    The sender number ('from') decides the role — a verified owner phone, or a
    customer (auto-created on first contact). Because owner/customer lookups are
    tenant-scoped, the same phone messaging two shops is two distinct identities.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def resolve(
        self, *, to: str, from_: str, display_name: str | None = None
    ) -> ResolvedIdentity:
        tenant = await TenantRepository(self._session).get_by_whatsapp_number(to)
        if tenant is None or not tenant.is_active:
            # The destination shop is not on the platform — nothing to route to.
            raise HTTPException(status.HTTP_404_NOT_FOUND, "destination not registered")

        # A pending (unverified) owner phone is NOT trusted as owner — it is
        # treated as a customer until verification completes (Task 1.11).
        owner = await TenantOwnerRepository(self._session).get_by_phone(tenant.id, from_)
        if owner is not None and owner.verification_status == "verified":
            return ResolvedIdentity(tenant=tenant, role="owner", actor=owner)

        customers = CustomerRepository(self._session)
        customer = await customers.get_by_phone(tenant.id, from_)
        if customer is None:
            customer = await customers.add(
                tenant.id, Customer(phone_number=from_, display_name=display_name)
            )
            await AuditService(self._session).record(
                tenant_id=tenant.id,
                actor_id=customer.id,
                action="customer.autocreate",
            )
            await self._session.commit()

        return ResolvedIdentity(tenant=tenant, role="customer", actor=customer)
