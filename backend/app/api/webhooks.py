from typing import Annotated

from fastapi import APIRouter, Depends, Request

from app.api.deps import resolve_message_identity
from app.api.schemas.webhook import WhatsAppWebhookPayload
from app.domain.identity import ResolvedIdentity

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/whatsapp")
async def whatsapp_webhook(
    payload: WhatsAppWebhookPayload,
    identity: Annotated[ResolvedIdentity, Depends(resolve_message_identity)],
    request: Request,
) -> dict:
    """Inbound customer/owner message entry point.

    Depends on the Phase 1 identity resolver UNCHANGED: the destination number
    selects the tenant (unknown → 404), the sender selects the role. The resolved
    message is handed to the lifespan-built dispatcher, which applies the Layer 2
    rails and routes customer → OrderAgent / owner → placeholder. The reply is
    Lebanese Arabic.
    """
    dispatcher = request.app.state.dispatcher
    reply = await dispatcher.dispatch(payload.text, identity)
    return {"reply": reply}
