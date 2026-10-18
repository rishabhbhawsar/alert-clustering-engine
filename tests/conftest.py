from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from alertflow.config.settings import get_settings
from alertflow.models.alert import Alert


@pytest.fixture(autouse=True)
def _isolated_settings(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> Iterator[None]:
    import os

    for key in list(os.environ):
        if key.startswith("ALERTFLOW_"):
            monkeypatch.delenv(key)
    # Run from an empty directory so a developer's .env cannot leak into tests.
    monkeypatch.chdir(tmp_path)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def set_env(monkeypatch: pytest.MonkeyPatch) -> Callable[..., None]:
    def _set(**env: str) -> None:
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        get_settings.cache_clear()

    return _set


@pytest.fixture
def make_payload() -> Callable[..., dict[str, Any]]:
    def _make(**overrides: Any) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "alert_id": "a-1",
            "timestamp": datetime.now(timezone.utc) - timedelta(seconds=30),
            "service_name": "payment-api",
            "environment": "production",
            "severity": "ERROR",
            "title": "Upstream timeout",
            "message": "POST /charge exceeded 5000ms deadline",
        }
        payload.update(overrides)
        return payload

    return _make


@pytest.fixture
def make_alert(make_payload: Callable[..., dict[str, Any]]) -> Callable[..., Alert]:
    def _make(**overrides: Any) -> Alert:
        return Alert(**make_payload(**overrides))

    return _make
