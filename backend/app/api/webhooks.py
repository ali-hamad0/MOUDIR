from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import resolve_message_identity
from app.domain.identity import ResolvedIdentity

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/whatsapp")
async def whatsapp_webhook(
    identity: Annotated[ResolvedIdentity, Depends(resolve_message_identity)],
) -> dict:
    """Inbound customer/owner message entry point.

    Depends on the Phase 1 identity resolver UNCHANGED: the destination number
    selects the tenant (unknown → 404), the sender selects the role. The message
    dispatcher and OrderAgent are wired in Tasks 2.7/2.11; for now this proves the
    route + identity resolution work end-to-end against the live stack.
    """
    return {"role": identity.role, "tenant": str(identity.tenant.id)}
