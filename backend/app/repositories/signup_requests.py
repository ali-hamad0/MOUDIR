from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import SignupRequest


class SignupRequestRepository:
    """Signup-request lookups/writes. Deliberately NOT tenant-scoped — a request
    sits ABOVE the tenant boundary (it has no tenant until approved). Like
    AdminRepository, it must never be a path into tenant-owned data.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, request: SignupRequest) -> SignupRequest:
        self._session.add(request)
        await self._session.flush()
        return request

    async def get_by_id(self, id_: UUID) -> SignupRequest | None:
        result = await self._session.execute(select(SignupRequest).where(SignupRequest.id == id_))
        return result.scalar_one_or_none()

    async def get_pending_by_email(self, email: str) -> SignupRequest | None:
        """A still-open (pending) request for this email, if any — used to dedupe
        so the same applicant can't pile up multiple pending rows. A previously
        rejected applicant can apply again (only `pending` blocks a re-apply)."""
        result = await self._session.execute(
            select(SignupRequest).where(
                SignupRequest.owner_email == email,
                SignupRequest.status == "pending",
            )
        )
        return result.scalar_one_or_none()

    async def list_by_status(self, status: str | None = None) -> list[SignupRequest]:
        stmt = select(SignupRequest).order_by(SignupRequest.created_at.desc())
        if status is not None:
            stmt = stmt.where(SignupRequest.status == status)
        return list((await self._session.execute(stmt)).scalars().all())
