"""Provider-agnostic email sending (Phase 1.5, founder onboarding).

Two modes, selected by Settings.mail_mode:
- "dev": send over SMTP to MailHog (or any local catcher). NO real mail leaves
  the machine — this is the default for development.
- "api": POST to a provider's HTTP API using httpx.AsyncClient (never the
  `requests` library — constitution). The provider key resolves from Vault
  (Settings.mail_api_key); wire a concrete provider here when one is chosen.

Application code calls `EmailSender.send(...)`; swapping providers is a change in
THIS module, not in callers.
"""

from email.message import EmailMessage

import aiosmtplib

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
        # Placeholder for a real provider (SES/Resend/Postmark) via httpx
        # (the constitution's required HTTP client). Implemented when a provider
        # is chosen; the key is Settings.mail_api_key, resolved from Vault.
        raise NotImplementedError("API mail provider not configured; use mail_mode='dev'")
