from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.meta_webhook import MetaWebhookPayload
from app.db.session import get_db_session
from app.infra.logging import get_logger
from app.infra.settings import Settings, get_settings
from app.infra.whatsapp_parser import extract_message
from app.services.identity_resolver import IdentityResolver

log = get_logger(__name__)

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
    meta_payload: MetaWebhookPayload,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db_session)],
) -> Response:
    """Inbound WhatsApp message handler — real Meta Cloud API format (Phase 10).

    Flow:
      1. Parse the Meta envelope → InboundMessage (None for status updates).
      2. Resolve tenant + role via IdentityResolver (unknown destination → 200).
      3. Rate-limit check — exceeded → 200 silent (Meta must not retry).
      4. Dispatch to agent via MessageDispatcher (guardrails apply inside).
      5. Send reply via WhatsAppClient.send_text() (dev: log; live: Meta API).
      6. Return plain 200 — Meta ignores the body; it only needs the status.

    The Wall: every identity resolution and agent dispatch is tenant-scoped.
    Identity is resolved directly (not via the FastAPI dependency) to avoid a
    body-parameter conflict with the Meta payload schema.
    """
    # 1. Parse
    message = extract_message(meta_payload)
    if message is None:
        # Status updates, delivery receipts, read receipts — silent 200
        return Response(status_code=200)

    log_ctx = {
        "wamid": message.wamid,
        "message_type": message.message_type,
    }

    # 2. Resolve identity — unknown destination is not our tenant; acknowledge
    # silently so Meta doesn't retry (a 404 back to Meta looks like a server error)
    try:
        identity = await IdentityResolver(db).resolve(
            to=message.to, from_=message.from_, display_name=message.display_name
        )
    except HTTPException as exc:
        if exc.status_code == status.HTTP_404_NOT_FOUND:
            log.info("whatsapp.webhook.unknown_destination", **log_ctx)
            return Response(status_code=200)
        raise

    log_ctx.update({"tenant_id": str(identity.tenant.id), "role": identity.role})
    log.info("whatsapp.webhook.inbound", **log_ctx)

    # 3. Rate limit — exceeded → 200 silent (customer sees nothing; Meta doesn't retry)
    limiter = request.app.state.rate_limiter
    db_limit = await limiter.get_limit(identity.tenant.id, db)
    result = await limiter.check_and_increment(identity.tenant.id, db_limit)
    if not result.allowed:
        log.info("whatsapp.webhook.rate_limited", **log_ctx)
        return Response(status_code=200)

    # 4. Text extraction — download + transcribe audio if needed (Task 10.5)
    text = message.text
    if text is None and message.audio_id is not None:
        whatsapp_client = request.app.state.whatsapp_client
        audio_transcriber = request.app.state.audio_transcriber
        audio_bytes, mime_type = await whatsapp_client.download_media(message.audio_id)
        text = await audio_transcriber.transcribe(audio_bytes, mime_type)
        log.info(
            "whatsapp.webhook.audio_transcribed",
            transcript_length=len(text),
            **log_ctx,
        )

    # 5. Dispatch through guardrails → agent → Lebanese Arabic reply
    dispatcher = request.app.state.dispatcher
    reply = await dispatcher.dispatch(text, identity)

    # 6. Send reply back to the sender via Meta. A send failure (e.g. the
    # sandbox refusing a non-allowlisted recipient, an expired token) must NOT
    # bubble up: the inbound work (order, reply text) is already committed, and
    # any non-2xx back to Meta makes it retry the whole message.
    whatsapp_client = request.app.state.whatsapp_client
    try:
        await whatsapp_client.send_text(to=message.from_, body=reply)
        log.info("whatsapp.webhook.replied", **log_ctx)
    except Exception:
        log.warning("whatsapp.webhook.send_failed", exc_info=True, **log_ctx)
    return Response(status_code=200)
