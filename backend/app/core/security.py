"""Password hashing, JWT issuance/verification, and device pseudonymisation."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import bcrypt
import jwt

from app.core.config import settings

# Cost factor. 12 is roughly 250 ms on commodity hardware in 2026 -- slow enough
# to make offline cracking expensive, fast enough that a dashboard login does
# not feel broken.
BCRYPT_ROUNDS = 12


# --- Passwords ---------------------------------------------------------------
def _prepare(raw: str) -> bytes:
    """Prepare a password for bcrypt.

    bcrypt silently truncates anything past 72 bytes, which would mean a long
    passphrase is only as strong as its first 72 bytes -- and, worse, that two
    different long passphrases sharing a prefix would both verify. Pre-hashing
    with SHA-256 and base64-encoding gives a fixed 44-byte input, so the whole
    password contributes and nothing is truncated.

    bcrypt is used directly rather than through passlib: passlib 1.7.4 has had
    no release since 2020 and its backend probe is incompatible with bcrypt 5.x.
    """
    digest = hashlib.sha256(raw.encode("utf-8")).digest()
    return base64.b64encode(digest)


def hash_password(raw: str) -> str:
    return bcrypt.hashpw(_prepare(raw), bcrypt.gensalt(rounds=BCRYPT_ROUNDS)).decode("ascii")


def verify_password(raw: str, hashed: str) -> bool:
    if not hashed:
        return False
    try:
        return bcrypt.checkpw(_prepare(raw), hashed.encode("ascii"))
    except (ValueError, TypeError):
        return False


# --- Tokens ------------------------------------------------------------------
def _encode(payload: dict[str, Any], ttl: timedelta, token_type: str) -> str:
    now = datetime.now(UTC)
    body = {
        **payload,
        "iat": int(now.timestamp()),
        "exp": int((now + ttl).timestamp()),
        "jti": str(uuid.uuid4()),
        "typ": token_type,
        "iss": "satva",
    }
    return jwt.encode(body, settings.secret_key, algorithm=settings.jwt_algorithm)


def create_access_token(*, subject: str, role: str, extra: dict | None = None) -> str:
    return _encode(
        {"sub": subject, "role": role, **(extra or {})},
        timedelta(minutes=settings.access_token_ttl_min),
        "access",
    )


def create_refresh_token(*, subject: str) -> str:
    return _encode({"sub": subject}, timedelta(days=settings.refresh_token_ttl_days), "refresh")


def decode_token(token: str, *, expected_type: str = "access") -> dict[str, Any]:
    """Decode and validate. Raises jwt exceptions on failure; callers translate."""
    payload = jwt.decode(
        token,
        settings.secret_key,
        algorithms=[settings.jwt_algorithm],
        issuer="satva",
        options={"require": ["exp", "iat", "sub"]},
    )
    if payload.get("typ") != expected_type:
        raise jwt.InvalidTokenError("unexpected token type")
    return payload


# --- Pseudonymisation --------------------------------------------------------
def pseudonymise_device(device_identifier: str) -> str:
    """Turn a raw device identifier into a stable, non-reversible pseudonym.

    Spec rule 11 requires identity to be separated from scan payloads, but Watch
    still needs to count *independent devices* contributing to a cluster
    (spec 3.3). A keyed hash gives a stable per-device pseudonym that cannot be
    reversed into the original installation identifier without the pepper, and
    which is never stored alongside a user id.
    """
    digest = hmac.new(
        settings.device_hash_pepper.encode("utf-8"),
        device_identifier.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return "dev_" + digest[:32]


def constant_time_compare(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def new_otp_code(length: int = 6) -> str:
    return "".join(secrets.choice("0123456789") for _ in range(length))


def hash_otp(code: str, phone: str) -> str:
    """OTP codes are stored hashed and phone-bound so a DB read cannot replay them."""
    material = (phone + ":" + code).encode("utf-8")
    return hmac.new(settings.secret_key.encode("utf-8"), material, hashlib.sha256).hexdigest()
