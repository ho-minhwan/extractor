"""RMS extractor metrics definitions."""

from cognite.extractorutils.metrics import BaseMetrics
from prometheus_client import Counter, Gauge


class RMSMetrics(BaseMetrics):
    """RMS extractor specific metrics."""

    def __init__(self, extractor_name: str, extractor_version: str):
        super().__init__(extractor_name=extractor_name, extractor_version=extractor_version)

        self.risk_master_rows_read = Counter(
            "rms_risk_master_rows_read",
            "Number of risk_master rows read from RMS DB.",
            labelnames=["item_type"],
        )

        self.risk_cycle_rows_read = Counter(
            "rms_risk_cycle_rows_read",
            "Number of risk_cycle rows read from RMS DB.",
            labelnames=["item_type"],
        )

        self.risk_master_rows_uploaded = Counter(
            "rms_risk_master_rows_uploaded",
            "Number of risk_master rows uploaded to CDF Raw.",
            labelnames=["item_type"],
        )

        self.risk_cycle_rows_uploaded = Counter(
            "rms_risk_cycle_rows_uploaded",
            "Number of risk_cycle rows uploaded to CDF Raw.",
            labelnames=["item_type"],
        )

        self.run_duration_seconds = Gauge(
            "rms_run_duration_seconds",
            "Duration of the last complete extraction cycle.",
        )
