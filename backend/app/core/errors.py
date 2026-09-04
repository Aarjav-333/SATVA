"""Application error taxonomy and the FastAPI handlers that render it.

Every error the API returns has a stable machine-readable `code`, so the mobile
client can branch on refusal reasons (a first-class concept in SATVA) without
string-matching human prose.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import get_logger

log = get_logger("satva.errors")

# Starlette renamed these constants; the numeric codes are unchanged. Resolving
# them once here keeps the class definitions readable and avoids a deprecation
# warning on every import, while still working on either version.
_UNPROCESSABLE = getattr(
    status, "HTTP_422_UNPROCESSABLE_CONTENT", None
) or status.HTTP_422_UNPROCESSABLE_ENTITY
_TOO_LARGE = getattr(
    status, "HTTP_413_CONTENT_TOO_LARGE", None
) or status.HTTP_413_REQUEST_ENTITY_TOO_LARGE


class SatvaError(Exception):
    """Base class. `code` is contractual; `message` is human-facing."""

    status_code: int = status.HTTP_400_BAD_REQUEST
    code: str = "satva_error"

    def __init__(self, message: str, *, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}


class NotFoundError(SatvaError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "not_found"


class ConflictError(SatvaError):
    status_code = status.HTTP_409_CONFLICT
    code = "conflict"


class DomainValidationError(SatvaError):
    status_code = _UNPROCESSABLE
    code = "validation_error"


class AuthenticationError(SatvaError):
    status_code = status.HTTP_401_UNAUTHORIZED
    code = "authentication_required"


class PermissionDeniedError(SatvaError):
    status_code = status.HTTP_403_FORBIDDEN
    code = "permission_denied"


class RateLimitError(SatvaError):
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    code = "rate_limited"


class EvidenceRuleViolation(SatvaError):
    """Raised when a caller tries to use Layer A output as evidence.

    This is the programmatic enforcement of the Evidence Rule in spec 3.1 and of
    non-negotiable rules 1 and 2. It is a 409 rather than a 400 because the
    request is well-formed: it is the *state* of the referenced scan that makes
    the operation impermissible.
    """

    status_code = status.HTTP_409_CONFLICT
    code = "evidence_rule_violation"


class MeasurementRefused(SatvaError):
    """A colorimetric read was refused because capture conditions were unusable.

    Spec 7 and non-negotiable rule 3: refusal, not a weak result.
    """

    status_code = _UNPROCESSABLE
    code = "measurement_refused"


class UnsupportedCropError(SatvaError):
    """Spec rule 15: refuse rather than guess."""

    status_code = _UNPROCESSABLE
    code = "unsupported_crop"


class UploadRejected(SatvaError):
    status_code = _TOO_LARGE
    code = "upload_rejected"


class ChainIntegrityError(SatvaError):
    """A custody hash chain failed verification, or an append would break it."""

    status_code = status.HTTP_409_CONFLICT
    code = "chain_integrity_error"


def _payload(code: str, message: str, details: dict | None = None) -> dict:
    return {"error": {"code": code, "message": message, "details": details or {}}}


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(SatvaError)
    async def _satva(_: Request, exc: SatvaError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=_payload(exc.code, exc.message, exc.details),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        fields = [{"loc": list(e.get("loc", [])), "msg": e.get("msg", "")} for e in exc.errors()]
        return JSONResponse(
            status_code=_UNPROCESSABLE,
            content=_payload(
                "validation_error", "Request payload failed validation.", {"fields": fields}
            ),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        mapping = {
            401: "authentication_required",
            403: "permission_denied",
            404: "not_found",
            429: "rate_limited",
        }
        return JSONResponse(
            status_code=exc.status_code,
            content=_payload(mapping.get(exc.status_code, "http_error"), str(exc.detail)),
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled_exception", path=request.url.path, error=str(exc))
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=_payload("internal_error", "An unexpected error occurred."),
        )
