"""
Ingress contracts for AlertFlow: the Severity gate, Alert, and AlertBatch.
Every external alert payload must pass through these models.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Annotated, Any, TypeAlias

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    StringConstraints,
    field_validator,
)

from alertflow.config.settings import get_settings

_SEVERITY_RANK = {"INFO": 0, "WARNING": 1, "ERROR": 2, "CRITICAL": 3}

MAX_METADATA_ENTRIES = 32
MAX_METADATA_KEY_CHARS = 64
MAX_METADATA_VALUE_CHARS = 512


class Severity(str, Enum):
    """Closed severity gate; values outside this set are rejected at ingress."""

    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"

    @property
    def rank(self) -> int:
        return _SEVERITY_RANK[self.value]

    @classmethod
    def highest(cls, severities: Iterable[Severity]) -> Severity:
        return max(severities, key=lambda s: s.rank)


AlertId: TypeAlias = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, min_length=1, max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    ),
]
ServiceName: TypeAlias = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, min_length=1, max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
    ),
]
# Case-insensitive pattern; lower-casing is applied in an after-validator.
EnvironmentName: TypeAlias = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, min_length=1, max_length=32,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$",
    ),
]
Title: TypeAlias = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=256)]
Message: TypeAlias = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=8192)]
StackTrace: TypeAlias = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=16384)]
MetadataValue: TypeAlias = StrictStr | StrictInt | StrictFloat | StrictBool


class Alert(BaseModel):
    """A single operational alert. Shallow-frozen: `metadata` is a plain dict."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    alert_id: AlertId
    timestamp: AwareDatetime
    service_name: ServiceName
    environment: EnvironmentName
    severity: Severity
    title: Title
    message: Message
    stack_trace: StackTrace | None = None
    metadata: dict[str, MetadataValue] | None = None

    @field_validator("severity", mode="before")
    @classmethod
    def _coerce_severity_case(cls, v: Any) -> Any:
        return v.strip().upper() if isinstance(v, str) else v

    @field_validator("environment")
    @classmethod
    def _lowercase_environment(cls, v: str) -> str:
        return v.lower()

    @field_validator("stack_trace", mode="before")
    @classmethod
    def _blank_stack_trace_is_absent(cls, v: Any) -> Any:
        return None if isinstance(v, str) and not v.strip() else v

    @field_validator("timestamp")
    @classmethod
    def _enforce_temporal_boundary(cls, v: datetime) -> datetime:
        cfg = get_settings().ingestion
        now = datetime.now(timezone.utc)
        if v > now + timedelta(seconds=cfg.max_future_skew_seconds):
            raise ValueError("timestamp exceeds permitted future clock skew")
        if v < now - timedelta(seconds=cfg.max_alert_age_seconds):
            raise ValueError("timestamp exceeds maximum accepted alert age")
        return v.astimezone(timezone.utc)

    @field_validator("metadata")
    @classmethod
    def _bound_metadata(cls, v: dict[str, MetadataValue] | None) -> dict[str, MetadataValue] | None:
        if v is None:
            return v
        if len(v) > MAX_METADATA_ENTRIES:
            raise ValueError(f"metadata exceeds {MAX_METADATA_ENTRIES} entries")
        for key, value in v.items():
            if not key or len(key) > MAX_METADATA_KEY_CHARS:
                raise ValueError(f"metadata key must be 1..{MAX_METADATA_KEY_CHARS} chars")
            if isinstance(value, str) and len(value) > MAX_METADATA_VALUE_CHARS:
                raise ValueError(
                    f"metadata value for {key!r} exceeds {MAX_METADATA_VALUE_CHARS} chars"
                )
        return v


class AlertBatch(BaseModel):
    """
    All-or-nothing batch contract: one invalid member fails the whole batch.
    Partial-acceptance semantics, if wanted, belong in the ingestion layer.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    alerts: tuple[Alert, ...] = Field(min_length=1)

    @field_validator("alerts")
    @classmethod
    def _bounded_and_unique(cls, v: tuple[Alert, ...]) -> tuple[Alert, ...]:
        max_size = get_settings().ingestion.max_batch_size
        if len(v) > max_size:
            raise ValueError(f"batch size {len(v)} exceeds max_batch_size={max_size}")
        seen: set[str] = set()
        duplicates: list[str] = []
        for alert in v:
            if alert.alert_id in seen and alert.alert_id not in duplicates:
                duplicates.append(alert.alert_id)
            seen.add(alert.alert_id)
        if duplicates:
            raise ValueError(f"duplicate alert_id in batch: {duplicates[:5]}")
        return v
