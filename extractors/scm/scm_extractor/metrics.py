"""SCM extractor metrics definitions."""

from cognite.extractorutils.metrics import BaseMetrics
from prometheus_client import Counter, Gauge


class SCMMetrics(BaseMetrics):
    """SCM Extractor specific metrics."""

    def __init__(self, extractor_name: str, extractor_version: str):
        """Initialize SCM metrics.

        Args:
            extractor_name: Name of the extractor.
            extractor_version: Version of the extractor.

        """
        super().__init__(extractor_name=extractor_name, extractor_version=extractor_version)

        # Identified items
        self.suppliers_queried = Counter(
            "suppliers_queried",
            "Total number of suppliers identified for processing.",
            labelnames=["item_type"],
        )

        self.purchase_orders_queried = Counter(
            "purchase_orders_queried",
            "Total number of purchase orders identified for processing.",
            labelnames=["item_type"],
        )

        self.request_for_quotations_queried = Counter(
            "request_for_quotations_queried",
            "Total number of request for quotations identified for processing.",
            labelnames=["item_type"],
        )

        # Supplier uploaded items
        self.supplier_attachments_succeeded = Counter(
            "supplier_attachments_succeeded",
            "Number of suppliers for which attachment records query succeeded.",
            labelnames=["item_type"],
        )

        self.supplier_sites_succeeded = Counter(
            "supplier_sites_succeeded",
            "Number of suppliers for which site records query succeeded.",
            labelnames=["item_type"],
        )

        self.attachment_files_succeeded = Counter(
            "attachment_files_succeeded",
            "Number of attachment files uploaded to CDF.",
            labelnames=["item_type"],
        )

        # Purchase order uploaded items
        self.purchase_order_lines_succeeded = Counter(
            "purchase_order_lines_succeeded",
            "Number of purchase order lines for which records query succeeded.",
            labelnames=["item_type"],
        )

        self.purchase_order_DFFs_succeeded = Counter(
            "purchase_order_DFFs_succeeded",
            "Number of purchase order DFFs for which records query succeeded.",
            labelnames=["item_type"],
        )

        self.purchase_order_attachments_succeeded = Counter(
            "purchase_order_attachments_succeeded",
            "Number of purchase orders for which attachment records query succeeded.",
            labelnames=["item_type"],
        )

        self.purchase_order_files_succeeded = Counter(
            "purchase_order_files_succeeded",
            "Number of purchase order attachment files uploaded to CDF.",
            labelnames=["item_type"],
        )

        # Request for quotation uploaded items
        self.request_for_quotation_lines_succeeded = Counter(
            "request_for_quotation_lines_succeeded",
            "Number of request for quotation lines for which records query succeeded.",
            labelnames=["item_type"],
        )

        self.request_for_quotation_DFFs_succeeded = Counter(
            "request_for_quotation_DFFs_succeeded",
            "Number of request for quotation DFFs for which records query succeeded.",
            labelnames=["item_type"],
        )

        self.request_for_quotation_attachments_succeeded = Counter(
            "request_for_quotation_attachments_succeeded",
            "Number of request for quotations for which attachment records query succeeded.",
            labelnames=["item_type"],
        )

        self.request_for_quotation_files_succeeded = Counter(
            "request_for_quotation_files_succeeded",
            "Number of request for quotation attachment files uploaded to CDF.",
            labelnames=["item_type"],
        )

        # Errors
        self.supplier_attachments_failed = Counter(
            "supplier_attachments_failed",
            "Number of suppliers for which attachment records query failed.",
            labelnames=["item_type"],
        )

        self.supplier_sites_failed = Counter(
            "supplier_sites_failed",
            "Number of suppliers for which site records query failed.",
            labelnames=["item_type"],
        )

        self.purchase_order_lines_failed = Counter(
            "purchase_order_lines_failed",
            "Number of purchase order lines for which records query failed.",
            labelnames=["item_type"],
        )

        self.purchase_order_DFFs_failed = Counter(
            "purchase_order_DFFs_failed",
            "Number of purchase order DFFs for which records query failed.",
            labelnames=["item_type"],
        )

        self.purchase_order_attachments_failed = Counter(
            "purchase_order_attachments_failed",
            "Number of purchase orders for which attachment records query failed.",
            labelnames=["item_type"],
        )

        self.request_for_quotation_lines_failed = Counter(
            "request_for_quotation_lines_failed",
            "Number of request for quotation lines for which records query failed.",
            labelnames=["item_type"],
        )

        self.request_for_quotation_DFFs_failed = Counter(
            "request_for_quotation_DFFs_failed",
            "Number of request for quotation DFFs for which records query failed.",
            labelnames=["item_type"],
        )

        self.request_for_quotation_attachments_failed = Counter(
            "request_for_quotation_attachments_failed",
            "Number of request for quotations for which attachment records query failed.",
            labelnames=["item_type"],
        )

        # Duration
        self.run_duration_seconds = Gauge(
            "scm_run_duration_seconds",
            "Duration of the last complete extraction cycle.",
        )
