from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from alertflow.config.settings import (
    ClusteringAlgorithm,
    ClusteringSettings,
    DeduplicationSettings,
    DistanceMetric,
    IngestionSettings,
    Settings,
    StorageSettings,
    TfidfSettings,
    get_settings,
)


def test_defaults_match_registry_contract() -> None:
    s = Settings()
    assert s.clustering.dbscan_eps == 0.3
    assert s.clustering.dbscan_min_samples == 2
    assert s.clustering.distance_metric is DistanceMetric.COSINE
    assert s.clustering.algorithm is ClusteringAlgorithm.HDBSCAN
    assert s.dedup.window_seconds == 300
    assert s.tfidf.max_vocabulary_size == 20_000
    assert s.embedding.model_name == "sentence-transformers/all-MiniLM-L6-v2"
    assert s.environment == "production"


def test_settings_are_frozen() -> None:
    s = Settings()
    with pytest.raises(ValidationError):
        s.clustering.dbscan_eps = 0.9  # type: ignore[misc]
    with pytest.raises(ValidationError):
        s.service_name = "other"  # type: ignore[misc]


def test_get_settings_is_cached_singleton() -> None:
    assert get_settings() is get_settings()


def test_env_override_reaches_nested_group(set_env: Any) -> None:
    set_env(ALERTFLOW_CLUSTER_DBSCAN_EPS="0.5", ALERTFLOW_EMBED_MODEL_NAME="custom/model")
    s = get_settings()
    assert s.clustering.dbscan_eps == 0.5
    assert s.embedding.model_name == "custom/model"


def test_dotenv_populates_nested_groups_and_ignores_unrelated_keys() -> None:
    Path(".env").write_text(
        "ALERTFLOW_CLUSTER_DBSCAN_EPS=0.42\nALERTFLOW_DEDUP_WINDOW_SECONDS=120\nUNRELATED=1\n"
    )
    s = Settings()
    assert s.clustering.dbscan_eps == 0.42
    assert s.dedup.window_seconds == 120


def test_log_level_is_case_insensitive() -> None:
    assert Settings(log_level="debug").log_level == "DEBUG"  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("cls", "kwargs"),
    [
        (ClusteringSettings, {"dbscan_eps": 0}),
        (ClusteringSettings, {"dbscan_eps": 2.5}),
        (ClusteringSettings, {"dbscan_min_samples": 0}),
        (ClusteringSettings, {"hdbscan_min_cluster_size": 1}),
        (ClusteringSettings, {"hdbscan_selection_method": "bogus"}),
        (ClusteringSettings, {"kmeans_n_clusters": 0}),
        (DeduplicationSettings, {"window_seconds": 0}),
        (DeduplicationSettings, {"window_seconds": 86_401}),
        (DeduplicationSettings, {"near_duplicate_similarity": 1.5}),
        (TfidfSettings, {"max_vocabulary_size": 0}),
        (TfidfSettings, {"ngram_range_min": 3, "ngram_range_max": 2}),
        (IngestionSettings, {"max_batch_size": 0}),
        (StorageSettings, {"database_url": "postgresql://user@host/db"}),
        (Settings, {"environment": "staging"}),
        (Settings, {"log_level": "LOUD"}),
    ],
)
def test_invalid_values_are_rejected(cls: type, kwargs: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        cls(**kwargs)
