from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError

from alertflow.models.alert import Severity
from alertflow.models.notification import Notification, NotificationChannel, NotificationStatus


def _make(**overrides: Any) -> Notification:
    payload: dict[str, Any] = {
        "incident_id": uuid4(),
        "severity": Severity.CRITICAL,
        "channel": NotificationChannel.LOG,
        "subject": "CRITICAL: payment-api incident",
        "body": "3 alerts grouped into one incident",
    }
    payload.update(overrides)
    return Notification(**payload)


def test_defaults_are_pending_with_zero_attempts() -> None:
    n = _make()
    assert n.status is NotificationStatus.PENDING
    assert n.attempts == 0
    assert n.sent_at is None


def test_sent_requires_sent_at() -> None:
    with pytest.raises(ValidationError, match="sent_at"):
        _make(status=NotificationStatus.SENT)


def test_sent_with_timestamp_is_valid() -> None:
    n = _make(status=NotificationStatus.SENT, sent_at=datetime.now(timezone.utc) + timedelta(seconds=1))
    assert n.status is NotificationStatus.SENT


def test_sent_cannot_carry_error() -> None:
    with pytest.raises(ValidationError, match="error"):
        _make(
            status=NotificationStatus.SENT,
            sent_at=datetime.now(timezone.utc) + timedelta(seconds=1),
            error="boom",
        )


def test_failed_requires_error() -> None:
    with pytest.raises(ValidationError, match="error"):
        _make(status=NotificationStatus.FAILED)


def test_sent_at_is_only_valid_for_sent_status() -> None:
    with pytest.raises(ValidationError, match="sent_at"):
        _make(status=NotificationStatus.SIMULATED, sent_at=datetime.now(timezone.utc))


def test_sent_at_cannot_precede_created_at() -> None:
    created = datetime.now(timezone.utc)
    with pytest.raises(ValidationError, match="precede"):
        _make(
            status=NotificationStatus.SENT,
            created_at=created,
            sent_at=created - timedelta(seconds=5),
        )


def test_simulated_is_valid_without_delivery_fields() -> None:
    assert _make(status=NotificationStatus.SIMULATED).sent_at is None


def test_negative_attempts_rejected() -> None:
    with pytest.raises(ValidationError):
        _make(attempts=-1)


def test_unknown_channel_rejected() -> None:
    with pytest.raises(ValidationError):
        _make(channel="carrier-pigeon")
