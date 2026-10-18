"""
Immutable configuration registry for AlertFlow.

Every settings group is a frozen pydantic-settings model resolved from
environment variables (group-specific ALERTFLOW_* prefix), then a `.env`
file in the working directory, then the defaults declared here.
"""

from __future__ import annotations

from enum import Enum
from functools import lru_cache
from typing import Literal, Self

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LogLevelName = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class DistanceMetric(str, Enum):
    COSINE = "cosine"
    EUCLIDEAN = "euclidean"
    MANHATTAN = "manhattan"


class EmbeddingProvider(str, Enum):
    SENTENCE_TRANSFORMER = "sentence_transformer"
    FAKE = "fake"


class ClusteringAlgorithm(str, Enum):
    HDBSCAN = "hdbscan"
    TFIDF_DBSCAN = "tfidf_dbscan"
    TFIDF_KMEANS = "tfidf_kmeans"


class _FrozenSettings(BaseSettings):
    # extra="ignore": the shared .env legitimately carries keys owned by
    # sibling settings groups, so unknown keys must not fail validation.
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )


class IngestionSettings(_FrozenSettings):
    """Ingress safety bounds enforced by the Alert / AlertBatch models."""

    model_config = SettingsConfigDict(env_prefix="ALERTFLOW_INGEST_")

    max_alert_age_seconds: int = Field(default=86_400, gt=0)
    max_future_skew_seconds: int = Field(default=300, ge=0)
    max_batch_size: int = Field(default=5_000, gt=0, le=100_000)


class DeduplicationSettings(_FrozenSettings):
    """Time-bucketed exact / near-duplicate suppression."""

    model_config = SettingsConfigDict(env_prefix="ALERTFLOW_DEDUP_")

    window_seconds: int = Field(default=300, gt=0, le=86_400)
    near_duplicate_similarity: float = Field(default=0.95, gt=0.0, le=1.0)


class EmbeddingSettings(_FrozenSettings):
    """Embedding provider selection and encoding parameters."""

    model_config = SettingsConfigDict(env_prefix="ALERTFLOW_EMBED_")

    provider: EmbeddingProvider = EmbeddingProvider.SENTENCE_TRANSFORMER
    model_name: str = Field(default="sentence-transformers/all-MiniLM-L6-v2", min_length=1)
    batch_size: int = Field(default=64, gt=0)
    normalize: bool = True
    # JSON array when set via env, e.g. ALERTFLOW_EMBED_METADATA_KEYS='["component"]'.
    metadata_keys: tuple[str, ...] = ()


class TfidfSettings(_FrozenSettings):
    """Bounded-vocabulary TF-IDF encoding used by the baseline clusterers."""

    model_config = SettingsConfigDict(env_prefix="ALERTFLOW_TFIDF_")

    max_vocabulary_size: int = Field(default=20_000, gt=0)
    ngram_range_min: int = Field(default=1, ge=1)
    ngram_range_max: int = Field(default=2, ge=1)
    min_document_frequency: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def _validate_ngram_bounds(self) -> Self:
        if self.ngram_range_min > self.ngram_range_max:
            raise ValueError("ngram_range_min cannot exceed ngram_range_max")
        return self


class ClusteringSettings(_FrozenSettings):
    """Hyperparameters for the primary HDBSCAN clusterer and both baselines."""

    model_config = SettingsConfigDict(env_prefix="ALERTFLOW_CLUSTER_")

    algorithm: ClusteringAlgorithm = ClusteringAlgorithm.HDBSCAN
    distance_metric: DistanceMetric = DistanceMetric.COSINE
    random_seed: int = 42

    hdbscan_min_cluster_size: int = Field(default=3, ge=2)
    hdbscan_min_samples: int | None = Field(default=None, ge=1)
    hdbscan_selection_epsilon: float = Field(default=0.0, ge=0.0)
    hdbscan_selection_method: Literal["eom", "leaf"] = "eom"

    dbscan_eps: float = Field(default=0.3, gt=0.0, le=2.0)
    dbscan_min_samples: int = Field(default=2, ge=1)

    kmeans_n_clusters: int = Field(default=5, ge=1)
    kmeans_n_init: int = Field(default=10, ge=1)


class StorageSettings(_FrozenSettings):
    """Persistence target; the repository interface isolates the backend."""

    model_config = SettingsConfigDict(env_prefix="ALERTFLOW_STORAGE_")

    database_url: str = "sqlite:///./alertflow.db"

    @field_validator("database_url")
    @classmethod
    def _sqlite_only(cls, v: str) -> str:
        if not v.startswith("sqlite:///"):
            raise ValueError("only sqlite:/// URLs are supported by the MVP storage layer")
        return v


class Settings(_FrozenSettings):
    """Top-level registry aggregating every subsystem's settings."""

    model_config = SettingsConfigDict(env_prefix="ALERTFLOW_")

    environment: Literal["development", "test", "production"] = "production"
    service_name: str = Field(default="alertflow", min_length=1)
    log_level: LogLevelName | None = None

    ingestion: IngestionSettings = Field(default_factory=IngestionSettings)
    dedup: DeduplicationSettings = Field(default_factory=DeduplicationSettings)
    embedding: EmbeddingSettings = Field(default_factory=EmbeddingSettings)
    tfidf: TfidfSettings = Field(default_factory=TfidfSettings)
    clustering: ClusteringSettings = Field(default_factory=ClusteringSettings)
    storage: StorageSettings = Field(default_factory=StorageSettings)

    @field_validator("log_level", mode="before")
    @classmethod
    def _normalize_log_level(cls, v: object) -> object:
        return v.strip().upper() if isinstance(v, str) else v


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide singleton; tests reset it via get_settings.cache_clear()."""
    return Settings()
