"""Aveva UE technical extractor metrics definitions."""

from cognite.extractorutils.metrics import BaseMetrics
from prometheus_client import Counter, Gauge


class AvevaUEMetrics(BaseMetrics):
    """Aveva UE technical extractor specific metrics."""

    def __init__(self, extractor_name: str, extractor_version: str):
        """Initialize Aveva UE metrics.

        Args:
            extractor_name: Name of the extractor.
            extractor_version: Version of the extractor.

        """
        super().__init__(extractor_name=extractor_name, extractor_version=extractor_version)

        # Attempted items
        self.sources_attempted = Counter(
            "sources_attempted",
            "Total number of Aveva UE datasource requests attempted.",
            labelnames=["item_type"],
        )

        self.acknowledgements_attempted = Counter(
            "acknowledgements_attempted",
            "Total number of Aveva UE acknowledgement requests attempted.",
            labelnames=["item_type"],
        )

        self.chunks_attempted = Counter(
            "chunks_attempted",
            "Total number of Aveva UE data chunk requests attempted.",
            labelnames=["item_type"],
        )

        # Successful items
        self.sources_succeeded = Counter(
            "sources_succeeded",
            "Total number of Aveva UE datasource requests succeeded.",
            labelnames=["item_type"],
        )

        self.acknowledgements_succeeded = Counter(
            "acknowledgements_succeeded",
            "Total number of Aveva UE acknowledgement requests succeeded.",
            labelnames=["item_type"],
        )

        self.chunks_succeeded = Counter(
            "chunks_succeeded",
            "Total number of Aveva UE data chunk requests succeeded.",
            labelnames=["item_type"],
        )

        # Errors
        self.sources_errors = Counter(
            "sources_errors",
            "Total number of Aveva UE datasource request errors.",
            labelnames=["item_type"],
        )

        self.acknowledgements_errors = Counter(
            "acknowledgements_errors",
            "Total number of Aveva UE acknowledgement request errors.",
            labelnames=["item_type"],
        )

        self.chunks_errors = Counter(
            "chunks_errors",
            "Total number of Aveva UE data chunk request errors.",
            labelnames=["item_type"],
        )

        # Duration
        self.run_duration_seconds = Gauge(
            "run_duration_seconds",
            "Duration of the last complete extraction cycle.",
        )
