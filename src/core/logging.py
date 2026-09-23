"""
Structured logging subsystem for the Alert Aggregation & Incident
Clustering Engine. Emits one JSON object per record for direct
ingestion by log pipelines (Fluentd/Vector/ELK) without a parsing layer.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
import uuid
from contextvars import ContextVar
from typing import Any

from src.core.config import get_settings

_RESERVED_LOG_RECORD_ATTRS = frozenset(
    {
        "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
        "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
        "created", "msecs", "relativeCreated", "thread", "threadName",
        "processName", "process", "taskName",
    }
)

# Per-request/per-window correlation id, threaded through async/streaming
# contexts without explicit parameter passing.
_correlation_id: ContextVar[str | None] = ContextVar("correlation_id", default=None)


def set_correlation_id(correlation_id: str | None = None) -> str:
    """Bind a correlation id (e.g. window_id, incident_id) to the current context."""
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
            "timestamp": time.strftime(
                "%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)
            )
            + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "service": self._service_name,
            "environment": self._environment,
            "component": record.name,
            "message": record.getMessage(),
            "correlation_id": get_correlation_id(),
        }

        # Merge caller-supplied `extra={...}` fields not already reserved.
        for key, value in record.__dict__.items():
            if key not in _RESERVED_LOG_RECORD_ATTRS and key not in payload:
                payload[key] = value

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, default=str)


def _resolve_log_level() -> int:
    """
    Threshold precedence: explicit LOG_LEVEL env override, then
    environment-derived default (production -> INFO, else DEBUG).
    """
    env_override = os.environ.get("LOG_LEVEL")
    if env_override:
        resolved = logging.getLevelName(env_override.upper())
        if isinstance(resolved, int):
            return resolved

    settings = get_settings()
    return logging.INFO if settings.environment == "production" else logging.DEBUG


def configure_logging(level: int | None = None) -> None:
    """
    Installs a single JSON-emitting stream handler on the root logger.
    Idempotent: repeated calls reset handlers rather than stacking them.
    """
    settings = get_settings()
    resolved_level = level if level is not None else _resolve_log_level()

    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(
        JSONFormatter(
            service_name=settings.service_name,
            environment=settings.environment,
        )
    )

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(resolved_level)


def get_logger(component: str) -> logging.Logger:
    """Scoped logger accessor; component should map to a module/subsystem name."""
    return logging.getLogger(component)