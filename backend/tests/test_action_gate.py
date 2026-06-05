"""The single execution gate (constitution V): mint → authorize, and every refusal.

These tests are the proof the gate is real. The happy path shows a token minted
for one action authorizes exactly that action. Every other test is a way an
unauthorized dispatch could slip through — wrong resource, wrong tenant, wrong
action, expired, tampered, or simply absent — and each one MUST raise
`UnauthorizedAction`. There is no branch that proceeds on partial success.

Pure crypto: no DB, no tenants fixture needed. We construct a minimal Settings
the same way test_auth.py does.
"""

from uuid import uuid4

import jwt
import pytest
from pydantic import SecretStr

from app.infra.action_gate import (
    ActionGate,
    ApprovedAction,
    UnauthorizedAction,
    mint_approval_token,
)
from app.infra.settings import Settings

ACTION = "po.dispatch"


def _settings(ttl_minutes: int = 30) -> Settings:
    return Settings.model_construct(
        jwt_secret=SecretStr("test-secret-that-is-long-enough-32b"),
        jwt_algorithm="HS256",
        approval_token_ttl_minutes=ttl_minutes,
    )


def test_mint_then_authorize_happy_path() -> None:
    """A token minted for (action, resource, tenant) authorizes that exact call."""
    settings = _settings()
    po_id, tenant_id, approver_id = uuid4(), uuid4(), uuid4()

    token = mint_approval_token(
        settings,
        action=ACTION,
        resource_id=po_id,
        tenant_id=tenant_id,
        approver_id=approver_id,
    )
    approved = ActionGate.authorize(
        settings, token, action=ACTION, resource_id=po_id, tenant_id=tenant_id
    )

    assert isinstance(approved, ApprovedAction)
    assert approved.action == ACTION
    assert approved.resource_id == po_id
    assert approved.tenant_id == tenant_id
    # The gate surfaces WHO approved — straight from the token's sub claim.
    assert approved.approver_id == approver_id


def test_absent_token_is_refused() -> None:
    """No token at all → refused. The constitution-V 'accidental call' guard."""
    settings = _settings()
    with pytest.raises(UnauthorizedAction):
        ActionGate.authorize(settings, None, action=ACTION, resource_id=uuid4(), tenant_id=uuid4())
    with pytest.raises(UnauthorizedAction):
        ActionGate.authorize(settings, "", action=ACTION, resource_id=uuid4(), tenant_id=uuid4())


def test_wrong_resource_is_refused() -> None:
    """Replaying one PO's token to dispatch a DIFFERENT PO → refused."""
    settings = _settings()
    tenant_id, approver_id = uuid4(), uuid4()
    token = mint_approval_token(
        settings,
        action=ACTION,
        resource_id=uuid4(),  # minted for one PO
        tenant_id=tenant_id,
        approver_id=approver_id,
    )
    with pytest.raises(UnauthorizedAction):
        ActionGate.authorize(
            settings,
            token,
            action=ACTION,
            resource_id=uuid4(),  # presented for a different PO
            tenant_id=tenant_id,
        )


def test_wrong_tenant_is_refused() -> None:
    """The Wall: a token minted under tenant A cannot authorize tenant B's action."""
    settings = _settings()
    po_id, approver_id = uuid4(), uuid4()
    token = mint_approval_token(
        settings,
        action=ACTION,
        resource_id=po_id,
        tenant_id=uuid4(),  # tenant A
        approver_id=approver_id,
    )
    with pytest.raises(UnauthorizedAction):
        ActionGate.authorize(
            settings,
            token,
            action=ACTION,
            resource_id=po_id,
            tenant_id=uuid4(),  # tenant B
        )


def test_wrong_action_is_refused() -> None:
    """A token for one action cannot be reused to authorize a different action."""
    settings = _settings()
    po_id, tenant_id, approver_id = uuid4(), uuid4(), uuid4()
    token = mint_approval_token(
        settings,
        action="po.dispatch",
        resource_id=po_id,
        tenant_id=tenant_id,
        approver_id=approver_id,
    )
    with pytest.raises(UnauthorizedAction):
        ActionGate.authorize(
            settings,
            token,
            action="finance.post",  # a different side-effecting action
            resource_id=po_id,
            tenant_id=tenant_id,
        )


def test_expired_token_is_refused() -> None:
    """A token past its TTL → refused (a stale approval cannot be replayed)."""
    expired = _settings(ttl_minutes=-1)  # already expired at mint time
    po_id, tenant_id, approver_id = uuid4(), uuid4(), uuid4()
    token = mint_approval_token(
        expired,
        action=ACTION,
        resource_id=po_id,
        tenant_id=tenant_id,
        approver_id=approver_id,
    )
    # The underlying decode raises ExpiredSignatureError; the gate maps it.
    with pytest.raises(UnauthorizedAction):
        ActionGate.authorize(
            _settings(), token, action=ACTION, resource_id=po_id, tenant_id=tenant_id
        )


def test_tampered_token_is_refused() -> None:
    """Flipping a byte breaks the HMAC signature → refused."""
    settings = _settings()
    po_id, tenant_id, approver_id = uuid4(), uuid4(), uuid4()
    token = mint_approval_token(
        settings,
        action=ACTION,
        resource_id=po_id,
        tenant_id=tenant_id,
        approver_id=approver_id,
    )
    # Corrupt a char in the MIDDLE of the signature segment, keeping it a
    # structurally valid JWT. (Flipping the last base64url char can be a no-op:
    # its trailing bits may decode to the same signature bytes.)
    header, payload, signature = token.split(".")
    i = len(signature) // 2
    flipped = "A" if signature[i] != "A" else "B"
    tampered = f"{header}.{payload}.{signature[:i]}{flipped}{signature[i + 1:]}"
    with pytest.raises(UnauthorizedAction):
        ActionGate.authorize(
            settings, tampered, action=ACTION, resource_id=po_id, tenant_id=tenant_id
        )


def test_token_signed_with_other_secret_is_refused() -> None:
    """A token signed with a different secret (forged) → refused at verify."""
    attacker_settings = Settings.model_construct(
        jwt_secret=SecretStr("a-totally-different-attacker-secret-32b"),
        jwt_algorithm="HS256",
        approval_token_ttl_minutes=30,
    )
    po_id, tenant_id, approver_id = uuid4(), uuid4(), uuid4()
    forged = mint_approval_token(
        attacker_settings,
        action=ACTION,
        resource_id=po_id,
        tenant_id=tenant_id,
        approver_id=approver_id,
    )
    with pytest.raises(UnauthorizedAction):
        ActionGate.authorize(
            _settings(), forged, action=ACTION, resource_id=po_id, tenant_id=tenant_id
        )


def test_garbage_token_is_refused() -> None:
    """A non-JWT string is refused, not crashed-on."""
    settings = _settings()
    with pytest.raises(UnauthorizedAction):
        ActionGate.authorize(
            settings,
            "not-a-jwt",
            action=ACTION,
            resource_id=uuid4(),
            tenant_id=uuid4(),
        )


def test_underlying_decode_actually_raises_on_expiry() -> None:
    """Sanity: the expiry refusal really comes from jwt expiry, not a claim diff."""
    expired = _settings(ttl_minutes=-1)
    po_id, tenant_id = uuid4(), uuid4()
    token = mint_approval_token(
        expired,
        action=ACTION,
        resource_id=po_id,
        tenant_id=tenant_id,
        approver_id=uuid4(),
    )
    with pytest.raises(jwt.ExpiredSignatureError):
        jwt.decode(token, _settings().jwt_secret.get_secret_value(), algorithms=["HS256"])
