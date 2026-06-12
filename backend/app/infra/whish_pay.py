"""Whish Pay collect client (Phase 11) — online subscription checkout.

Whish Money's merchant ("collect") API: we create a collect request and get
back a Whish-hosted payment URL where the owner pays by card or wallet
balance; we then verify the payment status SERVER-SIDE with our channel +
secret before activating anything. Redirect parameters are never trusted.

Mode seam (same convention as infra/whatsapp.py):
  "dev"  → never touches the network. create_collect returns the success
           redirect directly and get_status always reports success — the full
           checkout flow works locally/CI without a merchant account, loudly
           logged as SIMULATED.
  "live" → real HTTPS calls. Requires the channel + secret Whish issues after
           merchant onboarding (https://apps.whish.money), stored in Vault —
           never in code or .env.

API shape (Whish Web Service spec): JSON over HTTPS; headers `channel`,
`secret`, `websiteurl`; responses {status, code, dialog, data}. Endpoint paths
are configurable in settings in case the spec revision differs — fixing a path
is config, not code.
"""

from decimal import Decimal

import httpx

from app.infra.logging import get_logger
from app.infra.settings import Settings

log = get_logger(__name__)

# Collect statuses we normalize to.
SUCCESS = "success"
PENDING = "pending"
FAILED = "failed"


class WhishPayError(RuntimeError):
    """The gateway answered with a failure or an unexpected shape."""


class WhishPayClient:
    def __init__(self, settings: Settings) -> None:
        self._mode = settings.whish_pay_mode
        self._base_url = settings.whish_pay_base_url.rstrip("/")
        self._channel = settings.whish_pay_channel.get_secret_value()
        self._secret = settings.whish_pay_secret.get_secret_value()
        self._website_url = settings.whish_pay_website_url
        self._create_path = settings.whish_pay_create_path
        self._status_path = settings.whish_pay_status_path

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "channel": self._channel,
            "secret": self._secret,
            "websiteurl": self._website_url,
            "Content-Type": "application/json",
        }

    async def create_collect(
        self,
        *,
        amount_usd: Decimal,
        external_id: int,
        invoice: str,
        success_redirect: str,
        failure_redirect: str,
    ) -> str:
        """Create a collect request; return the Whish-hosted payment URL."""
        if self._mode != "live":
            # Dev: skip the hosted page entirely — "paying" is following the
            # success redirect, and get_status() will confirm.
            log.info(
                "whish_pay.create_collect.SIMULATED",
                external_id=external_id,
                amount_usd=str(amount_usd),
            )
            return success_redirect

        payload = {
            "amount": float(amount_usd),
            "currency": "USD",
            "invoice": invoice,
            "externalId": external_id,
            "successCallbackUrl": success_redirect,
            "failureCallbackUrl": failure_redirect,
            "successRedirectUrl": success_redirect,
            "failureRedirectUrl": failure_redirect,
        }
        data = await self._post(self._create_path, payload)
        collect_url = data.get("collectUrl")
        if not collect_url:
            raise WhishPayError("collect response missing collectUrl")
        log.info("whish_pay.create_collect.ok", external_id=external_id)
        return str(collect_url)

    async def get_status(self, external_id: int) -> str:
        """Server-side payment verification: success | pending | failed."""
        if self._mode != "live":
            log.info("whish_pay.get_status.SIMULATED", external_id=external_id, result=SUCCESS)
            return SUCCESS

        data = await self._post(self._status_path, {"currency": "USD", "externalId": external_id})
        raw = str(data.get("collectStatus") or data.get("status") or "").lower()
        if raw == SUCCESS:
            return SUCCESS
        if raw in ("failed", "failure", "expired", "cancelled"):
            return FAILED
        return PENDING

    async def _post(self, path: str, payload: dict) -> dict:
        url = f"{self._base_url}/{path.lstrip('/')}"
        async with httpx.AsyncClient(timeout=20.0) as client:
            res = await client.post(url, json=payload, headers=self._headers)
        if res.status_code != 200:
            log.error("whish_pay.http_error", status=res.status_code, path=path)
            raise WhishPayError(f"Whish API returned HTTP {res.status_code}")
        body = res.json()
        if body.get("status") is not True:
            log.error("whish_pay.api_failure", code=body.get("code"), path=path)
            raise WhishPayError(f"Whish API failure: {body.get('code')}")
        return body.get("data") or {}
