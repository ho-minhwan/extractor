"""ProArc custom file extractor configuration classes."""

from dataclasses import dataclass, field
from typing import Any

from cognite.extractorutils.configtools import BaseConfig, MetricsConfig, RawDestinationConfig, StateStoreConfig


@dataclass
class ScheduleConfig:
    """Schedule for Windows service version of extractor."""

    schedule: str = "0 */12 * * *"
    data_sync_schedule: str = "10 0 * * 0"


@dataclass
class ExtractorConfig:
    """Default configuration for extractor."""

    name: str = "proarc"
    version: str = "0.0.0"
    state_store: StateStoreConfig = field(default_factory=StateStoreConfig)


@dataclass
class ProArcRepoConfig:
    """Proarc repository configuration parameters."""

    repo_no: str
    project_no: str
    file_instance_space: str


@dataclass
class ProArcConfig:
    """ProArc repository related configuration parameters."""

    user_name: str
    password: str
    repos: list[ProArcRepoConfig]
    increment_variable: str
    access_token_url: str
    access_token_creds: str
    search_documents_url: str
    search_documents_criteria: dict[str, Any]
    sync_documents_criteria: dict[str, Any]
    get_revisions_url: str
    get_revisions_criteria: dict[str, Any]
    get_files_search_url: str
    get_files_export_url: str
    get_files_criteria: dict[str, Any]


@dataclass
class RawConfig:
    """CDF Raw related configuration parameters."""

    last_run: RawDestinationConfig
    document_md: RawDestinationConfig
    document_md_key: str
    revision_md: RawDestinationConfig
    revision_md_key: list
    file_md: RawDestinationConfig
    file_md_key: str


@dataclass
class Config(BaseConfig):
    """Extractor related configuration parameters."""

    raw: RawConfig
    proarc: ProArcConfig
    metrics: MetricsConfig
    schedule: ScheduleConfig
    extractor: ExtractorConfig = field(default_factory=ExtractorConfig)
