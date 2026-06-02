from pydantic import BaseModel, ConfigDict, Field


class WhatsAppWebhookPayload(BaseModel):
    """Minimal inbound WhatsApp message shape used for identity resolution.

    'to'   = the business's WhatsApp number → selects the tenant.
    'from' = the sender's number → selects the role (owner vs customer).
    The real webhook route arrives in Phase 2; Phase 1 ships this schema + the
    resolver dependency so Phase 2 depends on them unchanged.
    """

    # 'from' is a Python keyword, so expose it as from_ with a populating alias.
    model_config = ConfigDict(populate_by_name=True)

    to: str = Field(min_length=4, max_length=32)
    from_: str = Field(alias="from", min_length=4, max_length=32)
    text: str | None = None
    display_name: str | None = None
