"""
Structured logging for AlertFlow. Emits one JSON object per record for
direct ingestion by log pipelines without a parsing layer.
"""

from __future__ import annotations

import json
import logging
import sys
import time
import uuid
from contextvars import ContextVar
from typing import Any

from alertflow.config.settings import get_settings

_RESERVED_LOG_RECORD_ATTRS = frozenset(
    {
        "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
        "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
        "created", "msecs", "relativeCreated", "thread", "threadName",
        "processName", "process", "taskName",
    }
)

# Correlation id (batch id, incident id) threaded through async contexts
# without explicit parameter passing.
_correlation_id: ContextVar[str | None] = ContextVar("correlation_id", default=None)


def set_correlation_id(correlation_id: str | None = None) -> str:
    cid = correlation_id or str(uuid.uuid4())
    _correlation_id.set(cid)
    return cid


def get_correlation_id() -> str | None:
    return _correlation_id.get()


class JSONFormatter(logging.Formatter):
    """Renders each LogRecord as a single-line JSON document."""

    def __init__(self, service_name: str, environment: str) -> None:
        super().__init__()
        self._service_name = service_name
        self._environment = environment

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created))
            + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "service": self._service_name,
            "environment": self._environment,
            "component": record.name,
            "message": record.getMessage(),
            "correlation_id": get_correlation_id(),
        }

        # Merge caller-supplied `extra={...}` fields that do not shadow LogRecord attrs.
        for key, value in record.__dict__.items():
            if key not in _RESERVED_LOG_RECORD_ATTRS and key not in payload:
                payload[key] = value

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, default=str)


def _resolve_log_level() -> int:
    """Explicit setting wins; otherwise production -> INFO, anything else -> DEBUG."""
    settings = get_settings()
    if settings.log_level is not None:
        return logging.getLevelNamesMapping()[settings.log_level]
    return logging.INFO if settings.environment == "production" else logging.DEBUG


def configure_logging(level: int | None = None) -> None:
    """Idempotent: replaces root handlers rather than stacking them."""
    settings = get_settings()
    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(JSONFormatter(settings.service_name, settings.environment))

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level if level is not None else _resolve_log_level())


def get_logger(component: str) -> logging.Logger:
    return logging.getLogger(component)
