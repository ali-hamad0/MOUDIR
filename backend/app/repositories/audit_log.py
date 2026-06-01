from app.db.models import AuditLog
from app.repositories.base import TenantScopedRepository


class AuditLogRepository(TenantScopedRepository[AuditLog]):
    """Append-only audit trail. Base add()/list() cover Phase 1 needs; the
    centralized AuditService (Task 1.13) writes through this.
    """

    model = AuditLog
