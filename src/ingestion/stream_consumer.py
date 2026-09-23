"""
Ingestion boundary for the Alert Aggregation & Incident Clustering
Engine. Converts raw, unstructured log payloads (str or dict) into
validated RawAlert instances, isolating malformed input from the
downstream streaming window pipeline.
"""

from __future__ import annotations

import asyncio
import json
import random
import re
import uuid
from collections.abc import AsyncIterator, AsyncIterable, Iterable
from datetime import datetime, timezone
from typing import Any

from pydantic import ValidationError

from src.core.logging import get_logger
from src.models.schemas import RawAlert, SeverityLevel

logger = get_logger("ingestion.stream_consumer")

# Fallback textual parser for unstructured log lines, e.g.:
# "2026-09-23T10:15:00Z host=db-primary-03 CRITICAL disk usage at 97%"
_LINE_PATTERN = re.compile(
    r"^(?P<timestamp>\S+)\s+host=(?P<host>\S+)\s+(?P<severity>INFO|WARNING|CRITICAL)\s+(?P<message>.+)$"
)

_DEMO_HOSTS = ("db-primary-03", "api-gw-01", "cache-node-07", "web-lb-02")
_DEMO_SEVERITIES = (SeverityLevel.INFO, SeverityLevel.WARNING, SeverityLevel.CRITICAL)
_DEMO_MESSAGES = (
    "disk usage at 97%",
    "connection pool exhausted",
    "elevated p99 latency detected",
    "memory pressure threshold breached",
    "healthcheck timeout on downstream dependency",
)


class MalformedAlertError(Exception):
    """Raised internally when a raw payload cannot be structurally parsed."""


class AlertStreamConsumer:
    """
    Parses heterogeneous raw alert payloads into RawAlert contracts and
    exposes an async iteration surface for high-velocity streaming
    ingestion. Parse failures are logged and dropped; the stream never
    raises on a single bad record.
    """

    def __init__(self) -> None:
        self._parsed_count = 0
        self._rejected_count = 0

    @property
    def parsed_count(self) -> int:
        return self._parsed_count

    @property
    def rejected_count(self) -> int:
        return self._rejected_count

    def parse(self, raw: str | dict[str, Any]) -> RawAlert | None:
        """
        Attempts to coerce a single raw payload into a RawAlert. Returns
        None (and logs) on any structural or validation failure rather
        than propagating, so the ingestion thread is never interrupted.
        """
        try:
            fields = self._extract_fields(raw)
            alert = RawAlert(**fields)
        except (MalformedAlertError, ValidationError, ValueError) as exc:
            self._rejected_count += 1
            logger.warning(
                "alert rejected at ingestion boundary",
                extra={"raw_payload": self._truncate(raw), "reason": str(exc)},
            )
            return None

        self._parsed_count += 1
        return alert

    def _extract_fields(self, raw: str | dict[str, Any]) -> dict[str, Any]:
        if isinstance(raw, dict):
            return self._extract_from_dict(raw)
        if isinstance(raw, str):
            return self._extract_from_line(raw)
        raise MalformedAlertError(f"unsupported payload type: {type(raw).__name__}")

    @staticmethod
    def _extract_from_dict(raw: dict[str, Any]) -> dict[str, Any]:
        try:
            return {
                "message": raw["message"],
                "host_source": raw["host_source"],
                "severity": raw["severity"],
                "timestamp": raw["timestamp"],
            }
        except KeyError as exc:
            raise MalformedAlertError(f"missing required field: {exc}") from exc

    @staticmethod
    def _extract_from_line(raw: str) -> dict[str, Any]:
        stripped = raw.strip()
        if not stripped:
            raise MalformedAlertError("empty log line")

        # JSON-encoded line takes precedence over the delimited fallback.
        if stripped.startswith("{"):
            try:
                return AlertStreamConsumer._extract_from_dict(json.loads(stripped))
            except json.JSONDecodeError as exc:
                raise MalformedAlertError(f"invalid JSON payload: {exc}") from exc

        match = _LINE_PATTERN.match(stripped)
        if not match:
            raise MalformedAlertError("log line does not match expected grammar")

        return {
            "message": match.group("message"),
            "host_source": match.group("host"),
            "severity": match.group("severity"),
            "timestamp": match.group("timestamp"),
        }

    @staticmethod
    def _truncate(raw: str | dict[str, Any], limit: int = 256) -> str:
        text = raw if isinstance(raw, str) else json.dumps(raw, default=str)
        return text if len(text) <= limit else text[:limit] + "...<truncated>"

    async def consume(
        self, source: AsyncIterable[str | dict[str, Any]]
    ) -> AsyncIterator[RawAlert]:
        """
        Wraps an arbitrary async payload source (queue, socket reader,
        Kafka consumer) and yields only successfully validated alerts
        for downstream window aggregation.
        """
        async for raw in source:
            alert = self.parse(raw)
            if alert is not None:
                yield alert

    def consume_batch(self, raws: Iterable[str | dict[str, Any]]) -> list[RawAlert]:
        """Synchronous batch entry point for backfill/replay scenarios."""
        return [a for a in (self.parse(raw) for raw in raws) if a is not None]

    async def simulate_ingestion(
        self, rate_per_second: float = 50.0, jitter_ratio: float = 0.2
    ) -> AsyncIterator[RawAlert]:
        """
        Synthetic high-velocity source for portfolio/demo/load-testing
        contexts. Emits validated RawAlert instances at the target rate
        with randomized inter-arrival jitter.
        """
        interval = 1.0 / rate_per_second
        while True:
            payload = self._generate_synthetic_payload()
            alert = self.parse(payload)
            if alert is not None:
                yield alert
            jitter = interval * random.uniform(-jitter_ratio, jitter_ratio)
            await asyncio.sleep(max(0.0, interval + jitter))

    @staticmethod
    def _generate_synthetic_payload() -> dict[str, Any]:
        return {
            "message": f"{random.choice(_DEMO_MESSAGES)} [ref={uuid.uuid4().hex[:8]}]",
            "host_source": random.choice(_DEMO_HOSTS),
            "severity": random.choice(_DEMO_SEVERITIES),
            "timestamp": datetime.now(timezone.utc),
        }