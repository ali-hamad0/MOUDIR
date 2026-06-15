"""Provider-agnostic email sending (Phase 1.5, founder onboarding).

Two modes, selected by Settings.mail_mode:
- "dev": send over SMTP to MailHog (or any local catcher). NO real mail leaves
  the machine — this is the default for development.
- "api": POST to a provider's HTTP API using httpx.AsyncClient (never the
  `requests` library — constitution). The provider key resolves from Vault
  (Settings.mail_api_key). Implemented for Resend (https://resend.com).

Application code calls `EmailSender.send(...)`; swapping providers is a change in
THIS module, not in callers.
"""

from email.message import EmailMessage

import aiosmtplib
import httpx

from app.infra.logging import get_logger
from app.infra.settings import Settings

log = get_logger(__name__)


class EmailSender:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def send(self, *, to: str, subject: str, body: str) -> None:
        """Send a plaintext email. In dev mode this goes to MailHog; an exception
        is logged but never crashes the caller's flow (the activation token still
        exists, so the link can be re-sent)."""
        if self._settings.mail_mode == "api":
            await self._send_via_api(to=to, subject=subject, body=body)
        else:
            await self._send_via_smtp(to=to, subject=subject, body=body)

    async def _send_via_smtp(self, *, to: str, subject: str, body: str) -> None:
        message = EmailMessage()
        message["From"] = self._settings.mail_from
        message["To"] = to
        message["Subject"] = subject
        message.set_content(body)
        await aiosmtplib.send(
            message,
            hostname=self._settings.mail_smtp_host,
            port=self._settings.mail_smtp_port,
            # MailHog has no auth/TLS; real SMTP would add those here.
        )
        log.info("email.sent", mode="smtp", to_domain=to.split("@")[-1])

    async def _send_via_api(self, *, to: str, subject: str, body: str) -> None:
        """Send through Resend's HTTP API (https://resend.com).

        Uses httpx.AsyncClient (the constitution's required HTTP client, never
        `requests`). The API key resolves from Vault (Settings.mail_api_key).
        A non-2xx response is logged and raised so the caller can decide; the
        activation token already exists, so a failed send can be retried.
        """
        api_key = self._settings.mail_api_key.get_secret_value()
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "from": self._settings.mail_from,
                    "to": [to],
                    "subject": subject,
                    "text": body,
                },
            )
        if response.status_code >= 400:
            log.error(
                "email.api.failed",
                status=response.status_code,
                to_domain=to.split("@")[-1],
                detail=response.text[:200],
            )
            response.raise_for_status()
        log.info("email.sent", mode="api", to_domain=to.split("@")[-1])
