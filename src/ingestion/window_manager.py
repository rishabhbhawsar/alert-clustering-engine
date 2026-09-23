"""
Sliding window buffer for the Alert Aggregation & Incident Clustering
Engine. Maintains a bounded, time-ordered in-memory buffer of validated
RawAlert instances, evicting anything that ages out of the configured
sliding_window_seconds threshold before each snapshot is served.
"""

from __future__ import annotations

import threading
from collections import deque
from datetime import datetime, timedelta, timezone

from src.core.config import StreamWindowSettings, get_settings
from src.core.logging import get_logger
from src.models.schemas import RawAlert

logger = get_logger("ingestion.window_manager")


class SlidingWindowManager:
    """
    Thread-safe temporal buffer. Alerts are stored in timestamp order;
    eviction and snapshot are both O(k) in the number of expired/active
    records, never O(n) over the full historical backlog.
    """

    def __init__(self, settings: StreamWindowSettings | None = None) -> None:
        self._settings = settings or get_settings().stream_window
        self._buffer: deque[RawAlert] = deque()
        self._lock = threading.RLock()
        self._total_ingested = 0
        self._total_evicted = 0
        self._total_dropped_capacity = 0

    @property
    def window_seconds(self) -> int:
        return self._settings.sliding_window_seconds

    def add_alert(self, alert: RawAlert) -> None:
        """Inserts a validated alert in timestamp order and enforces capacity."""
        with self._lock:
            self._insert_sorted(alert)
            self._total_ingested += 1
            self._enforce_capacity()

    def _insert_sorted(self, alert: RawAlert) -> None:
        # Fast path: near-monotonic arrival order is the common case.
        if not self._buffer or alert.timestamp >= self._buffer[-1].timestamp:
            self._buffer.append(alert)
            return
        # Slow path: out-of-order arrival, linear scan from the tail.
        position = len(self._buffer)
        for existing in reversed(self._buffer):
            if existing.timestamp <= alert.timestamp:
                break
            position -= 1
        self._buffer.insert(position, alert)

    def _enforce_capacity(self) -> None:
        max_backlog = self._settings.max_alert_backlog
        while len(self._buffer) > max_backlog:
            self._buffer.popleft()
            self._total_dropped_capacity += 1
            logger.warning(
                "alert dropped: max_alert_backlog exceeded",
                extra={"max_alert_backlog": max_backlog},
            )

    def _evict_expired(self, reference_time: datetime | None = None) -> int:
        """Removes alerts older than the sliding window; caller must hold the lock."""
        now = reference_time or datetime.now(timezone.utc)
        cutoff = now - timedelta(seconds=self._settings.sliding_window_seconds)
        evicted = 0
        while self._buffer and self._buffer[0].timestamp < cutoff:
            self._buffer.popleft()
            evicted += 1
        if evicted:
            self._total_evicted += evicted
            logger.debug(
                "evicted expired alerts from sliding window",
                extra={"evicted_count": evicted, "cutoff": cutoff.isoformat()},
            )
        return evicted

    def snapshot(self, reference_time: datetime | None = None) -> tuple[RawAlert, ...]:
        """
        Atomically evicts expired entries and returns the current active
        window contents as an immutable tuple, safe for downstream
        vectorization without holding the buffer lock.
        """
        with self._lock:
            self._evict_expired(reference_time)
            return tuple(self._buffer)

    def window_bounds(
        self, reference_time: datetime | None = None
    ) -> tuple[datetime, datetime]:
        """Returns (window_start, window_end) for the current active window."""
        now = reference_time or datetime.now(timezone.utc)
        window_start = now - timedelta(seconds=self._settings.sliding_window_seconds)
        return window_start, now

    def size(self) -> int:
        with self._lock:
            self._evict_expired()
            return len(self._buffer)

    def clear(self) -> None:
        with self._lock:
            self._buffer.clear()

    @property
    def stats(self) -> dict[str, int]:
        with self._lock:
            return {
                "total_ingested": self._total_ingested,
                "total_evicted": self._total_evicted,
                "total_dropped_capacity": self._total_dropped_capacity,
                "current_size": len(self._buffer),
            }