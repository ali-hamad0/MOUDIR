from pydantic import BaseModel, Field


class ActivationCheckResponse(BaseModel):
    """GET /activate?token= — tells the set-password screen whether the token is
    usable, and (if so) the email it belongs to so the screen can greet the owner.
    No tenant/internal data is exposed."""

    valid: bool
    email: str | None = None


class ActivateRequest(BaseModel):
    """POST /activate — the owner sets their own password via the one-time link."""

    token: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


class ActivateResponse(BaseModel):
    message: str
