"""Request dependencies: authentication, authorization, rate limiting, auditing.

Authorization is enforced **here**, on the server, for every protected route.
Hiding a route in the dashboard's client-side router is not access control; the
officer endpoints are the ones that expose vendor identities (non-negotiable
rule 5), so they are gated by a dependency that runs before the handler and
cannot be bypassed by calling the API directly.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

import jwt
from fastapi import Depends, Header, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.constants import Role
from app.core.errors import AuthenticationError, PermissionDeniedError, RateLimitError
from app.core.logging import get_logger
from app.core.security import decode_token
from app.db.session import get_db
from app.models.identity import AuditLog, Device, User

log = get_logger("satva.api.deps")

# auto_error=False so a missing header produces our own error envelope rather
# than FastAPI's, keeping the API's error contract uniform.
bearer_scheme = HTTPBearer(auto_error=False)


@dataclass
class Principal:
    """The authenticated caller."""

    user: User
    role: Role
    token_id: str | None = None

    def __post_init__(self) -> None:
        # `User.role` is a plain string column, so callers naturally pass a str.
        # Coercing here means every consumer can rely on `role` being a Role,
        # rather than each call site remembering to convert.
        if not isinstance(self.role, Role):
            self.role = Role(self.role)

    @property
    def id(self) -> uuid.UUID:
        return self.user.id

    def is_(self, *roles: Role) -> bool:
        return self.role in roles


def get_current_principal(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> Principal:
    if credentials is None or not credentials.credentials:
        raise AuthenticationError("This endpoint requires a bearer token.")

    try:
        payload = decode_token(credentials.credentials, expected_type="access")
    except jwt.ExpiredSignatureError as exc:
        raise AuthenticationError("Your session has expired. Sign in again.") from exc
    except jwt.InvalidTokenError as exc:
        raise AuthenticationError("Invalid authentication token.") from exc

    try:
        user_id = uuid.UUID(payload["sub"])
    except (KeyError, ValueError) as exc:
        raise AuthenticationError("Malformed authentication token.") from exc

    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise AuthenticationError("This account is no longer active.")

    # The role is read from the database, not from the token. A role revoked
    # after a token was issued must take effect immediately; trusting the token's
    # claim would leave a window in which a demoted officer keeps vendor access.
    return Principal(user=user, role=Role(user.role), token_id=payload.get("jti"))


def get_optional_principal(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> Principal | None:
    """For endpoints that behave differently when signed in but do not require it."""
    if credentials is None or not credentials.credentials:
        return None
    try:
        return get_current_principal(credentials, db)
    except AuthenticationError:
        return None


def require_roles(*roles: Role) -> Callable[..., Principal]:
    """Dependency factory enforcing role membership server-side."""

    allowed = set(roles)

    def _dependency(principal: Principal = Depends(get_current_principal)) -> Principal:
        # Administrators can reach every route; there is no separate super-user
        # path that could drift from this one.
        if principal.role is Role.ADMIN or principal.role in allowed:
            return principal
        raise PermissionDeniedError(
            "Your account does not have access to this resource.",
            details={
                "required_roles": sorted(r.value for r in allowed),
                "your_role": principal.role.value,
            },
        )

    return _dependency


require_officer = require_roles(Role.OFFICER)
require_retailer = require_roles(Role.RETAILER)
require_farmer = require_roles(Role.FARMER)
require_admin = require_roles(Role.ADMIN)
require_any_user = require_roles(
    Role.CONSUMER, Role.FARMER, Role.RETAILER, Role.OFFICER, Role.ADMIN
)


def get_device(
    x_satva_device: str | None = Header(default=None, alias="X-SATVA-Device"),
    principal: Principal | None = Depends(get_optional_principal),
    db: Session = Depends(get_db),
) -> Device | None:
    """Resolve the calling handset from its pseudonym header."""
    if not x_satva_device:
        return None
    from sqlalchemy import select

    device = db.scalar(select(Device).where(Device.device_pseudonym == x_satva_device))
    if device is None:
        return None
    if device.is_blocked:
        raise PermissionDeniedError("This device has been blocked from submitting scans.")
    device.last_seen_at = datetime.now(UTC)
    return device


# --- Rate limiting -----------------------------------------------------------
class RateLimiter:
    """Fixed-window rate limiter backed by Redis, with an in-process fallback.

    Redis is the correct backing store because the API runs multiple workers and
    a per-process counter would multiply the effective limit by the worker
    count. The in-memory fallback exists so the service still starts (and tests
    still run) when Redis is unavailable; it logs a warning because in that mode
    the limit is per-process and therefore weaker than configured.
    """

    def __init__(self) -> None:
        self._client = None
        self._local: dict[str, tuple[int, float]] = {}
        self._warned = False

    def _redis(self):
        if self._client is None:
            try:
                import redis

                self._client = redis.Redis.from_url(settings.redis_url, socket_timeout=0.25)
                self._client.ping()
            except Exception:  # noqa: BLE001
                self._client = False
        return self._client or None

    def hit(self, key: str, limit: int, window_seconds: int) -> tuple[bool, int]:
        """Returns (allowed, remaining)."""
        client = self._redis()
        if client is not None:
            try:
                bucket = f"satva:rl:{key}:{int(time.time() // window_seconds)}"
                pipeline = client.pipeline()
                pipeline.incr(bucket)
                pipeline.expire(bucket, window_seconds + 5)
                count = int(pipeline.execute()[0])
                return count <= limit, max(0, limit - count)
            except Exception:  # noqa: BLE001
                self._client = False

        if not self._warned:
            log.warning("rate_limiter_using_in_process_fallback")
            self._warned = True

        now = time.time()
        window_start = now - (now % window_seconds)
        count, start = self._local.get(key, (0, window_start))
        if start < window_start:
            count, start = 0, window_start
        count += 1
        self._local[key] = (count, start)
        return count <= limit, max(0, limit - count)


rate_limiter = RateLimiter()


def rate_limit(
    *, limit: int, window_seconds: int = 3600, scope: str = "default"
) -> Callable[..., None]:
    """Dependency factory applying a rate limit.

    Keyed by device pseudonym when present, otherwise by client IP. Preferring
    the device means a shared connection (a market's public Wi-Fi, or CGNAT)
    does not cause one user's activity to lock out everyone around them.
    """

    def _dependency(
        request: Request,
        x_satva_device: str | None = Header(default=None, alias="X-SATVA-Device"),
    ) -> None:
        identity = x_satva_device or (request.client.host if request.client else "unknown")
        allowed, remaining = rate_limiter.hit(f"{scope}:{identity}", limit, window_seconds)
        if not allowed:
            raise RateLimitError(
                "Too many requests. Please wait before trying again.",
                details={"scope": scope, "limit": limit, "window_seconds": window_seconds},
            )
        request.state.rate_limit_remaining = remaining

    return _dependency


scan_rate_limit = rate_limit(
    limit=settings.rate_limit_scan_per_hour, window_seconds=3600, scope="scan"
)
auth_rate_limit = rate_limit(
    limit=settings.rate_limit_auth_per_hour, window_seconds=3600, scope="auth"
)


# --- Auditing ----------------------------------------------------------------
def write_audit(
    db: Session,
    *,
    action: str,
    principal: Principal | None = None,
    object_type: str | None = None,
    object_id: str | None = None,
    request: Request | None = None,
    context: dict | None = None,
) -> None:
    """Record a sensitive action.

    Called for every view of vendor-level data, every complaint package
    generated, every evidence download and every role change. The audit trail is
    what makes "vendor detail is restricted to authenticated officials" an
    auditable claim rather than an assertion.
    """
    entry = AuditLog(
        actor_user_id=principal.id if principal else None,
        actor_role=principal.role.value if principal else None,
        action=action,
        object_type=object_type,
        object_id=object_id,
        ip_address=(request.client.host if request and request.client else None),
        user_agent=(request.headers.get("user-agent", "")[:256] if request else None),
        context=context,
    )
    db.add(entry)
    log.info(
        "audit",
        action=action,
        actor=str(principal.id) if principal else None,
        role=principal.role.value if principal else None,
        object_type=object_type,
        object_id=object_id,
    )


class AuditedOfficerAccess:
    """Dependency that authorises an officer *and* records the access.

    Combining the two is deliberate: it makes it impossible to add an officer
    endpoint that authorises correctly but forgets to leave an audit trail.
    """

    def __init__(self, action: str, object_type: str | None = None):
        self.action = action
        self.object_type = object_type

    def __call__(
        self,
        request: Request,
        principal: Principal = Depends(require_officer),
        db: Session = Depends(get_db),
    ) -> Principal:
        write_audit(
            db,
            action=self.action,
            principal=principal,
            object_type=self.object_type,
            object_id=request.path_params.get("cluster_id")
            or request.path_params.get("merchant_ref"),
            request=request,
            context={"path": request.url.path, "query": dict(request.query_params)},
        )
        return principal
