"""RMS extractor configuration classes."""

from dataclasses import dataclass, field

from cognite.extractorutils.configtools import BaseConfig, MetricsConfig, RawDestinationConfig, StateStoreConfig


@dataclass
class ScheduleConfig:
    """Schedule for Windows service version of extractor."""

    schedule: str = "0 */6 * * *"


@dataclass
class ExtractorConfig:
    """Default configuration for extractor."""

    name: str = "rms_extractor"
    version: str = "1.0.0"
    state_store: StateStoreConfig = field(default_factory=StateStoreConfig)


@dataclass
class DBConfig:
    """Database connection configuration."""

    url: str
    batch_size: int = 500


@dataclass
class RMSConfig:
    """RMS database and Raw related configuration parameters."""

    db: DBConfig
    raw_database: str
    table_names: list[str] = field(default_factory=lambda: ["risk_master", "risk_cycle", "risk_action"])
    raw_key_fields: dict[str, str] = field(
        default_factory=lambda: {
            "risk_master": "risk_id",
            "risk_cycle": "cycle_id",
            "risk_action": "action_id",
        }
    )


@dataclass
class Config(BaseConfig):
    """Extractor related configuration parameters."""

    raw: RawDestinationConfig
    rms: RMSConfig
    metrics: MetricsConfig
    schedule: ScheduleConfig
    extractor: ExtractorConfig = field(default_factory=ExtractorConfig)
