"""
Output contracts: ClusterSummary, Incident, and IncidentDetail.
Incident exposes summary-derived values as computed fields so there is a
single source of truth for severity, services, and time range.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Self
from uuid import UUID, uuid4

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    PositiveInt,
    computed_field,
    model_validator,
)

from alertflow.models.alert import Alert, Severity

MAX_TOP_KEYWORDS = 20


class ClusterSummary(BaseModel):
    """Deterministic, non-LLM description of one cluster of alerts."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    alert_count: int = Field(ge=1)
    severity_distribution: dict[Severity, PositiveInt]
    services: tuple[str, ...] = Field(min_length=1)
    environments: tuple[str, ...] = Field(min_length=1)
    time_start: AwareDatetime
    time_end: AwareDatetime
    top_keywords: tuple[str, ...] = Field(default=(), max_length=MAX_TOP_KEYWORDS)
    representative_alert_ids: tuple[str, ...] = Field(min_length=1)
    summary_text: str = Field(min_length=1, max_length=2048)

    @property
    def highest_severity(self) -> Severity:
        return Severity.highest(self.severity_distribution)

    @model_validator(mode="after")
    def _check_invariants(self) -> Self:
        if sum(self.severity_distribution.values()) != self.alert_count:
            raise ValueError("severity_distribution counts must sum to alert_count")
        if self.time_end < self.time_start:
            raise ValueError("time_end cannot precede time_start")
        reps = self.representative_alert_ids
        if len(set(reps)) != len(reps):
            raise ValueError("representative_alert_ids must be unique")
        if len(reps) > self.alert_count:
            raise ValueError("representative_alert_ids cannot exceed alert_count")
        return self


class Incident(BaseModel):
    """A persisted cluster of related alerts, referenced by alert id."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    incident_id: UUID = Field(default_factory=uuid4)
    created_at: AwareDatetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    member_alert_ids: tuple[str, ...] = Field(min_length=1)
    summary: ClusterSummary

    @computed_field  # type: ignore[prop-decorator]
    @property
    def severity(self) -> Severity:
        return self.summary.highest_severity

    @computed_field  # type: ignore[prop-decorator]
    @property
    def alert_count(self) -> int:
        return len(self.member_alert_ids)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def services(self) -> tuple[str, ...]:
        return self.summary.services

    @computed_field  # type: ignore[prop-decorator]
    @property
    def first_seen(self) -> datetime:
        return self.summary.time_start

    @computed_field  # type: ignore[prop-decorator]
    @property
    def last_seen(self) -> datetime:
        return self.summary.time_end

    @model_validator(mode="after")
    def _check_membership(self) -> Self:
        members = self.member_alert_ids
        if len(set(members)) != len(members):
            raise ValueError("member_alert_ids must be unique")
        if len(members) != self.summary.alert_count:
            raise ValueError("summary.alert_count must equal len(member_alert_ids)")
        if not set(self.summary.representative_alert_ids) <= set(members):
            raise ValueError("representative_alert_ids must be members of the incident")
        return self


class IncidentDetail(Incident):
    """Incident with hydrated member alerts for GET /incidents/{id}."""

    alerts: tuple[Alert, ...] = Field(min_length=1)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def representative_alerts(self) -> tuple[Alert, ...]:
        by_id = {a.alert_id: a for a in self.alerts}
        return tuple(by_id[i] for i in self.summary.representative_alert_ids)

    @classmethod
    def from_incident(cls, incident: Incident, alerts: tuple[Alert, ...]) -> Self:
        return cls(
            incident_id=incident.incident_id,
            created_at=incident.created_at,
            member_alert_ids=incident.member_alert_ids,
            summary=incident.summary,
            alerts=alerts,
        )

    @model_validator(mode="after")
    def _check_hydration(self) -> Self:
        if {a.alert_id for a in self.alerts} != set(self.member_alert_ids):
            raise ValueError("alerts must match member_alert_ids exactly")
        start, end = self.summary.time_start, self.summary.time_end
        if any(not (start <= a.timestamp <= end) for a in self.alerts):
            raise ValueError("member alert timestamps must fall within the summary time range")
        return self
