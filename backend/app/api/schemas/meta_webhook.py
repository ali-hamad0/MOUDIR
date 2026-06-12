"""Pydantic schemas for the real Meta WhatsApp Cloud API webhook payload.

Meta sends a deeply nested JSON on every inbound event — messages, delivery
receipts, read receipts, etc. These models parse the full structure so FastAPI
validates the body (bad shape → 422, not a crash). Only the fields we actually
use are declared; extras are ignored via `model_config`.

Reference: https://developers.facebook.com/docs/whatsapp/cloud-api/webhooks/payload-examples
"""

from pydantic import BaseModel, ConfigDict, Field


class MetaText(BaseModel):
    model_config = ConfigDict(extra="ignore")

    body: str


class MetaAudio(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    mime_type: str = "audio/ogg; codecs=opus"


class MetaProfile(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str | None = None


class MetaContact(BaseModel):
    model_config = ConfigDict(extra="ignore")

    profile: MetaProfile | None = None
    wa_id: str


class MetaMetadata(BaseModel):
    model_config = ConfigDict(extra="ignore")

    # Human-readable number (e.g. "96170123456") — used for tenant lookup
    # against tenants.whatsapp_number. NOT the Meta-internal phone_number_id.
    display_phone_number: str
    phone_number_id: str


class MetaMessage(BaseModel):
    """A single inbound message (text, audio, image, …).

    'from' is a Python keyword so Pydantic maps it to from_ via alias.
    """

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    id: str  # wamid — the Meta message ID (used for dedup / logging)
    from_: str = Field(alias="from")
    timestamp: str
    type: str  # "text" | "audio" | "image" | "document" | …
    text: MetaText | None = None
    audio: MetaAudio | None = None


class MetaValue(BaseModel):
    """The payload inside entry[].changes[].value."""

    model_config = ConfigDict(extra="ignore")

    messaging_product: str = "whatsapp"
    metadata: MetaMetadata
    contacts: list[MetaContact] | None = None
    # Inbound messages — absent on status-update notifications.
    messages: list[MetaMessage] | None = None
    # Delivery / read receipts — present instead of messages on status events.
    # Declared so FastAPI accepts the payload without 422; we don't parse further.
    statuses: list[dict] | None = None


class MetaChange(BaseModel):
    model_config = ConfigDict(extra="ignore")

    value: MetaValue
    field: str


class MetaEntry(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    changes: list[MetaChange]


class MetaWebhookPayload(BaseModel):
    """Top-level Meta webhook envelope."""

    model_config = ConfigDict(extra="ignore")

    object: str  # "whatsapp_business_account"
    entry: list[MetaEntry]
