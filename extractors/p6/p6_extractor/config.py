"""Primavera P6 extractor configuration classes."""

from dataclasses import dataclass, field

from cognite.extractorutils.configtools import BaseConfig, MetricsConfig, RawDestinationConfig, StateStoreConfig


@dataclass
class ScheduleConfig:
    """Schedule for the Windows service version of the extractor."""

    schedule: str = "0 */12 * * *"


@dataclass
class ExtractorConfig:
    """Default extractor configuration."""

    name: str = "p6"
    version: str = "1.0.0"
    state_store: StateStoreConfig = field(default_factory=StateStoreConfig)


@dataclass
class QueryConfig:
    """Configuration for a P6 REST query and its Raw destination."""

    endpoint: str
    raw_table: RawDestinationConfig
    raw_key: str
    response_items_key: str = "items"
    has_more_field: str = "hasMore"
    limit_parameter: str = "limit"
    offset_parameter: str = "offset"


@dataclass
class P6Config:
    """P6 REST API connection and query configuration."""

    base_url: str
    user_name: str
    password: str
    timeout: int
    query_limit: int
    project_data: QueryConfig
    activity_data: QueryConfig


@dataclass
class RawConfig:
    """CDF Raw related configuration."""

    last_run: RawDestinationConfig


@dataclass
class Config(BaseConfig):
    """Extractor configuration."""

    raw: RawConfig
    p6: P6Config
    metrics: MetricsConfig
    schedule: ScheduleConfig
    extractor: ExtractorConfig = field(default_factory=ExtractorConfig)
