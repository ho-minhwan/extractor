"""SCM custom file extractor configuration classes."""

from dataclasses import dataclass, field

from cognite.extractorutils.configtools import BaseConfig, MetricsConfig, RawDestinationConfig, StateStoreConfig


@dataclass
class ScheduleConfig:
    """Schedule for Windows service version of extractor."""

    schedule: str = "0 */12 * * *"


@dataclass
class ExtractorConfig:
    """Default configuration for extractor."""

    name: str = "scm"
    version: str = "0.0.0"
    state_store: StateStoreConfig = field(default_factory=StateStoreConfig)


@dataclass
class QueryConfig:
    """SCM api url configuration parameters."""

    url: str
    raw_table: RawDestinationConfig | None = None
    raw_key: str | None = None
    omit_fields: list[str] = field(default_factory=list)


@dataclass
class SCMConfig:
    """SCM data related configuration parameters."""

    user_name: str
    password: str
    query_limit: int
    timeout: int
    instance_space_ext_id: str
    supplier_data: dict[str, QueryConfig]
    purchase_order_data: dict[str, QueryConfig]
    request_for_quotation_data: dict[str, QueryConfig]


@dataclass
class Config(BaseConfig):
    """Extractor related configuration parameters."""

    statestore: RawDestinationConfig
    metrics: MetricsConfig
    scm: SCMConfig
    schedule: ScheduleConfig
    extractor: ExtractorConfig = field(default_factory=ExtractorConfig)
