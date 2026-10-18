"""Notification record contract. Delivery logic is deferred past the clustering MVP."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Self
from uuid import UUID, uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from alertflow.models.alert import Severity


class NotificationChannel(str, Enum):
    LOG = "log"
    EMAIL = "email"
    WEBHOOK = "webhook"
    PAGER = "pager"


class NotificationStatus(str, Enum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"
    SIMULATED = "simulated"


class Notification(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    notification_id: UUID = Field(default_factory=uuid4)
    incident_id: UUID
    severity: Severity
    channel: NotificationChannel
    status: NotificationStatus = NotificationStatus.PENDING
    subject: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=4096)
    attempts: int = Field(default=0, ge=0)
    created_at: AwareDatetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    sent_at: AwareDatetime | None = None
    error: str | None = Field(default=None, min_length=1, max_length=1024)

    @model_validator(mode="after")
    def _check_status_consistency(self) -> Self:
        if self.status is NotificationStatus.SENT:
            if self.sent_at is None:
                raise ValueError("sent notifications require sent_at")
            if self.error is not None:
                raise ValueError("sent notifications cannot carry an error")
        elif self.sent_at is not None:
            raise ValueError("sent_at is only valid for sent notifications")
        if self.status is NotificationStatus.FAILED and self.error is None:
            raise ValueError("failed notifications require an error message")
        if self.sent_at is not None and self.sent_at < self.created_at:
            raise ValueError("sent_at cannot precede created_at")
        return self
