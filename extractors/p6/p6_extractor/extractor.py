"""P6 EPPM REST API extraction and CDF Raw upload."""

import datetime
import logging
import time
from threading import Event
from typing import Any
from urllib.parse import urljoin

import requests
from cognite.client import CogniteClient
from cognite.client.data_classes import ExtractionPipelineRunWrite, Row
from cognite.extractorutils.metrics import safe_get
from cognite.extractorutils.statestore import RawStateStore
from cognite.extractorutils.uploader import RawUploadQueue

from p6_extractor.config import Config, QueryConfig
from p6_extractor.metrics import P6Metrics

logger = logging.getLogger(__name__)


def _extract_records(
    session: requests.Session,
    upload_queue: RawUploadQueue,
    config: Config,
    query: QueryConfig,
    item_type: str,
    metrics: P6Metrics,
    stop_event: Event,
) -> bool:
    """Fetch configured pages for one P6 resource and queue them in CDF Raw."""
    url = urljoin(f"{config.p6.base_url.rstrip('/')}/", query.endpoint.lstrip("/"))
    offset = 0
    succeeded = True

    while not stop_event.is_set():
        params: dict[str, int] = {}
        if query.limit_parameter:
            params[query.limit_parameter] = config.p6.query_limit
        if query.offset_parameter:
            params[query.offset_parameter] = offset

        try:
            response = session.get(url, params=params, timeout=config.p6.timeout)
            response.raise_for_status()
            payload: Any = response.json()
            if isinstance(payload, list):
                records = payload
                has_more = bool(query.offset_parameter) and len(records) == config.p6.query_limit
            elif isinstance(payload, dict):
                records = payload.get(query.response_items_key)
                if not isinstance(records, list):
                    raise ValueError(
                        f"Expected a list at response key {query.response_items_key!r} "
                        f"for P6 {item_type} endpoint"
                    )
                has_more = bool(payload.get(query.has_more_field, False)) if query.has_more_field else False
            else:
                raise ValueError(f"Expected a JSON list or object for P6 {item_type} endpoint")

            metrics.records_identified.labels(item_type=item_type).inc(len(records))
            for record in records:
                if not isinstance(record, dict):
                    raise ValueError(f"Expected each P6 {item_type} record to be a JSON object")
                if query.raw_key not in record or record[query.raw_key] is None:
                    raise KeyError(f"P6 {item_type} record is missing configured key {query.raw_key!r}")
                upload_queue.add_to_upload_queue(
                    database=query.raw_table.database,
                    table=query.raw_table.table,
                    raw_row=Row(key=str(record[query.raw_key]), columns=record),
                )
            metrics.records_uploaded.labels(item_type=item_type).inc(len(records))

            if not has_more:
                break
            if not records:
                raise ValueError(f"P6 {item_type} endpoint indicated more pages but returned no records")
            if not query.offset_parameter:
                raise ValueError(
                    f"P6 {item_type} endpoint indicated more pages, but no offset parameter is configured"
                )
            offset += len(records)
        except (requests.exceptions.RequestException, ValueError, KeyError) as error:
            logger.exception("Failed to extract P6 %s records", item_type)
            metrics.query_errors.labels(item_type=item_type).inc()
            succeeded = False
            break

    if stop_event.is_set():
        logger.info("Stop signal received while extracting P6 %s records", item_type)
        return False
    return succeeded


def run_extractor(cognite: CogniteClient, states: RawStateStore, config: Config, stop_event: Event) -> None:
    """Run one complete P6 project and activity extraction."""
    del states  # State is maintained in the configured Raw state store below.
    start_time = time.monotonic()
    extraction_pipeline_ext_id = config.cognite.extraction_pipeline.external_id
    state_store = RawStateStore(
        cdf_client=cognite,
        database=config.raw.last_run.database,
        table=config.raw.last_run.table,
    )
    metrics = safe_get(P6Metrics, extractor_name="p6_extractor", extractor_version="1.0.0")

    try:
        state_store.initialize()
        current_run_time = datetime.datetime.now(tz=datetime.UTC)
        session = requests.Session()
        session.auth = (config.p6.user_name, config.p6.password)
        session.headers.update({"Accept": "application/json", "User-Agent": "CogniteExtractor/1.0.0"})

        run_succeeded = True
        for item_type, query in (
            ("projects", config.p6.project_data),
            ("activities", config.p6.activity_data),
        ):
            if stop_event.is_set():
                logger.info("Stop signal received; ending P6 extraction")
                return
            logger.info("Extracting P6 %s", item_type)
            with RawUploadQueue(cdf_client=cognite, max_queue_size=10_000) as upload_queue:
                if not _extract_records(session, upload_queue, config, query, item_type, metrics, stop_event):
                    run_succeeded = False

        if stop_event.is_set():
            logger.info("Stop signal received; state timestamp will not be advanced")
            return

        if run_succeeded:
            state_store.set_state(external_id="last_run_utc", high=current_run_time.isoformat())
            state_store.set_state(external_id="last_run_epoch", high=current_run_time.timestamp())
            state_store.synchronize()
            status = "success"
            message = "P6 project and activity extraction completed successfully."
        else:
            status = "failure"
            message = "P6 extraction completed with one or more endpoint errors."

        cognite.extraction_pipelines.runs.create(
            ExtractionPipelineRunWrite(
                status=status,
                extpipe_external_id=extraction_pipeline_ext_id,
                message=message,
            )
        )
        metrics.run_duration_seconds.set(time.monotonic() - start_time)
        metrics.finish.set_to_current_time()
    except Exception:
        logger.exception("Unhandled error during P6 extraction")
        cognite.extraction_pipelines.runs.create(
            ExtractionPipelineRunWrite(
                status="failure",
                extpipe_external_id=extraction_pipeline_ext_id,
                message="P6 extraction failed with an unhandled exception.",
            )
        )
