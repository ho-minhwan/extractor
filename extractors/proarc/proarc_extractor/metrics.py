"""ProArc extractor metrics definitions."""

from cognite.extractorutils.metrics import BaseMetrics
from prometheus_client import Counter, Gauge


class ProArcMetrics(BaseMetrics):
    """ProArc File Extractor specific metrics."""

    def __init__(self, extractor_name: str, extractor_version: str):
        """Initialize ProArc metrics.

        Args:
            extractor_name: Name of the extractor.
            extractor_version: Version of the extractor.

        """
        super().__init__(extractor_name=extractor_name, extractor_version=extractor_version)

        # Identified items
        self.documents_md_identified = Counter(
            "proarc_documents_identified",
            "Total number of documents identified for processing.",
            labelnames=["item_type"],
        )

        self.revision_md_identified = Counter(
            "proarc_revisions_identified",
            "Total number of revisions identified for processing.",
            labelnames=["item_type"],
        )

        self.file_md_identified = Counter(
            "proarc_files_identified",
            "Total number of files identified for processing.",
            labelnames=["item_type"],
        )

        # Uploaded items
        self.document_md_uploaded = Counter(
            "proarc_document_md_uploaded",
            "Total number of document metadata records uploaded to CDF Raw.",
            labelnames=["item_type"],
        )

        self.revision_md_uploaded = Counter(
            "proarc_revision_md_uploaded",
            "Total number of revision metadata records uploaded to CDF Raw.",
            labelnames=["item_type"],
        )

        self.file_md_uploaded = Counter(
            "proarc_file_md_uploaded",
            "Total number of file metadata records uploaded to CDF Raw.",
            labelnames=["item_type"],
        )

        self.files_uploaded_to_cdf = Counter(
            "proarc_files_uploaded_to_cdf", "Total number of files uploaded to CDF Files.", labelnames=["item_type"]
        )

        self.files_size_in_bytes = Counter(
            "proarc_files_size_in_bytes",
            "Total size in bytes of files uploaded to CDF Files.",
            labelnames=["item_type"],
        )

        # Errors
        self.document_md_error = Counter(
            "proarc_document_md_error",
            "Total number of times document metadata fetch failed and entered except block.",
            labelnames=["item_type"],
        )

        self.revision_md_error = Counter(
            "proarc_revision_md_error",
            "Total number of documents where revision metadata fetch failed.",
            labelnames=["item_type"],
        )

        self.file_md_error = Counter(
            "proarc_file_md_error",
            "Total number of times file processing failed and entered except block.",
            labelnames=["item_type"],
        )

        # Duration
        self.run_duration_seconds = Gauge(
            "proarc_run_duration_seconds",
            "Duration of the last complete extraction cycle.",
        )
