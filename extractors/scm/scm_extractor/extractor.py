"""Functions for custom ProArc file extractor."""

import datetime
import time
from logging import getLogger
from mimetypes import guess_type
from threading import Event

import requests
from cognite.client import CogniteClient
from cognite.client.data_classes import ExtractionPipelineRunWrite, Row
from cognite.client.data_classes.data_modeling.extractor_extensions.v1 import CogniteExtractorFileApply
from cognite.extractorutils.configtools import RawDestinationConfig
from cognite.extractorutils.metrics import safe_get
from cognite.extractorutils.statestore import RawStateStore
from cognite.extractorutils.uploader import BytesUploadQueue, RawUploadQueue
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from scm_extractor.config import Config
from scm_extractor.metrics import SCMMetrics

logger = getLogger(__name__)


def create_state_store(client: CogniteClient, config: RawDestinationConfig) -> tuple[RawStateStore, str, float]:
    """Creates a RawStateStore in CDF to store the last run time of the last succcessful extractor run.

    Args:
        client: To access Statestore in CDF Raw
        config: RawDestinationConfig for statestore table.

    Returns:
        A tuple containing:
        - a RawStateStore
        - last_run_utc: The last successful extractor run time in UTC from Raw.
        - last_run_epoch: The last successful extractor run epoch time from Raw.

    """
    state_store = RawStateStore(cdf_client=client, database=config.database, table=config.table)
    state_store.initialize()
    last_run_utc = state_store.get_state(external_id="last_run_utc")[1]
    last_run_epoch = state_store.get_state(external_id="last_run_epoch")[1]

    return state_store, last_run_utc, last_run_epoch


def create_file_md(
    attachment: dict, raw_key: str, instance_space: str, tags: list[str] | None = None
) -> CogniteExtractorFileApply:
    """Creates CogniteExtractorFileApply object for a given attachment.

    Args:
        attachment (dict): The attachment dictionary containing metadata.
        raw_key (str): The key to use for the external ID of the file.
        instance_space (str): The space where the file instance is located.
        tags: list of tags to add to file properties

    Returns:
        CogniteExtractorFileApply: The created CogniteExtractorFileApply object.

    """
    # name
    for character in ["\\", "/"]:
        attachment["FileName"] = attachment["FileName"].replace(character, "_")
    clean_file_name = attachment["FileName"]

    # mime type
    mime_type = guess_type(attachment["FileName"])[0] or "application/octet-stream"

    if tags is None:
        tags = []

    return CogniteExtractorFileApply(
        space=instance_space,
        external_id=str(attachment[raw_key]),
        name=clean_file_name,
        mime_type=mime_type,
        tags=tags,
    )


def get_supplier_data(
    upload_queue: RawUploadQueue,
    file_upload_queue: BytesUploadQueue,
    config: Config,
    last_run_utc: str,
    last_run_epoch: float,
    stop_event: Event,
    metrics: SCMMetrics = None,
) -> bool:
    """Queries SCM for supplier data.

    Args:
        upload_queue: Queue for uploading SCM data to CDF Raw.
        file_upload_queue: Queue for uploading attachment contents to CDF.
        config: SCM configuration object containing SCM and Raw settings.
        last_run_utc: The last successful extractor run time in UTC.
        last_run_epoch: The last successful extractor run epoch time.
        stop_event: Event to signal stopping the search.
        metrics: SCMMetrics object for tracking metrics (optional).

    Returns:
        bool: True if the all SCM supplier extraction succeeded, False otherwise.

    """
    retry_strategy = Retry(
        total=3,  # number of retries
        backoff_factor=1,  # wait 1s between retries
        status_forcelist=[429, 500, 502, 503, 504],  # retry on these errors
        allowed_methods=["GET"],
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session = requests.Session()
    session.mount("https://", adapter)
    session.auth = (config.scm.user_name, config.scm.password)

    function_succeed = True

    # SUPPLIERS
    offset = 0
    more_suppliers = True
    supplier_dict = {}
    while more_suppliers:
        if stop_event.is_set():
            logger.info("Stop event set in get_supplier_data.")
            return False

        try:
            data = session.get(
                config.scm.supplier_data["supplier_query"].url.format(limit=config.scm.query_limit, offset=offset),
                timeout=config.scm.timeout,
            )

            data.raise_for_status()
            supplier_data = data.json()
            more_suppliers = supplier_data["hasMore"]  # pagination
            offset += config.scm.query_limit

            # parse suppliers and add to raw upload queue
            db = config.scm.supplier_data["supplier_query"].raw_table.database
            table = config.scm.supplier_data["supplier_query"].raw_table.table
            raw_key = config.scm.supplier_data["supplier_query"].raw_key

            # create supplier dict for lookup in subsequent queries
            for supplier in supplier_data["items"]:
                supplier_dict[supplier["SupplierId"]] = supplier["Supplier"]
                raw_dict = {
                    k: v for k, v in supplier.items() if k not in config.scm.supplier_data["supplier_query"].omit_fields
                }
                upload_queue.add_to_upload_queue(
                    database=db,
                    table=table,
                    raw_row=Row(key=raw_dict[raw_key], columns=raw_dict),
                )

            # update metric
            if metrics:
                metrics.suppliers_queried.labels(item_type="suppliers").inc(len(supplier_data["items"]))
            logger.info("batch of %d suppliers identified for processing.", len(supplier_data["items"]))

        except (requests.exceptions.HTTPError, requests.exceptions.RequestException, ValueError) as e:
            logger.error("Error querying a batch of suppliers at offset %d: %s", offset, e)
            more_suppliers = False
            function_succeed = False

    # SUPPLIER ATTACHMENTS
    for supplier_id, supplier_name in supplier_dict.items():
        if stop_event.is_set():
            logger.info("Stop event set in get_supplier_data.")
            return False

        offset = 0
        more_attachments = True
        while more_attachments:
            try:
                data = session.get(
                    config.scm.supplier_data["attachment_query"].url.format(
                        supplier_id=supplier_id, limit=config.scm.query_limit, offset=offset
                    ),
                    timeout=config.scm.timeout,
                )
                data.raise_for_status()
                attachment_data = data.json()
                more_attachments = attachment_data["hasMore"]  # pagination
                offset += config.scm.query_limit

                # parse attachments and add to raw upload queue
                db = config.scm.supplier_data["attachment_query"].raw_table.database
                table = config.scm.supplier_data["attachment_query"].raw_table.table
                raw_key = config.scm.supplier_data["attachment_query"].raw_key
                files_uploaded = 0
                for attachment in attachment_data["items"]:
                    raw_dict = {
                        k: v
                        for k, v in attachment.items()
                        if k not in config.scm.supplier_data["attachment_query"].omit_fields
                    }
                    raw_dict["SupplierId"] = supplier_id
                    raw_dict["Supplier"] = supplier_name
                    upload_queue.add_to_upload_queue(
                        database=db,
                        table=table,
                        raw_row=Row(key=raw_dict[raw_key], columns=raw_dict),
                    )
                    if attachment.get("DatatypeCode") == "FILE":
                        # check file size
                        if attachment.get("UploadedFileLength") > 100 * 1024 * 1024:
                            logger.warning(
                                "Attachment %s for supplier %s is %d MB in size.",
                                attachment.get("AttachedDocumentId"),
                                supplier_id,
                                attachment.get("UploadedFileLength") / (1024 * 1024),
                            )

                        if file_contents_url := next(
                            (item["href"] for item in attachment["links"] if item.get("name") == "FileContents"), None
                        ):
                            try:
                                file_md = create_file_md(
                                    attachment,
                                    raw_key,
                                    config.scm.instance_space_ext_id,
                                    ["scm", "supplier_attachments"],
                                )
                                contents = session.get(
                                    file_contents_url,
                                    timeout=3 * config.scm.timeout,
                                )
                                files_uploaded += 1
                                file_upload_queue.add_to_upload_queue(content=contents.content, file_meta=file_md)
                            except Exception as e:
                                logger.error(
                                    "Error uploading file for attachment: %s in supplier: %s: %s",
                                    attachment.get("AttachedDocumentId"),
                                    supplier_id,
                                    e,
                                )

                # update metric
                if metrics:
                    metrics.supplier_attachments_succeeded.labels(item_type="supplier_attachments").inc()
                    metrics.attachment_files_succeeded.labels(item_type="attachment_files").inc(files_uploaded)

                logger.info(
                    "%d attachments extracted for supplier: %s. Uploaded %d files to CDF",
                    len(attachment_data["items"]),
                    supplier_id,
                    files_uploaded,
                )

            except (requests.exceptions.HTTPError, requests.exceptions.RequestException, ValueError) as e:
                logger.error("Error querying attachments for supplier %s at offset %d: %s", supplier_id, offset, e)
                if metrics:
                    metrics.supplier_attachments_failed.labels(item_type="supplier_attachments").inc()
                more_attachments = False
                function_succeed = False

    # SUPPLIER SITES
    for supplier_id, supplier_name in supplier_dict.items():
        if stop_event.is_set():
            logger.info("Stop event set in get_supplier_data.")
            return False

        offset = 0
        more_sites = True
        while more_sites:
            try:
                data = session.get(
                    config.scm.supplier_data["site_query"].url.format(
                        supplier_id=supplier_id, limit=config.scm.query_limit, offset=offset
                    ),
                    timeout=config.scm.timeout,
                )
                data.raise_for_status()
                site_data = data.json()
                more_sites = site_data["hasMore"]  # pagination
                offset += config.scm.query_limit

                # parse sites and add to raw upload queue
                db = config.scm.supplier_data["site_query"].raw_table.database
                table = config.scm.supplier_data["site_query"].raw_table.table
                raw_key = config.scm.supplier_data["site_query"].raw_key
                for site in site_data["items"]:
                    raw_dict = {
                        k: v for k, v in site.items() if k not in config.scm.supplier_data["site_query"].omit_fields
                    }
                    raw_dict["SupplierId"] = supplier_id
                    raw_dict["Supplier"] = supplier_name

                    upload_queue.add_to_upload_queue(
                        database=db,
                        table=table,
                        raw_row=Row(key=raw_dict[raw_key], columns=raw_dict),
                    )

                # update metric
                if metrics:
                    metrics.supplier_sites_succeeded.labels(item_type="supplier_sites").inc()
                logger.info("%d sites extracted for supplier: %s", len(site_data["items"]), supplier_id)
            except (requests.exceptions.HTTPError, requests.exceptions.RequestException, ValueError) as e:
                logger.error("Error querying sites for supplier %s at offset %d: %s", supplier_id, offset, e)
                if metrics:
                    metrics.supplier_sites_failed.labels(item_type="supplier_sites").inc()
                more_sites = False
                function_succeed = False

    return function_succeed


def get_purchase_order_data(
    upload_queue: RawUploadQueue,
    file_upload_queue: BytesUploadQueue,
    config: Config,
    last_run_utc: str,
    last_run_epoch: float,
    stop_event: Event,
    metrics: SCMMetrics = None,
) -> bool:
    """Queries SCM for purchase order data.

    Args:
        upload_queue: Queue for uploading SCM data to CDF Raw.
        file_upload_queue: Queue for uploading attachment contents to CDF.
        config: SCM configuration object containing SCM and Raw settings.
        last_run_utc: The last successful extractor run time in UTC.
        last_run_epoch: The last successful extractor run epoch time.
        stop_event: Event to signal stopping the search.
        metrics: SCMMetrics object for tracking metrics (optional).

    Returns:
        bool: True if the all SCM purchase order extraction succeeded, False otherwise.

    """
    # Set up a requests session with retry strategy
    retry_strategy = Retry(
        total=3,  # number of retries
        backoff_factor=1,  # wait 1s between retries
        status_forcelist=[429, 500, 502, 503, 504],  # retry on these errors
        allowed_methods=["GET"],
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session = requests.Session()
    session.mount("https://", adapter)
    session.auth = (config.scm.user_name, config.scm.password)

    function_succeed = True

    # PURCHSASE ORDERS
    offset = 0
    more_pos = True
    po_header_id_set = set()
    while more_pos:
        if stop_event.is_set():
            logger.info("Stop event set in get_purchase_order_data.")
            return False

        try:
            data = session.get(
                config.scm.purchase_order_data["purchase_order_query"].url.format(
                    limit=config.scm.query_limit, offset=offset
                ),
                timeout=config.scm.timeout,
            )

            data.raise_for_status()
            po_data = data.json()
            more_pos = po_data["hasMore"]  # pagination
            offset += config.scm.query_limit

            # parse purchase orders and add to raw upload queue
            db = config.scm.purchase_order_data["purchase_order_query"].raw_table.database
            table = config.scm.purchase_order_data["purchase_order_query"].raw_table.table
            raw_key = config.scm.purchase_order_data["purchase_order_query"].raw_key

            for po in po_data["items"]:
                po_header_id_set.add(po["POHeaderId"])
                raw_dict = {
                    k: v
                    for k, v in po.items()
                    if k not in config.scm.purchase_order_data["purchase_order_query"].omit_fields
                }
                upload_queue.add_to_upload_queue(
                    database=db,
                    table=table,
                    raw_row=Row(key=raw_dict[raw_key], columns=raw_dict),
                )

            # update metric
            if metrics:
                metrics.purchase_orders_queried.labels(item_type="purchase_orders").inc(len(po_data["items"]))
            logger.info("batch of %d purchase orders identified for processing.", len(po_data["items"]))

        except (requests.exceptions.HTTPError, requests.exceptions.RequestException, ValueError) as e:
            logger.error("Error querying a batch of purchase orders at offset %d: %s", offset, e)
            more_pos = False
            function_succeed = False

    # PURCHASE ORDER Lines / DFF (Descriptive Flex Fields)
    for po_header_id in po_header_id_set:
        if stop_event.is_set():
            logger.info("Stop event set in get_purchase_order_data.")
            return False

        # query lines for each purchase order
        offset = 0
        more_lines = True
        while more_lines:
            try:
                line_id_set = set()
                data = session.get(
                    config.scm.purchase_order_data["lines_query"].url.format(
                        PO_header_id=po_header_id, limit=config.scm.query_limit, offset=offset
                    ),
                    timeout=config.scm.timeout,
                )
                data.raise_for_status()
                line_data = data.json()
                more_lines = line_data["hasMore"]  # pagination
                offset += config.scm.query_limit

                # parse lines and add to raw upload queue
                line_db = config.scm.purchase_order_data["lines_query"].raw_table.database
                line_table = config.scm.purchase_order_data["lines_query"].raw_table.table
                line_raw_key = config.scm.purchase_order_data["lines_query"].raw_key
                for line in line_data["items"]:
                    line_id = line["POLineId"]
                    line_id_set.add(line_id)
                    raw_dict = {
                        k: v
                        for k, v in line.items()
                        if k not in config.scm.purchase_order_data["lines_query"].omit_fields
                    }
                    raw_dict[config.scm.purchase_order_data["purchase_order_query"].raw_key] = po_header_id
                    upload_queue.add_to_upload_queue(
                        database=line_db,
                        table=line_table,
                        raw_row=Row(key=f"{po_header_id}:{raw_dict[line_raw_key]}", columns=raw_dict),
                    )

                    # query DFF for each line
                    DFF_offset = 0
                    more_DFFs = True
                    while more_DFFs:
                        try:
                            data = session.get(
                                config.scm.purchase_order_data["DFF_query"].url.format(
                                    PO_header_id=po_header_id,
                                    line_id=line_id,
                                    limit=config.scm.query_limit,
                                    offset=DFF_offset,
                                ),
                                timeout=config.scm.timeout,
                            )
                            data.raise_for_status()
                            DFF_data = data.json()
                            more_DFFs = DFF_data["hasMore"]  # pagination
                            DFF_offset += config.scm.query_limit

                            # parse lines and add to raw upload queue
                            DFF_db = config.scm.purchase_order_data["DFF_query"].raw_table.database
                            DFF_table = config.scm.purchase_order_data["DFF_query"].raw_table.table
                            DFF_raw_key = config.scm.purchase_order_data["DFF_query"].raw_key
                            for DFF in DFF_data["items"]:
                                raw_dict = {
                                    k: v
                                    for k, v in DFF.items()
                                    if k not in config.scm.purchase_order_data["DFF_query"].omit_fields
                                }
                                raw_dict[config.scm.purchase_order_data["purchase_order_query"].raw_key] = po_header_id
                                upload_queue.add_to_upload_queue(
                                    database=DFF_db,
                                    table=DFF_table,
                                    raw_row=Row(key=f"{po_header_id}:{raw_dict[DFF_raw_key]}", columns=raw_dict),
                                )

                            # update metric for DFF
                            if metrics:
                                metrics.purchase_order_DFFs_succeeded.labels(item_type="purchase_order_DFFs").inc()

                        except (requests.exceptions.HTTPError, requests.exceptions.RequestException, ValueError) as e:
                            logger.error(
                                "Error querying DFF for purchase order %s, line %s at offset %d: %s",
                                po_header_id,
                                line_id,
                                DFF_offset,
                                e,
                            )
                            if metrics:
                                metrics.purchase_order_DFFs_failed.labels(item_type="purchase_order_DFFs").inc()
                            more_DFFs = False
                            function_succeed = False

                # update metric - for lines
                if metrics:
                    metrics.purchase_order_lines_succeeded.labels(item_type="purchase_order_lines").inc()

                logger.info(
                    "%d lines extracted for purchase order: %s. lines ids: %s",
                    len(line_data["items"]),
                    po_header_id,
                    list(line_id_set),
                )

            except (requests.exceptions.HTTPError, requests.exceptions.RequestException, ValueError) as e:
                logger.error("Error querying lines for purchase order %s at offset %d: %s", po_header_id, offset, e)
                if metrics:
                    metrics.purchase_order_lines_failed.labels(item_type="purchase_order_lines").inc()
                more_lines = False
                function_succeed = False

    # PURCHASE ORDER ATTACHMENTS
    for po_header_id in po_header_id_set:
        if stop_event.is_set():
            logger.info("Stop event set in get_purchase_order_data.")
            return False

        offset = 0
        more_attachments = True
        while more_attachments:
            try:
                data = session.get(
                    config.scm.purchase_order_data["attachment_query"].url.format(
                        PO_header_id=po_header_id, limit=config.scm.query_limit, offset=offset
                    ),
                    timeout=config.scm.timeout,
                )
                data.raise_for_status()
                attachment_data = data.json()
                more_attachments = attachment_data["hasMore"]  # pagination
                offset += config.scm.query_limit

                # parse attachments and add to raw upload queue
                db = config.scm.purchase_order_data["attachment_query"].raw_table.database
                table = config.scm.purchase_order_data["attachment_query"].raw_table.table
                raw_key = config.scm.purchase_order_data["attachment_query"].raw_key
                files_uploaded = 0
                for attachment in attachment_data["items"]:
                    raw_dict = {
                        k: v
                        for k, v in attachment.items()
                        if k not in config.scm.purchase_order_data["attachment_query"].omit_fields
                    }
                    raw_dict[config.scm.purchase_order_data["purchase_order_query"].raw_key] = po_header_id
                    upload_queue.add_to_upload_queue(
                        database=db,
                        table=table,
                        raw_row=Row(key=raw_dict[raw_key], columns=raw_dict),
                    )
                    if (attachment.get("DatatypeCode") == "FILE") and (
                        file_contents_url := next(
                            (item["href"] for item in attachment["links"] if item.get("name") == "FileContents"), None
                        )
                    ):
                        # check file size
                        if attachment.get("UploadedFileLength") > 100 * 1024 * 1024:
                            logger.warning(
                                "Attachment %s for purchase order %s is %d MB in size.",
                                attachment.get("AttachedDocumentId"),
                                po_header_id,
                                attachment.get("UploadedFileLength") / (1024 * 1024),
                            )
                        try:
                            file_md = create_file_md(
                                attachment,
                                raw_key,
                                config.scm.instance_space_ext_id,
                                ["scm", "purchase_order_attachments"],
                            )
                            contents = session.get(
                                file_contents_url,
                                timeout=3 * config.scm.timeout,
                            )
                            files_uploaded += 1
                            file_upload_queue.add_to_upload_queue(content=contents.content, file_meta=file_md)
                        except Exception as e:
                            logger.error(
                                "Error uploading file for attachment: %s in purchase order: %s: %s",
                                attachment.get("AttachedDocumentId"),
                                po_header_id,
                                e,
                            )

                # update metric
                if metrics:
                    metrics.purchase_order_attachments_succeeded.labels(item_type="purchase_order_attachments").inc()
                    metrics.purchase_order_files_succeeded.labels(item_type="purchase_order_files").inc(files_uploaded)

                logger.info(
                    "%d attachments extracted for purchase order: %s. Uploaded %d files to CDF",
                    len(attachment_data["items"]),
                    po_header_id,
                    files_uploaded,
                )

            except (requests.exceptions.HTTPError, requests.exceptions.RequestException, ValueError) as e:
                logger.error(
                    "Error querying attachments for purchase order %s at offset %d: %s", po_header_id, offset, e
                )
                if metrics:
                    metrics.purchase_order_attachments_failed.labels(item_type="purchase_order_attachments").inc()
                more_attachments = False
                function_succeed = False

    return function_succeed


def get_request_for_quotation_data(
    upload_queue: RawUploadQueue,
    file_upload_queue: BytesUploadQueue,
    config: Config,
    last_run_utc: str,
    last_run_epoch: float,
    stop_event: Event,
    metrics: SCMMetrics = None,
) -> bool:
    """Queries SCM for request for quotation data.

    Args:
        upload_queue: Queue for uploading SCM data to CDF Raw.
        file_upload_queue: Queue for uploading attachment contents to CDF.
        config: SCM configuration object containing SCM and Raw settings.
        last_run_utc: The last successful extractor run time in UTC.
        last_run_epoch: The last successful extractor run epoch time.
        stop_event: Event to signal stopping the search.
        metrics: SCMMetrics object for tracking metrics (optional).

    Returns:
        bool: True if the all SCM request for quotation extraction succeeded, False otherwise.

    """
    # Set up a requests session with retry strategy
    retry_strategy = Retry(
        total=3,  # number of retries
        backoff_factor=1,  # wait 1s between retries
        status_forcelist=[429, 500, 502, 503, 504],  # retry on these errors
        allowed_methods=["GET"],
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session = requests.Session()
    session.mount("https://", adapter)
    session.auth = (config.scm.user_name, config.scm.password)

    function_succeed = True

    # REQUEST FOR QUOTATIONS
    offset = 0
    more_neg = True
    rfq_auction_header_id_set = set()
    while more_neg:  # querying supplier negotiation end point for RFQs
        rfq_count = 0
        if stop_event.is_set():
            logger.info("Stop event set in get_request_for_quotation_data.")
            return False

        try:
            data = session.get(
                config.scm.request_for_quotation_data["request_for_quotation_query"].url.format(
                    limit=config.scm.query_limit,
                    offset=offset,
                    timeout=config.scm.timeout,
                ),
            )

            data.raise_for_status()
            neg_data = data.json()
            more_neg = neg_data["hasMore"]  # pagination
            offset += config.scm.query_limit

            # parse request for quotations and add to raw upload queue
            db = config.scm.request_for_quotation_data["request_for_quotation_query"].raw_table.database
            table = config.scm.request_for_quotation_data["request_for_quotation_query"].raw_table.table
            raw_key = config.scm.request_for_quotation_data["request_for_quotation_query"].raw_key

            for item in neg_data["items"]:
                # Only process RFQs
                if item["NegotiationType"] != "RFQ":
                    continue
                rfq_auction_header_id_set.add(item["AuctionHeaderId"])
                raw_dict = {
                    k: v
                    for k, v in item.items()
                    if k not in config.scm.request_for_quotation_data["request_for_quotation_query"].omit_fields
                }
                upload_queue.add_to_upload_queue(
                    database=db,
                    table=table,
                    raw_row=Row(key=raw_dict[raw_key], columns=raw_dict),
                )
                rfq_count += 1

            # update metric
            if metrics:
                metrics.request_for_quotations_queried.labels(item_type="request_for_quotations").inc(rfq_count)
            logger.info("batch of %d request for quotations identified for processing.", rfq_count)

        except (requests.exceptions.HTTPError, requests.exceptions.RequestException, ValueError) as e:
            logger.error("Error querying a batch of request for quotations at offset %d: %s", offset, e)
            more_neg = False
            function_succeed = False

    # REQUEST FOR QUOTATION Lines / DFF (Descriptive Flex Fields)
    for rfq_id in rfq_auction_header_id_set:
        if stop_event.is_set():
            logger.info("Stop event set in get_request_for_quotation_data.")
            return False

        # query lines for each request for quotation
        offset = 0
        more_lines = True
        while more_lines:
            try:
                line_id_set = set()
                data = session.get(
                    config.scm.request_for_quotation_data["lines_query"].url.format(
                        rfq_header_id=rfq_id, limit=config.scm.query_limit, offset=offset
                    ),
                    timeout=config.scm.timeout,
                )

                data.raise_for_status()
                line_data = data.json()
                more_lines = line_data["hasMore"]  # pagination
                offset += config.scm.query_limit

                # parse lines and add to raw upload queue
                line_db = config.scm.request_for_quotation_data["lines_query"].raw_table.database
                line_table = config.scm.request_for_quotation_data["lines_query"].raw_table.table
                line_raw_key = config.scm.request_for_quotation_data["lines_query"].raw_key
                for line in line_data["items"]:
                    line_id = line["LineId"]
                    line_id_set.add(line_id)
                    raw_dict = {
                        k: v
                        for k, v in line.items()
                        if k not in config.scm.request_for_quotation_data["lines_query"].omit_fields
                    }
                    raw_dict[config.scm.request_for_quotation_data["request_for_quotation_query"].raw_key] = rfq_id
                    upload_queue.add_to_upload_queue(
                        database=line_db,
                        table=line_table,
                        raw_row=Row(key=f"{rfq_id}:{raw_dict[line_raw_key]}", columns=raw_dict),
                    )

                    # query DFF for each line
                    DFF_offset = 0
                    more_DFFs = True
                    while more_DFFs:
                        try:
                            data = session.get(
                                config.scm.request_for_quotation_data["DFF_query"].url.format(
                                    rfq_header_id=rfq_id,
                                    line_id=line_id,
                                    limit=config.scm.query_limit,
                                    offset=DFF_offset,
                                ),
                                timeout=config.scm.timeout,
                            )

                            data.raise_for_status()
                            DFF_data = data.json()
                            more_DFFs = DFF_data["hasMore"]  # pagination
                            DFF_offset += config.scm.query_limit

                            # parse lines and add to raw upload queue
                            DFF_db = config.scm.request_for_quotation_data["DFF_query"].raw_table.database
                            DFF_table = config.scm.request_for_quotation_data["DFF_query"].raw_table.table
                            DFF_raw_key = config.scm.request_for_quotation_data["DFF_query"].raw_key
                            for DFF in DFF_data["items"]:
                                raw_dict = {
                                    k: v
                                    for k, v in DFF.items()
                                    if k not in config.scm.request_for_quotation_data["DFF_query"].omit_fields
                                }
                                raw_dict[
                                    config.scm.request_for_quotation_data["request_for_quotation_query"].raw_key
                                ] = rfq_id
                                upload_queue.add_to_upload_queue(
                                    database=DFF_db,
                                    table=DFF_table,
                                    raw_row=Row(key=f"{rfq_id}:{raw_dict[DFF_raw_key]}", columns=raw_dict),
                                )

                            # update metric for DFF
                            if metrics:
                                metrics.request_for_quotation_DFFs_succeeded.labels(
                                    item_type="request_for_quotation_DFFs"
                                ).inc()

                        except (requests.exceptions.HTTPError, requests.exceptions.RequestException, ValueError) as e:
                            logger.error(
                                "Error querying DFF for request for quotation %s, line %s at offset %d: %s",
                                rfq_id,
                                line_id,
                                DFF_offset,
                                e,
                            )
                            if metrics:
                                metrics.request_for_quotation_DFFs_failed.labels(
                                    item_type="request_for_quotation_DFFs"
                                ).inc()
                            more_DFFs = False
                            function_succeed = False

                # update metric - for lines
                if metrics:
                    metrics.request_for_quotation_lines_succeeded.labels(item_type="request_for_quotation_lines").inc()

                logger.info(
                    "%d lines extracted for request for quotation: %s. lines ids: %s",
                    len(line_data["items"]),
                    rfq_id,
                    list(line_id_set),
                )

            except (requests.exceptions.HTTPError, requests.exceptions.RequestException, ValueError) as e:
                logger.error("Error querying lines for request for quotation %s at offset %d: %s", rfq_id, offset, e)
                if metrics:
                    metrics.request_for_quotation_lines_failed.labels(item_type="request_for_quotation_lines").inc()
                more_lines = False
                function_succeed = False

    # REQUEST FOR QUOTATION ATTACHMENTS
    for rfq_id in rfq_auction_header_id_set:
        if stop_event.is_set():
            logger.info("Stop event set in get_request_for_quotation_data.")
            return False

        offset = 0
        more_attachments = True
        while more_attachments:
            try:
                data = session.get(
                    config.scm.request_for_quotation_data["attachment_query"].url.format(
                        rfq_header_id=rfq_id, limit=config.scm.query_limit, offset=offset
                    ),
                    timeout=config.scm.timeout,
                )

                data.raise_for_status()
                attachment_data = data.json()
                more_attachments = attachment_data["hasMore"]  # pagination
                offset += config.scm.query_limit

                # parse attachments and add to raw upload queue
                db = config.scm.request_for_quotation_data["attachment_query"].raw_table.database
                table = config.scm.request_for_quotation_data["attachment_query"].raw_table.table
                raw_key = config.scm.request_for_quotation_data["attachment_query"].raw_key
                files_uploaded = 0
                for attachment in attachment_data["items"]:
                    raw_dict = {
                        k: v
                        for k, v in attachment.items()
                        if k not in config.scm.request_for_quotation_data["attachment_query"].omit_fields
                    }
                    raw_dict[config.scm.request_for_quotation_data["request_for_quotation_query"].raw_key] = rfq_id
                    upload_queue.add_to_upload_queue(
                        database=db,
                        table=table,
                        raw_row=Row(key=raw_dict[raw_key], columns=raw_dict),
                    )
                    if attachment.get("DatatypeCode") == "FILE":
                        # check file size
                        if attachment.get("UploadedFileLength") > 100 * 1024 * 1024:
                            logger.warning(
                                "Attachment %s for request for quotation %s is %d MB in size.",
                                attachment.get("AttachedDocumentId"),
                                rfq_id,
                                attachment.get("UploadedFileLength") / (1024 * 1024),
                            )
                        if file_contents_url := next(
                            (item["href"] for item in attachment["links"] if item.get("name") == "FileContents"), None
                        ):
                            try:
                                file_md = create_file_md(
                                    attachment,
                                    raw_key,
                                    config.scm.instance_space_ext_id,
                                    ["scm", "request_for_quotation_attachments"],
                                )

                                contents = session.get(
                                    file_contents_url,
                                    timeout=config.scm.timeout,
                                )

                                files_uploaded += 1
                                file_upload_queue.add_to_upload_queue(content=contents.content, file_meta=file_md)
                            except Exception as e:
                                logger.error(
                                    "Error uploading file for attachment: %s in request for quotation: %s: %s",
                                    attachment.get("AttachedDocumentId"),
                                    rfq_id,
                                    e,
                                )

                # update metric
                if metrics:
                    metrics.request_for_quotation_attachments_succeeded.labels(
                        item_type="request_for_quotation_attachments"
                    ).inc()
                    metrics.request_for_quotation_files_succeeded.labels(item_type="request_for_quotation_files").inc(
                        files_uploaded
                    )

                logger.info(
                    "%d attachments extracted for request for quotation: %s. Uploaded %d files to CDF",
                    len(attachment_data["items"]),
                    rfq_id,
                    files_uploaded,
                )

            except (requests.exceptions.HTTPError, requests.exceptions.RequestException, ValueError) as e:
                logger.error(
                    "Error querying attachments for request for quotation %s at offset %d: %s", rfq_id, offset, e
                )
                if metrics:
                    metrics.request_for_quotation_attachments_failed.labels(
                        item_type="request_for_quotation_attachments"
                    ).inc()
                more_attachments = False
                function_succeed = False

    return function_succeed


def run_extractor(cognite: CogniteClient, states: RawStateStore, config: Config, stop_event: Event) -> None:
    """run_extractor function for SCM extractor.

    Args:
        cognite: To access CDF.
        states: RawStateStore.
        config: The full configuration object containing SCM and Raw settings.
        stop_event: default extractor stop event.

    Returns:
        None

    """
    start_time = time.time()
    run_succeed = True

    extraction_pipeline_ext_id = config.cognite.extraction_pipeline.external_id

    try:
        # Get metrics instance and manually create time series
        metrics = safe_get(SCMMetrics, extractor_name="scm_extractor", extractor_version="1.0.0")
        state_store, last_run_utc, last_run_epoch = create_state_store(cognite, config.statestore)
        current_run_time = datetime.datetime.now(tz=datetime.UTC)

        # supplier data
        logger.info("Getting Supplier Data")
        with (
            RawUploadQueue(cdf_client=cognite, max_queue_size=10_000) as supplier_upload_queue,
            BytesUploadQueue(cdf_client=cognite, max_queue_size=1_000, overwrite_existing=False) as file_upload_queue,
        ):
            supplier_data_succeed = get_supplier_data(
                supplier_upload_queue,
                file_upload_queue,
                config,
                last_run_utc,
                last_run_epoch,
                stop_event,
                metrics,
            )
        if not supplier_data_succeed:
            run_succeed = False

        # check stop event
        if stop_event.is_set():
            logger.info("Stop event set, exiting extractor run.")
            return

        # purchase order data
        logger.info("Getting Purchase Order Data")
        with (
            RawUploadQueue(cdf_client=cognite, max_queue_size=10_000) as po_upload_queue,
            BytesUploadQueue(cdf_client=cognite, max_queue_size=1_000, overwrite_existing=False) as file_upload_queue,
        ):
            po_data_succeed = get_purchase_order_data(
                po_upload_queue,
                file_upload_queue,
                config,
                last_run_utc,
                last_run_epoch,
                stop_event,
                metrics,
            )
        if not po_data_succeed:
            run_succeed = False

        # check stop event
        if stop_event.is_set():
            logger.info("Stop event set, exiting extractor run.")
            return

        # request for quotation data
        logger.info("Getting Request for Quotation Data")
        with (
            RawUploadQueue(cdf_client=cognite, max_queue_size=10_000) as rfq_upload_queue,
            BytesUploadQueue(cdf_client=cognite, max_queue_size=1_000, overwrite_existing=False) as file_upload_queue,
        ):
            rfq_data_succeed = get_request_for_quotation_data(
                rfq_upload_queue,
                file_upload_queue,
                config,
                last_run_utc,
                last_run_epoch,
                stop_event,
                metrics,
            )
        if not rfq_data_succeed:
            run_succeed = False

        # check stop event
        if stop_event.is_set():
            logger.info("Stop event set, exiting extractor run.")
            return

        # synchronize last run time in Raw
        if run_succeed:
            state_store.set_state(external_id="last_run_utc", high=current_run_time.isoformat())
            state_store.set_state(external_id="last_run_epoch", high=current_run_time.timestamp())
            state_store.synchronize()
            cognite.extraction_pipelines.runs.create(
                ExtractionPipelineRunWrite(
                    status="success",
                    extpipe_external_id=extraction_pipeline_ext_id,
                    message="Extraction run completed successfully.",
                )
            )

        else:
            logger.warning("Extraction completed with handled exception. Last run time not updated in Raw.")
            cognite.extraction_pipelines.runs.create(
                ExtractionPipelineRunWrite(
                    status="failure",
                    extpipe_external_id=extraction_pipeline_ext_id,
                    message="Extraction completed with handled exception.",
                )
            )

        # timing metrics
        end_time = time.time()
        duration = end_time - start_time
        metrics.run_duration_seconds.set(duration)
        metrics.finish.set_to_current_time()
        logger.info("Extractor run completed in %.2f seconds", duration)

    except Exception as e:
        logger.exception("Unhandled exception: %s", e)
        cognite.extraction_pipelines.runs.create(
            ExtractionPipelineRunWrite(
                status="failure",
                extpipe_external_id=extraction_pipeline_ext_id,
                message="Extraction completed with unhandled exception.",
            )
        )
