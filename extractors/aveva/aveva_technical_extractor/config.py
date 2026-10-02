"""Aveva UE technical data extractor configuration classes."""

from dataclasses import dataclass, field

from cognite.extractorutils.configtools import BaseConfig, MetricsConfig, RawDestinationConfig, StateStoreConfig


@dataclass
class ScheduleConfig:
    """Schedule for Windows service version of extractor."""

    schedule: str = "0 */6 * * *"


@dataclass
class ExtractorConfig:
    """Default configuration for extractor."""

    name: str = "aveva_technical"
    version: str = "1.0.0"
    state_store: StateStoreConfig = field(default_factory=StateStoreConfig)


@dataclass
class AvevaSourceConfig:
    """Aveva datasource parameters."""

    datasource: str
    category: str
    table_name: str


@dataclass
class AvevaConfig:
    """Aveva related configuration parameters."""

    bearer_token: str
    max_retry_attempts: int
    retry_internal_seconds: int
    number_worker_threads: int
    source_configs: list[AvevaSourceConfig]


@dataclass
class RawConfig:
    """CDF Raw related configuration parameters."""

    last_run: RawDestinationConfig
    storage_db: str


@dataclass
class Config(BaseConfig):
    """Extractor related configuration parameters."""

    raw: RawConfig
    aveva: AvevaConfig
    metrics: MetricsConfig
    schedule: ScheduleConfig
    extractor: ExtractorConfig = field(default_factory=ExtractorConfig)
