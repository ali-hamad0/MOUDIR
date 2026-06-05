"""The bill→stock commit leg of the HIL loop (Phase 5, Task 5.11).

This is the ONE place an OCR'd supplier bill actually changes inventory, and it is
the SAME execution gate as the Phase 4 purchase-order dispatch — a new action string
(`bill.commit`), NOT a new gate (constitution V). It mirrors `SupplierDispatcher`:

THE GATE COMES FIRST. Before touching stock, `commit` calls
`ActionGate.authorize(...)` with the signed `bill.commit` token. An absent, forged,
expired, or mismatched token raises `UnauthorizedAction` and NOTHING is committed —
no inventory moves, no status flip. The API layer (Task 5.12) only ever calls this
with a freshly minted token, but the committer re-checks independently: the gate is
enforced at the boundary that actually side-effects, not trusted from the caller.

`status == "committing"` on the row is NOT the gate (see SupplierBill docstring) —
the token is. A bug that flips status can never produce a stock change here without a
valid token.

The commit runs OUT OF BAND of the approve request (fired as a background task after
that transaction commits — Task 5.12), so it opens its OWN DB session. Every
validated, mapped line increases inventory in ONE transaction; if anything fails the
whole thing rolls back and the bill reverts to `extracted` for re-review (no partial
stock change). The bill row is the source of truth, so a failed commit is always
recoverable.
"""

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.models import SupplierBill
from app.infra.action_gate import ActionGate
from app.infra.logging import get_logger
from app.infra.settings import Settings
from app.repositories.inventory import InventoryRepository
from app.repositories.supplier_bills import SupplierBillRepository
from app.services.supplier_bills import SupplierBillService

log = get_logger(__name__)

# The action the approval token must authorize for a bill→stock commit. Must match
# what the approve handler mints (Task 5.12) and the ActionGate verifies. A NEW
# action string on the EXISTING gate — never a new gate.
COMMIT_ACTION = "bill.commit"


class BillCommitter:
    """Authorizes and applies an approved bill's lines to inventory.

    One instance is built in `lifespan` (next to the SupplierDispatcher / agents) and
    reused; it stores no per-call state, so it is concurrency-safe. Each `commit`
    opens its own session from the injected sessionmaker, because it runs as a
    background task after the approve commit.
    """

    def __init__(self, settings: Settings, sessionmaker: async_sessionmaker) -> None:
        self._settings = settings
        self._sessionmaker = sessionmaker

    async def commit(self, bill: SupplierBill, token: str | None) -> None:
        """Apply one approved bill's validated lines to stock — but ONLY through the gate.

        Order of operations is deliberate:
          1. ActionGate.authorize(token, ...) — refuse (raise UnauthorizedAction) on
             any absent/forged/expired/mismatched token. Nothing is committed.
          2. In ONE transaction: ensure_row + increase per mapped line with a positive
             quantity; mark each such line committed; transition the bill to
             `committed`. Unmapped or non-positive lines are skipped + logged.
          3. On any failure, roll back (no partial stock change) and revert the bill to
             `extracted` so the owner can re-review.

        Raises UnauthorizedAction if the gate refuses (the caller treats that as "not
        committed" — there is no fallback that commits anyway). A processing failure
        does NOT propagate: it is absorbed into the revert so a bad commit never
        crashes the background task.
        """
        # 1. THE GATE — before any side effect. A bad token raises here; nothing below
        # runs and no stock moves.
        ActionGate.authorize(
            self._settings,
            token,
            action=COMMIT_ACTION,
            resource_id=bill.id,
            tenant_id=bill.tenant_id,
        )

        tenant_id = bill.tenant_id
        bill_id = bill.id
        try:
            async with self._sessionmaker() as session:
                bills = SupplierBillRepository(session)
                inventory = InventoryRepository(session)
                svc = SupplierBillService(session)

                applied = 0
                skipped = 0
                for line, product in await bills.get_lines(tenant_id, bill_id):
                    qty = self._line_units(line.quantity)
                    if line.product_id is None or product is None or qty <= 0:
                        # Unmapped or nothing to add — skip (the review step requires
                        # mapping before approve, so this is defensive).
                        skipped += 1
                        continue
                    # First stock for a SKU is allowed (ensure_row), then increase.
                    await inventory.ensure_row(tenant_id, line.product_id)
                    await inventory.increase(tenant_id, line.product_id, qty)
                    await svc.mark_line_committed(tenant_id=tenant_id, line=line)
                    applied += 1

                await svc.mark_committed(tenant_id=tenant_id, bill_id=bill_id)
                await session.commit()

            log.info(
                "bill_committer.committed",
                tenant_id=str(tenant_id),
                bill_id=str(bill_id),
                lines_applied=applied,
                lines_skipped=skipped,
            )
        except Exception as exc:  # noqa: BLE001 — any failure → roll back + revert
            error = f"{type(exc).__name__}: {exc}"
            log.error(
                "bill_committer.failed",
                tenant_id=str(tenant_id),
                bill_id=str(bill_id),
                error=error,
            )
            async with self._sessionmaker() as session:
                await SupplierBillService(session).revert_to_extracted(
                    tenant_id=tenant_id, bill_id=bill_id, error=error
                )
                await session.commit()

    @staticmethod
    def _line_units(quantity) -> int:
        """Convert a bill line's Decimal quantity to whole inventory units.

        Inventory is tracked in whole units; a bill quantity like 50 (or 12.0) maps to
        50/12 units. A fractional bill quantity (e.g. 2.5 kg) is floored to whole
        units for now — Phase 5 inventory is integer; documenting the rounding rule
        here. None/negative → 0 (skipped by the caller).
        """
        if quantity is None:
            return 0
        return max(0, int(quantity))
