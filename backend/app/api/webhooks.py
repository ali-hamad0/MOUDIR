from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import PlainTextResponse

from app.api.deps import rate_limit_check, resolve_message_identity
from app.api.schemas.webhook import WhatsAppWebhookPayload
from app.domain.identity import ResolvedIdentity
from app.infra.settings import Settings, get_settings

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.get("/whatsapp")
async def whatsapp_verify(
    hub_mode: Annotated[str, Query(alias="hub.mode")] = "",
    hub_verify_token: Annotated[str, Query(alias="hub.verify_token")] = "",
    hub_challenge: Annotated[str, Query(alias="hub.challenge")] = "",
    settings: Annotated[Settings, Depends(get_settings)] = ...,
) -> PlainTextResponse:
    """Meta webhook verification challenge (Phase 10, Task 10.1).

    Meta sends a GET with hub.mode=subscribe, hub.verify_token, and hub.challenge
    when you register or update a webhook URL in the Meta App Dashboard. We must
    respond with the raw challenge string and 200 OK. Any mismatch → 403.
    """
    expected = settings.whatsapp_verify_token.get_secret_value()
    if hub_mode != "subscribe" or hub_verify_token != expected:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Webhook verification failed")
    return PlainTextResponse(hub_challenge, status_code=200)


@router.post("/whatsapp")
async def whatsapp_webhook(
    payload: WhatsAppWebhookPayload,
    identity: Annotated[ResolvedIdentity, Depends(resolve_message_identity)],
    request: Request,
    _: Annotated[None, Depends(rate_limit_check)],
) -> dict:
    """Inbound customer/owner message entry point.

    Phase 10 will replace this route's body with the real Meta payload format
    (Task 10.4). Until then it still accepts the internal WhatsAppWebhookPayload
    schema used by tests and the demo seed.

    Depends on the Phase 1 identity resolver UNCHANGED: the destination number
    selects the tenant (unknown → 404), the sender selects the role. The resolved
    message is handed to the lifespan-built dispatcher, which applies the Layer 2
    rails and routes customer → OrderAgent / owner → placeholder. The reply is
    Lebanese Arabic.
    """
    dispatcher = request.app.state.dispatcher
    reply = await dispatcher.dispatch(payload.text, identity)
    return {"reply": reply}
