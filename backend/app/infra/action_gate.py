"""The single execution gate — the architectural enforcement of constitution V.

THIS IS THE GATE. Modir prepares actions; a human approves them; only then does
the system execute. Any action that side-effects the real world (dispatch a
purchase order to a supplier, send a customer re-engagement message, post a
finance entry) is a Level 2+ action and MUST NOT run on a status flag alone. It
runs only when it can present a cryptographically signed approval token, minted
at the moment a human approved, and verified here before the side effect.

The flow is always:

    human approves  ──►  mint_approval_token(...)  ──►  (token travels with the
                                                          dispatch request)
    side-effecting code  ──►  ActionGate.authorize(token, ...)  ──►  proceed
                                                            │
                                                            └─ absent / forged /
                                                               expired / wrong
                                                               resource/tenant/action
                                                               ──► UnauthorizedAction
                                                                   (NEVER proceeds)

`status == "approved"` on a row is the lifecycle marker for the UI — it is NOT
the gate. A bug that flips a status must still not produce a side effect, because
the side-effecting boundary independently demands a valid token. There is no
"send anyway" path: an accidental call without authorization is rejected, not
executed.

The token is an HMAC-signed JWT over a small, dedicated claim set, signed with
the same Vault-resolved `jwt_secret` as the rest of `app/infra/security.py` (one
signing primitive, one secret to rotate). It is time-boxed by
`settings.approval_token_ttl_minutes` so a leaked or stale approval cannot be
replayed indefinitely.

Phases 5 (bill approval) and 7 (re-engagement, finance actions) reuse this exact
primitive — do not fork it. Add new action strings; do not add new gates.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

import jwt

from app.infra.settings import Settings


class UnauthorizedAction(Exception):
    """A side-effecting action was attempted without valid authorization.

    Raised by `ActionGate.authorize` for ANY failure to prove approval: a missing
    token, a bad signature (tampering), an expired token, or a token whose
    action/resource/tenant does not match the action actually being requested.
    The caller must treat this as "do not execute" — there is no recovery that
    proceeds with the side effect.
    """


@dataclass(frozen=True)
class ApprovedAction:
    """The verified result of a successful `authorize` — proof a human approved.

    Carries the identity of the approver and the exact action/resource/tenant the
    token was minted for. Returned only when every check passed; its mere
    existence is the gate's "yes".
    """

    action: str
    resource_id: UUID
    tenant_id: UUID
    approver_id: UUID


def mint_approval_token(
    settings: Settings,
    *,
    action: str,
    resource_id: UUID,
    tenant_id: UUID,
    approver_id: UUID,
) -> str:
    """Sign a single-action approval token at the moment a human approves.

    Binds the approval to ONE action on ONE resource in ONE tenant by ONE
    approver, with an expiry. Replaying this token for a different resource,
    tenant, or action fails `authorize` (the claims will not match), and it
    expires after `settings.approval_token_ttl_minutes`.

    Claims are deliberately short and dedicated (not the auth claim set):
      act — the action string, e.g. "po.dispatch"
      rid — the resource id (e.g. the purchase-order id)
      tid — the tenant id (the Wall: the token is bound to one tenant)
      sub — the approver's user id
      exp — expiry
    """
    now = datetime.now(UTC)
    payload = {
        "act": action,
        "rid": str(resource_id),
        "tid": str(tenant_id),
        "sub": str(approver_id),
        "iat": now,
        "exp": now + timedelta(minutes=settings.approval_token_ttl_minutes),
    }
    return jwt.encode(
        payload, settings.jwt_secret.get_secret_value(), algorithm=settings.jwt_algorithm
    )


class ActionGate:
    """The one place a side-effecting action is authorized before it executes."""

    @staticmethod
    def authorize(
        settings: Settings,
        token: str | None,
        *,
        action: str,
        resource_id: UUID,
        tenant_id: UUID,
    ) -> ApprovedAction:
        """Verify a token authorizes EXACTLY this action/resource/tenant, or refuse.

        Checks, in order, all of which must pass:
          1. a token is present,
          2. its signature is valid (not tampered) and it is not expired,
          3. its action matches the requested action,
          4. its resource id matches the requested resource,
          5. its tenant id matches the requested tenant (the Wall).

        Any failure raises `UnauthorizedAction`. Only an all-clear returns an
        `ApprovedAction`. There is no branch that proceeds on partial success.
        """
        if not token:
            raise UnauthorizedAction("no approval token presented")

        try:
            claims = jwt.decode(
                token,
                settings.jwt_secret.get_secret_value(),
                algorithms=[settings.jwt_algorithm],
            )
        except jwt.PyJWTError as exc:
            # Covers a bad signature (tampering), an expired token, malformed
            # input — all are "not authorized".
            raise UnauthorizedAction(f"invalid approval token: {exc}") from exc

        if claims.get("act") != action:
            raise UnauthorizedAction(f"token action {claims.get('act')!r} != requested {action!r}")
        if claims.get("rid") != str(resource_id):
            raise UnauthorizedAction("token resource does not match requested resource")
        if claims.get("tid") != str(tenant_id):
            # The Wall: a token minted under tenant A can never authorize an
            # action on tenant B's resource.
            raise UnauthorizedAction("token tenant does not match requested tenant")

        return ApprovedAction(
            action=action,
            resource_id=resource_id,
            tenant_id=tenant_id,
            approver_id=UUID(claims["sub"]),
        )
