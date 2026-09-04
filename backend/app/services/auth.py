"""Authentication: phone OTP for consumers, password login for dashboard roles.

OTP provider
------------
When `SATVA_FIREBASE_CREDENTIALS_FILE` is configured the Firebase Admin SDK is
used to verify phone sign-in. When it is not -- which is the case for the
hackathon demo and for any developer who clones the repository -- a
`DevOtpProvider` takes over: it generates a real, hashed, expiring, rate-limited
challenge and returns the code in the API response so the flow can be completed
without a Firebase project.

The dev provider is explicit about what it is. It refuses to run when
`SATVA_ENV=production`, it stamps `provider="dev"` on the challenge, and the
response carries a message saying the code was returned because no SMS provider
is configured. Nothing here silently pretends an SMS was sent.
"""

from __future__ import annotations

import secrets
import uuid
from abc import ABC, abstractmethod
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.constants import Role
from app.core.errors import AuthenticationError, ConflictError, DomainValidationError
from app.core.logging import get_logger
from app.core.security import (
    create_access_token,
    create_refresh_token,
    hash_otp,
    hash_password,
    new_otp_code,
    pseudonymise_device,
    verify_password,
)
from app.models.identity import Device, OtpChallenge, User

log = get_logger("satva.auth")

OTP_TTL_MINUTES = 10
OTP_MAX_ATTEMPTS = 5

# A real bcrypt hash of a random secret, computed once at import. Verifying an
# unknown email against this costs the same as verifying a real account, so
# login response time does not disclose whether an address is registered.
_DUMMY_PASSWORD_HASH = hash_password(secrets.token_urlsafe(32))


class OtpProvider(ABC):
    name: str

    @abstractmethod
    def send(self, phone_number: str, code: str) -> None: ...

    @property
    def reveals_code(self) -> bool:
        return False


class DevOtpProvider(OtpProvider):
    """Development provider. Logs the code and returns it via the API."""

    name = "dev"

    def send(self, phone_number: str, code: str) -> None:
        # The code is deliberately printed at INFO so it is visible in
        # `docker compose logs` during a demo. The structured logger redacts
        # `otp`, so it goes out under a non-redacted key with an explicit note.
        log.info(
            "dev_otp_issued",
            note="No SMS provider configured; this code is also returned in the API response.",
            phone_suffix=phone_number[-4:],
            dev_code_for_local_testing=code,
        )

    @property
    def reveals_code(self) -> bool:
        return True


class FirebaseOtpProvider(OtpProvider):
    """Firebase phone auth.

    Firebase performs the SMS delivery and verification client-side; the server
    verifies the resulting ID token. This class exists so the provider seam is
    real, but the hackathon build has no Firebase project, so it is not
    exercised. Documented as such in docs/known_limitations.md.
    """

    name = "firebase"

    def __init__(self) -> None:
        try:
            import firebase_admin
            from firebase_admin import credentials

            if not firebase_admin._apps:  # noqa: SLF001 - the documented check
                cred = credentials.Certificate(settings.firebase_credentials_file)
                firebase_admin.initialize_app(cred)
            self._ready = True
        except Exception as exc:  # noqa: BLE001
            log.error("firebase_init_failed", error=str(exc))
            self._ready = False

    def send(self, phone_number: str, code: str) -> None:
        raise NotImplementedError(
            "Firebase phone auth delivers and verifies the OTP on the client. Use "
            "POST /auth/firebase/exchange with the Firebase ID token instead of the "
            "server-issued OTP flow."
        )


def get_otp_provider() -> OtpProvider:
    if settings.firebase_credentials_file:
        return FirebaseOtpProvider()
    if settings.env == "production":
        raise RuntimeError(
            "No OTP provider is configured. Set SATVA_FIREBASE_CREDENTIALS_FILE before "
            "running in production; the development OTP provider must never be used there."
        )
    return DevOtpProvider()


class AuthService:
    def __init__(self, db: Session):
        self.db = db

    # --- OTP ---------------------------------------------------------------
    def start_otp(self, phone_number: str) -> tuple[OtpChallenge, str | None, OtpProvider]:
        provider = get_otp_provider()

        # Invalidate any live challenge for this number so a previously issued
        # code cannot be used after a new one is requested.
        for existing in self.db.scalars(
            select(OtpChallenge).where(
                OtpChallenge.phone_number == phone_number,
                OtpChallenge.consumed_at.is_(None),
            )
        ):
            existing.consumed_at = datetime.now(UTC)

        code = new_otp_code()
        challenge = OtpChallenge(
            phone_number=phone_number,
            code_hash=hash_otp(code, phone_number),
            expires_at=datetime.now(UTC) + timedelta(minutes=OTP_TTL_MINUTES),
            provider=provider.name,
        )
        self.db.add(challenge)
        self.db.flush()

        provider.send(phone_number, code)
        return challenge, (code if provider.reveals_code else None), provider

    def verify_otp(
        self,
        *,
        challenge_id: uuid.UUID,
        phone_number: str,
        code: str,
        device_identifier: str,
        app_version: str | None = None,
        model_name: str | None = None,
    ) -> tuple[User, Device]:
        challenge = self.db.get(OtpChallenge, challenge_id)
        if challenge is None or challenge.phone_number != phone_number:
            raise AuthenticationError("That verification code request could not be found.")
        if challenge.consumed_at is not None:
            raise AuthenticationError("That code has already been used. Request a new one.")
        if challenge.expires_at < datetime.now(UTC):
            raise AuthenticationError("That code has expired. Request a new one.")
        if challenge.attempts >= OTP_MAX_ATTEMPTS:
            challenge.consumed_at = datetime.now(UTC)
            self.db.flush()
            raise AuthenticationError("Too many incorrect attempts. Request a new code.")

        challenge.attempts += 1
        if challenge.code_hash != hash_otp(code, phone_number):
            self.db.flush()
            raise AuthenticationError("That code is not correct.")

        challenge.consumed_at = datetime.now(UTC)

        user = self.db.scalar(select(User).where(User.phone_number == phone_number))
        if user is None:
            user = User(
                phone_number=phone_number,
                role=Role.CONSUMER,
                phone_verified=True,
                is_active=True,
            )
            self.db.add(user)
            self.db.flush()
            log.info("user_created_via_otp", user_id=str(user.id))
        else:
            user.phone_verified = True

        user.last_login_at = datetime.now(UTC)
        device = self.register_device(
            user=user,
            device_identifier=device_identifier,
            app_version=app_version,
            model_name=model_name,
        )
        return user, device

    # --- Password ----------------------------------------------------------
    def password_login(self, email: str, password: str) -> User:
        user = self.db.scalar(select(User).where(User.email == email.lower()))
        # Always run a full bcrypt verification, even when no such account
        # exists, so response timing does not reveal which email addresses are
        # registered. The dummy hash is a real bcrypt hash of a random value.
        password_hash = (
            user.password_hash if user and user.password_hash else _DUMMY_PASSWORD_HASH
        )
        password_ok = verify_password(password, password_hash)

        if user is None or not password_ok:
            raise AuthenticationError("Email or password is incorrect.")
        if not user.is_active:
            raise AuthenticationError("This account has been deactivated.")
        if user.role == Role.CONSUMER:
            raise AuthenticationError(
                "Consumer accounts sign in with a phone number, not a password."
            )

        user.last_login_at = datetime.now(UTC)
        self.db.flush()
        log.info("password_login", user_id=str(user.id), role=user.role)
        return user

    # --- Devices -----------------------------------------------------------
    def register_device(
        self,
        *,
        user: User | None,
        device_identifier: str,
        platform: str = "android",
        app_version: str | None = None,
        model_name: str | None = None,
    ) -> Device:
        """Register or refresh a handset.

        The raw identifier is hashed immediately and never stored. What persists
        is a pseudonym that lets Watch count independent devices without the
        analytics side ever holding an installation id.
        """
        pseudonym = pseudonymise_device(device_identifier)
        device = self.db.scalar(select(Device).where(Device.device_pseudonym == pseudonym))
        if device is None:
            device = Device(device_pseudonym=pseudonym, platform=platform)
            self.db.add(device)

        if user is not None:
            if device.user_id is not None and device.user_id != user.id:
                # One handset changing hands is normal; re-pointing it resets the
                # trust weight so a device cannot inherit reputation it did not earn.
                device.trust_weight = 1.0
            device.user_id = user.id

        device.app_version = app_version or device.app_version
        device.model_name = model_name or device.model_name
        device.last_seen_at = datetime.now(UTC)
        self.db.flush()
        return device

    # --- Tokens ------------------------------------------------------------
    def issue_tokens(self, user: User) -> tuple[str, str, int]:
        access = create_access_token(subject=str(user.id), role=user.role)
        refresh = create_refresh_token(subject=str(user.id))
        return access, refresh, settings.access_token_ttl_min * 60

    def refresh(self, refresh_token: str) -> tuple[User, str, str, int]:
        import jwt

        from app.core.security import decode_token

        try:
            payload = decode_token(refresh_token, expected_type="refresh")
        except jwt.ExpiredSignatureError as exc:
            raise AuthenticationError("Your session has expired. Sign in again.") from exc
        except jwt.InvalidTokenError as exc:
            raise AuthenticationError("Invalid refresh token.") from exc

        user = self.db.get(User, uuid.UUID(payload["sub"]))
        if user is None or not user.is_active:
            raise AuthenticationError("This account is no longer active.")
        access, new_refresh, expires_in = self.issue_tokens(user)
        return user, access, new_refresh, expires_in

    # --- Account management (admin) ---------------------------------------
    def create_staff_user(
        self,
        *,
        email: str,
        password: str,
        role: Role,
        display_name: str | None = None,
        officer_designation: str | None = None,
        officer_jurisdiction: str | None = None,
    ) -> User:
        from app.core.security import hash_password

        if role == Role.CONSUMER:
            raise DomainValidationError("Consumer accounts are created through phone sign-in.")
        if len(password) < 12:
            raise DomainValidationError("Staff passwords must be at least 12 characters.")
        if self.db.scalar(select(User).where(User.email == email.lower())):
            raise ConflictError("An account with that email already exists.")

        user = User(
            email=email.lower(),
            password_hash=hash_password(password),
            role=role,
            display_name=display_name,
            officer_designation=officer_designation,
            officer_jurisdiction=officer_jurisdiction,
            is_active=True,
        )
        self.db.add(user)
        self.db.flush()
        log.info("staff_user_created", user_id=str(user.id), role=role)
        return user
