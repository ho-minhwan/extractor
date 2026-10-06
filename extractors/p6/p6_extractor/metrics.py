"""Primavera P6 extractor metrics."""

from cognite.extractorutils.metrics import BaseMetrics
from prometheus_client import Counter, Gauge


class P6Metrics(BaseMetrics):
    """P6-specific extraction metrics."""

    def __init__(self, extractor_name: str, extractor_version: str):
        super().__init__(extractor_name=extractor_name, extractor_version=extractor_version)
        self.records_identified = Counter(
            "p6_records_identified",
            "Number of P6 records returned by the configured endpoints.",
            labelnames=["item_type"],
        )
        self.records_uploaded = Counter(
            "p6_records_uploaded",
            "Number of P6 records queued for CDF Raw.",
            labelnames=["item_type"],
        )
        self.query_errors = Counter(
            "p6_query_errors",
            "Number of P6 endpoint query failures.",
            labelnames=["item_type"],
        )
        self.run_duration_seconds = Gauge(
            "p6_run_duration_seconds",
            "Duration of the last complete extraction cycle.",
        )
