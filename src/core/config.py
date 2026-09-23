"""
Immutable configuration registry for the Alert Aggregation & Incident
Clustering Engine. Backed by Pydantic V2 BaseSettings; values are
resolvable from environment variables (prefix ALERTENGINE_) or a .env
file, with hardcoded defaults as the final fallback.
"""

from __future__ import annotations

from enum import Enum
from functools import lru_cache

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class DistanceMetric(str, Enum):
    """Supported metrics for the streaming density clustering core."""

    COSINE = "cosine"
    EUCLIDEAN = "euclidean"
    MANHATTAN = "manhattan"


class ClusteringSettings(BaseSettings):
    """DBSCAN hyperparameters governing incident cluster formation."""

    model_config = SettingsConfigDict(
        env_prefix="ALERTENGINE_CLUSTER_",
        frozen=True,
        extra="forbid",
    )

    dbscan_eps: float = Field(
        default=0.3,
        gt=0.0,
        le=2.0,
        description="Max neighborhood radius for core-point density reachability.",
    )
    dbscan_min_samples: int = Field(
        default=2,
        ge=1,
        description="Minimum samples in a neighborhood for a core point.",
    )
    distance_metric: DistanceMetric = Field(
        default=DistanceMetric.COSINE,
        description="Distance metric applied over the vectorized alert space.",
    )

    @field_validator("dbscan_min_samples")
    @classmethod
    def _validate_min_samples(cls, v: int) -> int:
        if v < 1:
            raise ValueError("dbscan_min_samples must be >= 1")
        return v


class StreamWindowSettings(BaseSettings):
    """Temporal windowing configuration for streaming ingestion."""

    model_config = SettingsConfigDict(
        env_prefix="ALERTENGINE_STREAM_",
        frozen=True,
        extra="forbid",
    )

    sliding_window_seconds: int = Field(
        default=300,
        gt=0,
        le=86_400,
        description="Width of the sliding temporal window used to bucket alerts.",
    )
    window_slide_interval_seconds: int = Field(
        default=30,
        gt=0,
        description="Advance interval between successive sliding window evaluations.",
    )
    max_alert_backlog: int = Field(
        default=50_000,
        gt=0,
        description="Hard ceiling on in-memory alerts retained per active window.",
    )

    @model_validator(mode="after")
    def _validate_slide_leq_window(self) -> "StreamWindowSettings":
        if self.window_slide_interval_seconds > self.sliding_window_seconds:
            raise ValueError(
                "window_slide_interval_seconds cannot exceed sliding_window_seconds"
            )
        return self


class VectorizationSettings(BaseSettings):
    """Term-frequency vectorization constraints for alert message encoding."""

    model_config = SettingsConfigDict(
        env_prefix="ALERTENGINE_VECTOR_",
        frozen=True,
        extra="forbid",
    )

    max_vocabulary_size: int = Field(
        default=20_000,
        gt=0,
        description="Upper bound on distinct terms retained in the TF vector space.",
    )
    ngram_range_min: int = Field(default=1, ge=1)
    ngram_range_max: int = Field(default=2, ge=1)
    min_document_frequency: int = Field(
        default=1,
        ge=1,
        description="Minimum alert occurrences required for a term to enter vocabulary.",
    )

    @model_validator(mode="after")
    def _validate_ngram_bounds(self) -> "VectorizationSettings":
        if self.ngram_range_min > self.ngram_range_max:
            raise ValueError("ngram_range_min cannot exceed ngram_range_max")
        return self


class EngineSettings(BaseSettings):
    """Top-level immutable settings registry aggregating all subsystems."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        frozen=True,
        extra="forbid",
    )

    environment: str = Field(default="production")
    service_name: str = Field(default="alert-clustering-engine")

    clustering: ClusteringSettings = Field(default_factory=ClusteringSettings)
    stream_window: StreamWindowSettings = Field(default_factory=StreamWindowSettings)
    vectorization: VectorizationSettings = Field(default_factory=VectorizationSettings)


@lru_cache(maxsize=1)
def get_settings() -> EngineSettings:
    """Process-wide singleton accessor; settings are frozen post-construction."""
    return EngineSettings()