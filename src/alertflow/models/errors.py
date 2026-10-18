"""
Typed error surface: the wire-level ErrorResponse and the exception
hierarchy that maps domain failures to stable error codes.
"""

from __future__ import annotations

from collections.abc import Sequence
from enum import Enum
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, ValidationError


class ErrorCode(str, Enum):
    MALFORMED_ALERT = "malformed_alert"
    INVALID_SEVERITY = "invalid_severity"
    EMBEDDING_FAILURE = "embedding_failure"
    CLUSTERING_FAILURE = "clustering_failure"
    DATABASE_FAILURE = "database_failure"
    NOTIFICATION_FAILURE = "notification_failure"
    NOT_FOUND = "not_found"
    INTERNAL_ERROR = "internal_error"


class ErrorDetail(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    loc: tuple[str | int, ...] = ()
    message: str
    error_type: str | None = None


class ErrorResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    code: ErrorCode
    message: str
    retryable: bool = False
    details: tuple[ErrorDetail, ...] = ()


class AlertFlowError(Exception):
    code: ClassVar[ErrorCode] = ErrorCode.INTERNAL_ERROR
    http_status: ClassVar[int] = 500
    default_retryable: ClassVar[bool] = False

    def __init__(
        self,
        message: str,
        *,
        details: Sequence[ErrorDetail] = (),
        retryable: bool | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.details = tuple(details)
        self.retryable = self.default_retryable if retryable is None else retryable

    def to_response(self) -> ErrorResponse:
        return ErrorResponse(
            code=self.code,
            message=self.message,
            retryable=self.retryable,
            details=self.details,
        )


class MalformedAlertError(AlertFlowError):
    code = ErrorCode.MALFORMED_ALERT
    http_status = 422


class InvalidSeverityError(MalformedAlertError):
    code = ErrorCode.INVALID_SEVERITY


class EmbeddingError(AlertFlowError):
    code = ErrorCode.EMBEDDING_FAILURE
    http_status = 500


class ClusteringError(AlertFlowError):
    code = ErrorCode.CLUSTERING_FAILURE
    http_status = 500


class DatabaseError(AlertFlowError):
    code = ErrorCode.DATABASE_FAILURE
    http_status = 503


class NotificationError(AlertFlowError):
    code = ErrorCode.NOTIFICATION_FAILURE
    http_status = 502
    default_retryable = True


class NotFoundError(AlertFlowError):
    code = ErrorCode.NOT_FOUND
    http_status = 404


def alert_error_from_validation(exc: ValidationError) -> MalformedAlertError:
    """Maps a pydantic ValidationError from Alert/AlertBatch to a typed domain error."""
    details = tuple(
        ErrorDetail(loc=tuple(err["loc"]), message=err["msg"], error_type=err["type"])
        for err in exc.errors(include_url=False, include_context=False, include_input=False)
    )
    message = f"{len(details)} validation error(s) in alert payload"
    if any(d.loc and d.loc[-1] == "severity" for d in details):
        return InvalidSeverityError(message, details=details)
    return MalformedAlertError(message, details=details)
