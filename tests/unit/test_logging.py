from __future__ import annotations

import json
import logging
from typing import Any

from alertflow.logging import JSONFormatter, _resolve_log_level, set_correlation_id


def _record(**extra: Any) -> logging.LogRecord:
    record = logging.LogRecord(
        name="alertflow.test", level=logging.WARNING, pathname=__file__, lineno=1,
        msg="alert %s rejected", args=("a-1",), exc_info=None,
    )
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def test_formatter_emits_structured_json() -> None:
    payload = json.loads(
        JSONFormatter("alertflow", "test").format(_record(reason="empty message"))
    )
    assert payload["level"] == "WARNING"
    assert payload["component"] == "alertflow.test"
    assert payload["message"] == "alert a-1 rejected"
    assert payload["service"] == "alertflow"
    assert payload["environment"] == "test"
    assert payload["reason"] == "empty message"
    assert payload["timestamp"].endswith("Z")
    assert "correlation_id" in payload


def test_formatter_attaches_bound_correlation_id() -> None:
    set_correlation_id("batch-42")
    payload = json.loads(JSONFormatter("alertflow", "test").format(_record()))
    assert payload["correlation_id"] == "batch-42"


def test_level_defaults_follow_environment(set_env: Any) -> None:
    set_env(ALERTFLOW_ENVIRONMENT="production")
    assert _resolve_log_level() == logging.INFO
    set_env(ALERTFLOW_ENVIRONMENT="development")
    assert _resolve_log_level() == logging.DEBUG


def test_explicit_log_level_overrides_environment(set_env: Any) -> None:
    set_env(ALERTFLOW_ENVIRONMENT="production", ALERTFLOW_LOG_LEVEL="error")
    assert _resolve_log_level() == logging.ERROR
