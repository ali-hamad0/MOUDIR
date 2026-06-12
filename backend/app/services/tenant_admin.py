"""Founder tenant administration: suspend / reactivate a shop.

Flipping tenants.is_active is enforced everywhere a tenant is loaded — owner
login (api/auth.py), the tenant dependencies (api/deps.py), and the WhatsApp
identity resolver — so a suspended shop loses both the dashboard and WhatsApp
immediately, and reactivation restores both. This is a Level-3 action
(founder-initiated only, constitution V): it is never reachable from an agent,
always carries a reason, and is always audited.
"""

from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Admin, Tenant
from app.infra.logging import get_logger
from app.repositories.tenants import TenantRepository
from app.services.audit import AuditService

log = get_logger(__name__)


async def set_tenant_active(
    db: AsyncSession,
    *,
    admin: Admin,
    tenant_id: UUID,
    active: bool,
    reason: str,
) -> Tenant:
    """Suspend (active=False) or reactivate (active=True) a tenant.

    409 when the tenant is already in the requested state, so a double-click
    or a stale founder screen never silently re-audits the same action.
    """
    tenant = await TenantRepository(db).get_by_id(tenant_id)
    if tenant is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Tenant not found")
    if tenant.is_active == active:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Tenant is already active" if active else "Tenant is already suspended",
        )

    tenant.is_active = active
    action = "tenant.reactivated" if active else "tenant.suspended"
    await AuditService(db).record(
        tenant_id=tenant.id, actor_id=admin.id, action=action, target=reason
    )
    await db.commit()
    log.info(action, tenant_id=str(tenant.id), admin_id=str(admin.id))
    return tenant
