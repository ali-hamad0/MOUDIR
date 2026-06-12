"""Regression (found live): a Meta send failure must not 500 the webhook.

Meta retries any non-2xx, and by the time the reply is sent the inbound work
(order creation, agent reply) is already committed — so a refused recipient
(sandbox allow-list), expired token, or Meta outage must be logged and answered
with 200, never bubbled up.
"""

from types import SimpleNamespace

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas.meta_webhook import MetaWebhookPayload
from app.api.webhooks import whatsapp_webhook
from app.db.models import Tenant


def _payload() -> MetaWebhookPayload:
    return MetaWebhookPayload.model_validate(
        {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "id": "ENTRY",
                    "changes": [
                        {
                            "value": {
                                "messaging_product": "whatsapp",
                                "metadata": {
                                    "display_phone_number": "961SENDFAIL1",
                                    "phone_number_id": "PNID",
                                },
                                "contacts": [
                                    {
                                        "profile": {"name": "Test"},
                                        "wa_id": "96170SENDFAIL",
                                    }
                                ],
                                "messages": [
                                    {
                                        "from": "96170SENDFAIL",
                                        "id": "wamid.sendfail001",
                                        "timestamp": "1700000000",
                                        "text": {"body": "مرحبا"},
                                        "type": "text",
                                    }
                                ],
                            },
                            "field": "messages",
                        }
                    ],
                }
            ],
        }
    )


class _FailingWhatsAppClient:
    async def send_text(self, *, to: str, body: str) -> None:
        raise RuntimeError("Meta 400: recipient not in sandbox allow-list")


class _OkRateLimiter:
    async def get_limit(self, tenant_id, db) -> int:
        return 30

    async def check_and_increment(self, tenant_id, limit):
        return SimpleNamespace(allowed=True)


class _StubDispatcher:
    async def dispatch(self, text: str, identity) -> str:
        return "رد تجريبي"


async def test_send_failure_still_returns_200(db_session: AsyncSession) -> None:
    db_session.add(Tenant(name="SendFail", whatsapp_number="+961SENDFAIL1"))
    await db_session.flush()

    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                rate_limiter=_OkRateLimiter(),
                dispatcher=_StubDispatcher(),
                whatsapp_client=_FailingWhatsAppClient(),
            )
        )
    )

    response = await whatsapp_webhook(meta_payload=_payload(), request=request, db=db_session)
    assert response.status_code == 200
