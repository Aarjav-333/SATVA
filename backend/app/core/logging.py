"""Structured logging.

Logs are emitted as key/value pairs (JSON in non-development environments) so
that audit records for sensitive actions remain machine-queryable.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog

from app.core.config import settings

_SENSITIVE_KEYS = {
    "password",
    "token",
    "access_token",
    "refresh_token",
    "secret",
    "otp",
    "phone",
    "phone_number",
    "authorization",
    "secret_key",
}


def _redact(_logger: Any, _name: str, event_dict: dict) -> dict:
    """Never let a credential or a raw phone number reach the log sink."""
    for key in list(event_dict):
        if key.lower() in _SENSITIVE_KEYS and event_dict[key] is not None:
            event_dict[key] = "[redacted]"
    return event_dict


def configure_logging() -> None:
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=logging.DEBUG if settings.debug else logging.INFO,
    )
    processors: list = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _redact,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    if settings.env == "development":
        processors.append(structlog.dev.ConsoleRenderer(colors=False))
    else:
        processors.append(structlog.processors.JSONRenderer())

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.DEBUG if settings.debug else logging.INFO
        ),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str = "satva") -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
