from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from pydantic import ValidationError

from alertflow.models.alert import Alert, AlertBatch
from alertflow.models.errors import (
    AlertFlowError,
    ClusteringError,
    DatabaseError,
    EmbeddingError,
    ErrorCode,
    ErrorResponse,
    InvalidSeverityError,
    MalformedAlertError,
    NotFoundError,
    NotificationError,
    alert_error_from_validation,
)


@pytest.mark.parametrize(
    ("exc_cls", "code", "status", "retryable"),
    [
        (MalformedAlertError, ErrorCode.MALFORMED_ALERT, 422, False),
        (InvalidSeverityError, ErrorCode.INVALID_SEVERITY, 422, False),
        (EmbeddingError, ErrorCode.EMBEDDING_FAILURE, 500, False),
        (ClusteringError, ErrorCode.CLUSTERING_FAILURE, 500, False),
        (DatabaseError, ErrorCode.DATABASE_FAILURE, 503, False),
        (NotificationError, ErrorCode.NOTIFICATION_FAILURE, 502, True),
        (NotFoundError, ErrorCode.NOT_FOUND, 404, False),
    ],
)
def test_exception_contract(
    exc_cls: type[AlertFlowError], code: ErrorCode, status: int, retryable: bool
) -> None:
    err = exc_cls("boom")
    assert err.code is code
    assert err.http_status == status
    assert err.retryable is retryable
    response = err.to_response()
    assert response.code is code
    assert response.message == "boom"


def test_invalid_severity_is_a_malformed_alert() -> None:
    assert issubclass(InvalidSeverityError, MalformedAlertError)


def test_retryable_can_be_overridden_per_instance() -> None:
    assert NotificationError("permanent", retryable=False).retryable is False
    assert DatabaseError("transient", retryable=True).retryable is True


def test_error_response_serializes_code_as_string() -> None:
    dumped = DatabaseError("down").to_response().model_dump(mode="json")
    assert dumped["code"] == "database_failure"
    assert ErrorResponse.model_validate(dumped).code is ErrorCode.DATABASE_FAILURE


def test_validation_error_with_bad_severity_maps_to_invalid_severity(
    make_payload: Callable[..., dict[str, Any]],
) -> None:
    with pytest.raises(ValidationError) as exc:
        Alert(**make_payload(severity="FATAL"))
    err = alert_error_from_validation(exc.value)
    assert isinstance(err, InvalidSeverityError)
    assert err.details[0].loc == ("severity",)


def test_other_validation_errors_map_to_malformed_alert(
    make_payload: Callable[..., dict[str, Any]],
) -> None:
    with pytest.raises(ValidationError) as exc:
        Alert(**make_payload(message="   "))
    err = alert_error_from_validation(exc.value)
    assert type(err) is MalformedAlertError


def test_batch_member_severity_error_keeps_full_location(
    make_payload: Callable[..., dict[str, Any]],
) -> None:
    with pytest.raises(ValidationError) as exc:
        AlertBatch(alerts=[make_payload(alert_id="a-1", severity="FATAL")])
    err = alert_error_from_validation(exc.value)
    assert isinstance(err, InvalidSeverityError)
    assert err.details[0].loc == ("alerts", 0, "severity")
