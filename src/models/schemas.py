"""
Invariant data contracts for the Alert Aggregation & Incident Clustering
Engine. All ingress boundaries validate through these models; no
downstream component should accept unvalidated dict/JSON payloads.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Ingress temporal boundary: alerts older than this or timestamped
# meaningfully in the future are rejected as malformed/clock-skewed.
MAX_ALERT_AGE = timedelta(hours=24)
MAX_FUTURE_SKEW = timedelta(minutes=5)


class SeverityLevel(str, Enum):
    """Closed severity gate. No values outside this set are accepted."""

    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


class PriorityClassification(str, Enum):
    """Operational priority assigned to a resolved incident cluster."""

    P0 = "P0"
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"


class RawAlert(BaseModel):
    """A single unstructured infrastructure alert prior to clustering."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    alert_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    message: str = Field(..., min_length=1, max_length=8192)
    host_source: str = Field(..., min_length=1, max_length=255)
    severity: SeverityLevel
    timestamp: datetime

    @field_validator("message")
    @classmethod
    def _reject_blank_message(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("alert message cannot be empty or whitespace-only")
        return v

    @field_validator("timestamp")
    @classmethod
    def _enforce_temporal_boundary(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            v = v.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        if v > now + MAX_FUTURE_SKEW:
            raise ValueError("timestamp exceeds permitted future clock skew")
        if v < now - MAX_ALERT_AGE:
            raise ValueError("timestamp exceeds maximum retained alert age")
        return v


class ClusteredIncident(BaseModel):
    """
    Output of streaming density clustering: a set of raw alerts collapsed
    into a single actionable incident with an identified root-cause
    driver and assigned operational priority.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    incident_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    member_alerts: tuple[RawAlert, ...] = Field(..., min_length=1)
    centroid_driver_id: uuid.UUID = Field(
        ...,
        description="alert_id of the alert closest to the dense cluster centroid; treated as root-cause driver.",
    )
    priority: PriorityClassification
    cluster_density_score: float = Field(
        ...,
        ge=0.0,
        description="Density metric (e.g. mean intra-cluster similarity) backing the grouping decision.",
    )
    window_start: datetime
    window_end: datetime

    @model_validator(mode="after")
    def _validate_centroid_membership(self) -> "ClusteredIncident":
        member_ids = {alert.alert_id for alert in self.member_alerts}
        if self.centroid_driver_id not in member_ids:
            raise ValueError("centroid_driver_id must reference a member alert")
        return self

    @model_validator(mode="after")
    def _validate_window_ordering(self) -> "ClusteredIncident":
        if self.window_end < self.window_start:
            raise ValueError("window_end cannot precede window_start")
        return self

    @property
    def alert_count(self) -> int:
        return len(self.member_alerts)