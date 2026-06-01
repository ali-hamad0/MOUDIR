from dataclasses import dataclass
from typing import Literal

from app.db.models import Customer, Tenant, TenantOwner


@dataclass
class ResolvedIdentity:
    """The outcome of resolving an inbound WhatsApp message: which tenant it
    belongs to, whether the sender is the owner or a customer, and the concrete
    actor record."""

    tenant: Tenant
    role: Literal["owner", "customer"]
    actor: TenantOwner | Customer
