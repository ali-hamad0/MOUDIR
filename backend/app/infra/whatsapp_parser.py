"""Parse a real Meta WhatsApp Cloud API webhook payload into a flat InboundMessage.

The Meta payload is a deeply nested structure. This module flattens it into a
single dataclass that the webhook handler can act on. It also normalises the
phone numbers so tenant lookups work regardless of whether Meta includes the
leading '+' or not.

Returns None for any event that is not an inbound message (delivery receipts,
read receipts, status updates) — callers should respond 200 and exit silently.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.api.schemas.meta_webhook import MetaWebhookPayload


@dataclass(frozen=True)
class InboundMessage:
    """Flat representation of one inbound WhatsApp message.

    to          — destination (business) number, normalised with '+' prefix.
                  Matches tenants.whatsapp_number for tenant lookup.
    from_       — sender number, normalised with '+' prefix.
    text        — message body for type="text"; None for audio.
    audio_id    — Meta media_id for type="audio"; None for text.
    audio_mime  — MIME type of the audio file (e.g. "audio/ogg; codecs=opus").
    display_name — sender's WhatsApp display name from the contacts block.
    message_type — raw Meta type string ("text", "audio", "image", …).
    wamid       — Meta message ID, used for dedup and structured logging.
    """

    to: str
    from_: str
    text: str | None
    audio_id: str | None
    audio_mime: str
    display_name: str | None
    message_type: str
    wamid: str


def _normalise_phone(number: str) -> str:
    """Ensure a phone number has a leading '+' for consistent DB lookup.

    Meta's display_phone_number omits the '+' (e.g. "96170123456"), but
    tenants.whatsapp_number is stored with it (e.g. "+96170123456").
    """
    return number if number.startswith("+") else f"+{number}"


def extract_message(payload: MetaWebhookPayload) -> InboundMessage | None:
    """Extract the first inbound message from a Meta webhook payload.

    Returns None when:
    - The entry or changes arrays are empty (malformed but accepted)
    - The value has no 'messages' array (status updates, read receipts)
    - The messages array is empty
    Any None return means the caller should respond 200 and stop — no reply.
    """
    try:
        value = payload.entry[0].changes[0].value
    except IndexError:
        return None

    if not value.messages:
        return None

    message = value.messages[0]

    display_name: str | None = None
    if value.contacts:
        contact = value.contacts[0]
        if contact.profile and contact.profile.name:
            display_name = contact.profile.name

    text: str | None = None
    audio_id: str | None = None
    audio_mime = "audio/ogg; codecs=opus"

    if message.type == "text" and message.text is not None:
        text = message.text.body
    elif message.type == "audio" and message.audio is not None:
        audio_id = message.audio.id
        audio_mime = message.audio.mime_type

    return InboundMessage(
        to=_normalise_phone(value.metadata.display_phone_number),
        from_=_normalise_phone(message.from_),
        text=text,
        audio_id=audio_id,
        audio_mime=audio_mime,
        display_name=display_name,
        message_type=message.type,
        wamid=message.id,
    )
