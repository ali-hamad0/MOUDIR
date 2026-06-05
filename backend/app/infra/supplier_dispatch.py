"""The supplier dispatch leg of the HIL loop (Phase 4, Task 4.11).

This is the ONE place a purchase order actually leaves Modir for a supplier. It
is provider-agnostic and dev-safe, mirroring `EmailSender`: two modes selected by
`Settings.po_dispatch_mode`:

- "dev": log the PO payload (and optionally drop a copy in MailHog via the
  existing EmailSender). NO real supplier is ever contacted — this is the default
  for development.
- "webhook": POST the payload to the supplier's `webhook_url` using
  `httpx.AsyncClient` (never the `requests` library — constitution). Any shared
  webhook auth secret resolves from Vault (`Settings.po_dispatch_webhook_secret`).

THE GATE COMES FIRST. Before building or sending anything, `dispatch` calls
`ActionGate.authorize(...)` with the signed approval token (constitution V). An
absent, forged, expired, or mismatched token raises `UnauthorizedAction` and
NOTHING is sent — the PO is not even built into a payload, and no status flip
happens. The API layer (Task 4.12) only ever calls this with a freshly minted
token, but the dispatcher re-checks independently: the gate is enforced at the
boundary that actually side-effects, not trusted from the caller.

`status == "approved"` on the row is NOT the gate (see PurchaseOrder docstring) —
the token is. A bug that flips status can never produce a send here without a
valid token.

Dispatch runs OUT OF BAND of the approve request (fired as an asyncio background
task after that transaction commits — Task 4.12), so it opens its OWN DB session
from the injected sessionmaker to record the outcome. A transient failure is
retried with exponential backoff up to `Settings.po_dispatch_max_retries`; once
the budget is exhausted the PO is marked `dispatch_failed` and lands in the
manual queue. The PO row is the source of truth, so a missed dispatch is always
recoverable from that queue.
"""

import asyncio

import httpx
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.models import PurchaseOrder, Supplier
from app.infra.action_gate import ActionGate
from app.infra.logging import get_logger
from app.infra.settings import Settings
from app.services.purchase_orders import PurchaseOrderService

log = get_logger(__name__)

# The action the approval token must authorize for a PO send. Must match what the
# approve handler mints (Task 4.12) and the ActionGate verifies.
DISPATCH_ACTION = "po.dispatch"


class SupplierDispatcher:
    """Authorizes and sends an approved purchase order to its supplier.

    One instance is built in `lifespan` (next to EmailSender / the agents) and
    reused; it stores no per-call state, so it is concurrency-safe. Each
    `dispatch` opens its own session from the injected sessionmaker to record the
    outcome, because it runs as a background task after the approve commit.
    """

    def __init__(self, settings: Settings, sessionmaker: async_sessionmaker) -> None:
        self._settings = settings
        self._sessionmaker = sessionmaker

    async def dispatch(
        self, po: PurchaseOrder, supplier: Supplier | None, token: str | None
    ) -> None:
        """Send one approved PO to its supplier — but ONLY through the gate.

        Order of operations is deliberate:
          1. ActionGate.authorize(token, ...) — refuse (raise UnauthorizedAction)
             on any absent/forged/expired/mismatched token. Nothing is sent.
          2. Build the payload and attempt delivery, retrying transient failures
             with exponential backoff up to po_dispatch_max_retries.
          3. Success → mark_dispatched; budget exhausted → mark_dispatch_failed.

        Raises UnauthorizedAction if the gate refuses (the caller treats that as
        "not sent" — there is no fallback that sends anyway). Delivery failures do
        NOT propagate: they are absorbed into dispatch_failed so a down supplier
        never crashes the background task.
        """
        # 1. THE GATE — before any side effect, before even building the payload.
        # A bad token raises here and propagates; nothing below runs.
        ActionGate.authorize(
            self._settings,
            token,
            action=DISPATCH_ACTION,
            resource_id=po.id,
            tenant_id=po.tenant_id,
        )

        payload = self._build_payload(po, supplier)

        # 2. Attempt delivery with exponential backoff. attempt 0..max_retries
        # inclusive gives one initial try + max_retries retries.
        last_error: str | None = None
        for attempt in range(self._settings.po_dispatch_max_retries + 1):
            try:
                await self._send(supplier, payload)
            except Exception as exc:  # noqa: BLE001 — any send failure is retryable
                last_error = f"{type(exc).__name__}: {exc}"
                log.warning(
                    "supplier_dispatch.attempt_failed",
                    tenant_id=str(po.tenant_id),
                    po_id=str(po.id),
                    attempt=attempt,
                    error=last_error,
                )
                if attempt < self._settings.po_dispatch_max_retries:
                    delay = self._settings.po_dispatch_backoff_seconds * (2**attempt)
                    await asyncio.sleep(delay)
                continue

            # 3a. Delivered.
            await self._record_dispatched(po)
            log.info(
                "supplier_dispatch.sent",
                tenant_id=str(po.tenant_id),
                po_id=str(po.id),
                mode=self._settings.po_dispatch_mode,
                attempts=attempt + 1,
            )
            return

        # 3b. Retry budget exhausted → manual queue.
        error = last_error or "dispatch failed with no recorded error"
        await self._record_dispatch_failed(po, error)
        log.error(
            "supplier_dispatch.failed",
            tenant_id=str(po.tenant_id),
            po_id=str(po.id),
            attempts=self._settings.po_dispatch_max_retries + 1,
            error=error,
        )

    def _build_payload(self, po: PurchaseOrder, supplier: Supplier | None) -> dict:
        """The reorder payload sent to the supplier. tenant_id is a reference only
        — the supplier never queries Modir's DB; the Wall is enforced upstream."""
        return {
            "purchase_order_id": str(po.id),
            "tenant_id": str(po.tenant_id),
            "product_id": str(po.product_id),
            "quantity": po.quantity,
            "supplier_id": str(supplier.id) if supplier is not None else None,
            "supplier_name": supplier.name if supplier is not None else None,
            "note_ar": po.agent_note_ar,
        }

    async def _send(self, supplier: Supplier | None, payload: dict) -> None:
        """Deliver one attempt. Mode selects the channel; raises on any failure
        (a non-2xx response or a transport error) so the retry loop can react."""
        if self._settings.po_dispatch_mode == "webhook":
            await self._send_via_webhook(supplier, payload)
        else:
            self._send_via_dev_log(payload)

    def _send_via_dev_log(self, payload: dict) -> None:
        """Dev mode: log the payload and stop. NO real supplier is ever contacted
        (mirrors EmailSender's MailHog default — nothing leaves the machine)."""
        log.info("supplier_dispatch.dev", payload=payload)

    async def _send_via_webhook(self, supplier: Supplier | None, payload: dict) -> None:
        """POST the payload to the supplier's webhook via httpx (never requests —
        constitution). A shared auth secret, if configured, rides as a header so
        the supplier can verify the call originated from Modir. Raises on a non-2xx
        status (HTTPStatusError) or a transport error so the attempt is retried."""
        if supplier is None or not supplier.webhook_url:
            # No destination configured — not retryable; surface it as an error so
            # the PO lands in the manual queue for the owner to handle.
            raise ValueError("supplier has no webhook_url configured")

        headers: dict[str, str] = {}
        secret = self._settings.po_dispatch_webhook_secret.get_secret_value()
        if secret:
            headers["X-Modir-Signature"] = secret

        async with httpx.AsyncClient() as client:
            response = await client.post(
                supplier.webhook_url, json=payload, headers=headers, timeout=10.0
            )
            response.raise_for_status()

    async def _record_dispatched(self, po: PurchaseOrder) -> None:
        """Persist the success in its own session/transaction (the dispatcher runs
        out of band of the approve request)."""
        async with self._sessionmaker() as session:
            service = PurchaseOrderService(session)
            await service.mark_dispatched(tenant_id=po.tenant_id, po_id=po.id)
            await session.commit()

    async def _record_dispatch_failed(self, po: PurchaseOrder, error: str) -> None:
        """Persist the failure (→ dispatch_failed, manual queue) in its own
        session/transaction."""
        async with self._sessionmaker() as session:
            service = PurchaseOrderService(session)
            await service.mark_dispatch_failed(tenant_id=po.tenant_id, po_id=po.id, error=error)
            await session.commit()
