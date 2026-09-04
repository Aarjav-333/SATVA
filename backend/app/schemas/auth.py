"""Authentication and account schemas."""

from __future__ import annotations

import re
from datetime import datetime
from uuid import UUID

from pydantic import EmailStr, Field, field_validator

from app.core.constants import Role
from app.schemas.common import SatvaModel

# Indian mobile numbers in E.164, which is what Firebase phone auth returns.
_PHONE_RE = re.compile(r"^\+91[6-9]\d{9}$")


class PhoneRequest(SatvaModel):
    phone_number: str = Field(examples=["+919447012345"])

    @field_validator("phone_number")
    @classmethod
    def _valid_phone(cls, v: str) -> str:
        cleaned = v.replace(" ", "").replace("-", "")
        if not _PHONE_RE.match(cleaned):
            raise ValueError("phone_number must be an Indian mobile number in +91XXXXXXXXXX form")
        return cleaned


class OtpStartResponse(SatvaModel):
    challenge_id: UUID
    expires_at: datetime
    provider: str
    # Only ever populated by the development OTP provider, so the demo can run
    # without Firebase credentials. Always null when a real provider is configured.
    dev_code: str | None = None
    message: str


class OtpVerifyRequest(PhoneRequest):
    challenge_id: UUID
    code: str = Field(min_length=4, max_length=8)
    device_identifier: str = Field(min_length=8, max_length=200)
    app_version: str | None = Field(default=None, max_length=32)
    model_name: str | None = Field(default=None, max_length=80)


class PasswordLoginRequest(SatvaModel):
    """Used by dashboard roles (officer, retailer, admin)."""

    email: EmailStr
    password: str = Field(min_length=8, max_length=200)


class TokenPair(SatvaModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"  # noqa: S105
    expires_in: int


class RefreshRequest(SatvaModel):
    refresh_token: str


class UserOut(SatvaModel):
    id: UUID
    display_name: str | None
    role: Role
    phone_verified: bool
    locale: str
    officer_designation: str | None = None
    officer_jurisdiction: str | None = None
    created_at: datetime


class AuthResponse(SatvaModel):
    user: UserOut
    tokens: TokenPair
    device_pseudonym: str | None = None


class DeviceRegisterRequest(SatvaModel):
    device_identifier: str = Field(min_length=8, max_length=200)
    platform: str = Field(default="android", max_length=20)
    app_version: str | None = Field(default=None, max_length=32)
    model_name: str | None = Field(default=None, max_length=80)


class DeviceOut(SatvaModel):
    id: UUID
    device_pseudonym: str
    platform: str
    trust_weight: float
    attestation_state: str
    created_at: datetime
