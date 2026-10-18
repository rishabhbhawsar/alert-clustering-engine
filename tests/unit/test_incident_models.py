from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from pydantic import ValidationError

from alertflow.models.alert import Alert, Severity
from alertflow.models.incident import ClusterSummary, Incident, IncidentDetail


def _alerts(make_alert: Callable[..., Alert]) -> tuple[Alert, ...]:
    now = datetime.now(timezone.utc)
    return (
        make_alert(alert_id="a-1", severity="ERROR", timestamp=now - timedelta(seconds=90)),
        make_alert(alert_id="a-2", severity="ERROR", timestamp=now - timedelta(seconds=60)),
        make_alert(alert_id="a-3", severity="CRITICAL", timestamp=now - timedelta(seconds=30)),
    )


def _summary(alerts: tuple[Alert, ...], **overrides: Any) -> ClusterSummary:
    stamps = [a.timestamp for a in alerts]
    payload: dict[str, Any] = {
        "alert_count": len(alerts),
        "severity_distribution": dict(Counter(a.severity for a in alerts)),
        "services": tuple(sorted({a.service_name for a in alerts})),
        "environments": tuple(sorted({a.environment for a in alerts})),
        "time_start": min(stamps),
        "time_end": max(stamps),
        "top_keywords": ("timeout", "charge"),
        "representative_alert_ids": (alerts[0].alert_id,),
        "summary_text": "3 alerts across payment-api (1 CRITICAL, 2 ERROR)",
    }
    payload.update(overrides)
    return ClusterSummary(**payload)


def _incident(alerts: tuple[Alert, ...], **overrides: Any) -> Incident:
    payload: dict[str, Any] = {
        "member_alert_ids": tuple(a.alert_id for a in alerts),
        "summary": _summary(alerts),
    }
    payload.update(overrides)
    return Incident(**payload)


def test_summary_reports_highest_severity(make_alert: Callable[..., Alert]) -> None:
    assert _summary(_alerts(make_alert)).highest_severity is Severity.CRITICAL


def test_summary_serializes_distribution_with_string_keys(
    make_alert: Callable[..., Alert],
) -> None:
    dumped = _summary(_alerts(make_alert)).model_dump(mode="json")
    assert dumped["severity_distribution"] == {"ERROR": 2, "CRITICAL": 1}


@pytest.mark.parametrize(
    "overrides",
    [
        {"alert_count": 5},
        {"severity_distribution": {Severity.ERROR: 0, Severity.CRITICAL: 3}},
        {"representative_alert_ids": ("a-1", "a-1")},
        {"representative_alert_ids": ("a-1", "a-2", "a-3", "a-4")},
        {"services": ()},
        {"top_keywords": tuple(f"k{i}" for i in range(21))},
    ],
)
def test_summary_invariants(make_alert: Callable[..., Alert], overrides: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        _summary(_alerts(make_alert), **overrides)


def test_summary_rejects_inverted_time_range(make_alert: Callable[..., Alert]) -> None:
    alerts = _alerts(make_alert)
    with pytest.raises(ValidationError, match="time_end"):
        _summary(alerts, time_start=alerts[2].timestamp, time_end=alerts[0].timestamp)


def test_incident_exposes_derived_fields(make_alert: Callable[..., Alert]) -> None:
    incident = _incident(_alerts(make_alert))
    dumped = incident.model_dump(mode="json")
    assert dumped["severity"] == "CRITICAL"
    assert dumped["alert_count"] == 3
    assert dumped["services"] == ["payment-api"]
    assert dumped["first_seen"] < dumped["last_seen"]


def test_incident_ids_are_unique_and_created_at_is_aware(
    make_alert: Callable[..., Alert],
) -> None:
    alerts = _alerts(make_alert)
    a, b = _incident(alerts), _incident(alerts)
    assert a.incident_id != b.incident_id
    assert a.created_at.tzinfo is not None


def test_incident_member_count_must_match_summary(make_alert: Callable[..., Alert]) -> None:
    alerts = _alerts(make_alert)
    with pytest.raises(ValidationError, match="alert_count"):
        _incident(alerts, member_alert_ids=("a-1", "a-2"))


def test_incident_rejects_duplicate_members(make_alert: Callable[..., Alert]) -> None:
    with pytest.raises(ValidationError, match="unique"):
        _incident(_alerts(make_alert), member_alert_ids=("a-1", "a-1", "a-3"))


def test_incident_representatives_must_be_members(make_alert: Callable[..., Alert]) -> None:
    alerts = _alerts(make_alert)
    with pytest.raises(ValidationError, match="members"):
        _incident(alerts, summary=_summary(alerts, representative_alert_ids=("ghost",)))


def test_detail_hydrates_representative_alerts(make_alert: Callable[..., Alert]) -> None:
    alerts = _alerts(make_alert)
    detail = IncidentDetail.from_incident(_incident(alerts), alerts)
    assert [a.alert_id for a in detail.representative_alerts] == ["a-1"]
    assert "representative_alerts" in detail.model_dump(mode="json")


def test_detail_requires_alerts_to_match_members(make_alert: Callable[..., Alert]) -> None:
    alerts = _alerts(make_alert)
    with pytest.raises(ValidationError, match="match member_alert_ids"):
        IncidentDetail.from_incident(_incident(alerts), alerts[:2])


def test_detail_requires_alert_timestamps_inside_summary_range(
    make_alert: Callable[..., Alert],
) -> None:
    alerts = _alerts(make_alert)
    incident = _incident(alerts)
    outlier = make_alert(
        alert_id="a-3", severity="CRITICAL", timestamp=datetime.now(timezone.utc)
    )
    with pytest.raises(ValidationError, match="time range"):
        IncidentDetail.from_incident(incident, (alerts[0], alerts[1], outlier))
