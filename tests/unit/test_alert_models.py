from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from pydantic import ValidationError

from alertflow.models.alert import Alert, AlertBatch, Severity


def _locs(exc_info: pytest.ExceptionInfo[ValidationError]) -> set[tuple[str | int, ...]]:
    return {tuple(e["loc"]) for e in exc_info.value.errors()}


def test_valid_alert_round_trips_through_json(make_alert: Callable[..., Alert]) -> None:
    alert = make_alert()
    assert alert.severity is Severity.ERROR
    assert Alert.model_validate_json(alert.model_dump_json()) == alert


def test_severity_gate_is_the_closed_four_value_set() -> None:
    assert {s.value for s in Severity} == {"INFO", "WARNING", "ERROR", "CRITICAL"}
    assert Severity.INFO.rank < Severity.WARNING.rank < Severity.ERROR.rank < Severity.CRITICAL.rank
    assert Severity.highest([Severity.INFO, Severity.CRITICAL, Severity.ERROR]) is Severity.CRITICAL


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("INFO", Severity.INFO), ("warning", Severity.WARNING), (" Error ", Severity.ERROR), ("critical", Severity.CRITICAL)],
)
def test_severity_accepts_case_and_whitespace_variants(
    make_alert: Callable[..., Alert], raw: str, expected: Severity
) -> None:
    assert make_alert(severity=raw).severity is expected


@pytest.mark.parametrize("raw", ["FATAL", "", "   ", 3, None, ["ERROR"]])
def test_severity_rejects_values_outside_the_gate(
    make_payload: Callable[..., dict[str, Any]], raw: Any
) -> None:
    with pytest.raises(ValidationError) as exc:
        Alert(**make_payload(severity=raw))
    assert ("severity",) in _locs(exc)


def test_environment_is_lowercased(make_alert: Callable[..., Alert]) -> None:
    assert make_alert(environment="PROD").environment == "prod"


@pytest.mark.parametrize("field", ["title", "message"])
@pytest.mark.parametrize("blank", ["", "   \t\n"])
def test_blank_text_fields_are_rejected(
    make_payload: Callable[..., dict[str, Any]], field: str, blank: str
) -> None:
    with pytest.raises(ValidationError) as exc:
        Alert(**make_payload(**{field: blank}))
    assert (field,) in _locs(exc)


def test_message_length_cap(make_payload: Callable[..., dict[str, Any]]) -> None:
    with pytest.raises(ValidationError) as exc:
        Alert(**make_payload(message="x" * 8193))
    assert ("message",) in _locs(exc)


@pytest.mark.parametrize("name", ["bad name!", "", "-leading"])
def test_service_name_is_validated(make_payload: Callable[..., dict[str, Any]], name: str) -> None:
    with pytest.raises(ValidationError) as exc:
        Alert(**make_payload(service_name=name))
    assert ("service_name",) in _locs(exc)


@pytest.mark.parametrize("env", ["pro d", ""])
def test_environment_is_validated(make_payload: Callable[..., dict[str, Any]], env: str) -> None:
    with pytest.raises(ValidationError) as exc:
        Alert(**make_payload(environment=env))
    assert ("environment",) in _locs(exc)


def test_missing_service_name_is_rejected(make_payload: Callable[..., dict[str, Any]]) -> None:
    payload = make_payload()
    payload.pop("service_name")
    with pytest.raises(ValidationError) as exc:
        Alert(**payload)
    assert ("service_name",) in _locs(exc)


def test_legacy_host_source_field_is_rejected(make_payload: Callable[..., dict[str, Any]]) -> None:
    with pytest.raises(ValidationError) as exc:
        Alert(**make_payload(host_source="db-primary-03"))
    assert ("host_source",) in _locs(exc)


def test_naive_timestamp_is_rejected(make_payload: Callable[..., dict[str, Any]]) -> None:
    with pytest.raises(ValidationError) as exc:
        Alert(**make_payload(timestamp=datetime.now()))
    assert ("timestamp",) in _locs(exc)


def test_timestamp_older_than_max_age_is_rejected(
    make_payload: Callable[..., dict[str, Any]],
) -> None:
    stale = datetime.now(timezone.utc) - timedelta(seconds=86_400 + 60)
    with pytest.raises(ValidationError) as exc:
        Alert(**make_payload(timestamp=stale))
    assert ("timestamp",) in _locs(exc)


def test_timestamp_beyond_future_skew_is_rejected(
    make_payload: Callable[..., dict[str, Any]],
) -> None:
    future = datetime.now(timezone.utc) + timedelta(seconds=600)
    with pytest.raises(ValidationError) as exc:
        Alert(**make_payload(timestamp=future))
    assert ("timestamp",) in _locs(exc)


def test_timestamp_within_future_skew_is_accepted(make_alert: Callable[..., Alert]) -> None:
    make_alert(timestamp=datetime.now(timezone.utc) + timedelta(seconds=60))


def test_timestamp_is_normalized_to_utc(make_alert: Callable[..., Alert]) -> None:
    ist = timezone(timedelta(hours=5, minutes=30))
    alert = make_alert(timestamp=datetime.now(ist) - timedelta(seconds=10))
    assert alert.timestamp.utcoffset() == timedelta(0)


def test_max_alert_age_is_configurable(
    set_env: Any, make_payload: Callable[..., dict[str, Any]]
) -> None:
    set_env(ALERTFLOW_INGEST_MAX_ALERT_AGE_SECONDS="60")
    with pytest.raises(ValidationError) as exc:
        Alert(**make_payload(timestamp=datetime.now(timezone.utc) - timedelta(seconds=120)))
    assert ("timestamp",) in _locs(exc)


def test_blank_stack_trace_is_treated_as_absent(make_alert: Callable[..., Alert]) -> None:
    assert make_alert(stack_trace="  \n ").stack_trace is None


def test_metadata_accepts_scalars_and_preserves_types(make_alert: Callable[..., Alert]) -> None:
    alert = make_alert(metadata={"region": "us-east-1", "attempt": 3, "ratio": 0.5, "canary": True})
    assert alert.metadata is not None
    assert alert.metadata["canary"] is True
    assert type(alert.metadata["attempt"]) is int


def test_metadata_rejects_nested_values(make_payload: Callable[..., dict[str, Any]]) -> None:
    with pytest.raises(ValidationError) as exc:
        Alert(**make_payload(metadata={"nested": {"a": 1}}))
    assert any(loc[:2] == ("metadata", "nested") for loc in _locs(exc))


@pytest.mark.parametrize(
    "metadata",
    [
        {f"k{i}": "v" for i in range(33)},
        {"k" * 65: "v"},
        {"k": "v" * 513},
    ],
)
def test_metadata_bounds(make_payload: Callable[..., dict[str, Any]], metadata: dict[str, str]) -> None:
    with pytest.raises(ValidationError) as exc:
        Alert(**make_payload(metadata=metadata))
    assert ("metadata",) in _locs(exc)


def test_alert_is_frozen(make_alert: Callable[..., Alert]) -> None:
    with pytest.raises(ValidationError):
        make_alert().title = "mutated"  # type: ignore[misc]


def test_batch_accepts_unique_alerts(make_payload: Callable[..., dict[str, Any]]) -> None:
    batch = AlertBatch(alerts=[make_payload(alert_id=f"a-{i}") for i in range(3)])
    assert len(batch.alerts) == 3


def test_empty_batch_is_rejected() -> None:
    with pytest.raises(ValidationError) as exc:
        AlertBatch(alerts=[])
    assert ("alerts",) in _locs(exc)


def test_batch_rejects_duplicate_alert_ids(make_payload: Callable[..., dict[str, Any]]) -> None:
    with pytest.raises(ValidationError, match="duplicate alert_id"):
        AlertBatch(alerts=[make_payload(alert_id="dup"), make_payload(alert_id="dup")])


def test_batch_size_limit_is_configurable(
    set_env: Any, make_payload: Callable[..., dict[str, Any]]
) -> None:
    set_env(ALERTFLOW_INGEST_MAX_BATCH_SIZE="2")
    with pytest.raises(ValidationError, match="max_batch_size"):
        AlertBatch(alerts=[make_payload(alert_id=f"a-{i}") for i in range(3)])


def test_one_invalid_member_fails_the_whole_batch(
    make_payload: Callable[..., dict[str, Any]],
) -> None:
    with pytest.raises(ValidationError) as exc:
        AlertBatch(
            alerts=[make_payload(alert_id="a-1"), make_payload(alert_id="a-2", severity="FATAL")]
        )
    assert ("alerts", 1, "severity") in _locs(exc)
