"""Functions for custom Aveva UE technical data extractor."""

import datetime
import json
import logging
import threading
import time
from logging import getLogger
from queue import Empty, Queue
from threading import Event
from urllib.parse import unquote

import requests
from cognite.client import CogniteClient
from cognite.client.data_classes import ExtractionPipelineRunWrite, Row
from cognite.extractorutils.metrics import safe_get
from cognite.extractorutils.statestore import RawStateStore
from cognite.extractorutils.uploader import RawUploadQueue

from aveva_technical_extractor.config import Config, RawConfig
from aveva_technical_extractor.metrics import AvevaUEMetrics

logger = getLogger(__name__)


def get_data_request(token: str, datasource: str, category: str, table: str) -> tuple[bool, str]:
    """Get an acknowledgement ID for an Aveva data source.

    Args:
        token: Bearer token for authentication.
        datasource: Name of the Aveva data source.
        category: Name of the category.
        table: Name of the table.

    Returns:
        A tuple containing:
        - A boolean indicating if the request succeeded.
        - Acknowledgement ID as a string if available, otherwise an empty string.

    """
    url = f"https://eu.ais.connect.aveva.com/data/api/v1.1/datasources/{category}/{datasource}/datarequest"
    headers = {
        "Authorization": f"Bearer {token}",
        "User-Agent": "CogniteExtractor/0.0.1",
    }
    response = requests.post(url, headers=headers, json={"path": f"/tables('{table}')"})

    if response.status_code == 200 or response.status_code == 202:
        acknowledgement_id = response.json().get("acknowledgementId")
        return True, acknowledgement_id
    return False, ""


def check_acknowledgement_status(
    token: str,
    config: Config,
    datasource: str,
    category: str,
    table_name: str,
    acknowledgement_id: str,
    chunk_queue: Queue,
    stop_event: threading.Event,
) -> bool:
    """Check the status of an Aveva data request using the acknowledgement ID.

    If chunks are available, they are added to the provided chunk queue for processing.

    Args:
        token: Bearer token for authentication.
        config: Extractor configuration object.
        datasource: Name of the data source.
        category: Name of the category.
        table_name: Name of the table.
        acknowledgement_id: Acknowledgement ID to check.
        chunk_queue: A queue to add chunks for processing.
        stop_event: A threading event to signal stopping the process.

    Returns:
        A boolean indicating if the request succeeded.

    """
    url = f"https://eu.ais.connect.aveva.com/data/api/v1.1/datasources/{category}/{datasource}/acknowledgements/{acknowledgement_id}"
    headers = {
        "Authorization": f"Bearer {token}",
        "User-Agent": "CogniteExtractor/0.0.1",
    }

    # configs
    sleep_interval = config.aveva.retry_internal_seconds
    max_iterations = config.aveva.max_retry_attempts

    # initialize
    chunk_to_upload = 1
    tries = 0
    iterations = 0
    logger.info(
        "Checking acknowledgement status for category: %s - datasource: %s - acknowledgment ID: %s",
        category,
        datasource,
        acknowledgement_id,
    )

    while not stop_event.is_set():
        response = requests.get(url, headers=headers)

        if response.status_code not in [200, 202]:  # get aknowledgement status
            logger.error(
                "Error for status category: %s - datasource: %s - table name: %s acknowledgment ID: %s - "
                "status code: %s",
                category,
                datasource,
                table_name,
                acknowledgement_id,
                response.status_code,
            )
            return False

        # confirm correct chunks available
        md = json.loads(response.json()["metadata"])
        total_number_of_chunks = md.get("TotalNumberOfChunks")
        chunks_available = md.get("ChunksAvailable")

        # confirm chunks are available for queuing
        if not total_number_of_chunks:
            if tries == max_iterations:
                logger.error(
                    "Max tries reached for category: %s - datasource: %s - table name: %s acknowledgment ID: %s, "
                    "no chunks available, exiting loop",
                    category,
                    datasource,
                    table_name,
                    acknowledgement_id,
                )
                return False
            tries += 1
            iterations += 1
            if stop_event.wait(timeout=sleep_interval):
                logger.info("Stop signal received during check_acknowledgement_status.")
                return False
            continue

        # begin queueing chunks that are available
        if chunks_available >= chunk_to_upload:
            for chunk_index in range(chunk_to_upload, chunks_available + 1):
                # add chunk to upload queue here
                logger.info(
                    "Adding chunk %d of %d to upload queue for category: %s - datasource: %s - table name: %s - "
                    "acknowledgment ID: %s",
                    chunk_index,
                    total_number_of_chunks,
                    category,
                    datasource,
                    table_name,
                    acknowledgement_id,
                )
                chunk_queue.put((category, datasource, table_name, acknowledgement_id, chunk_index))
                chunk_to_upload += 1
            if stop_event.wait(timeout=sleep_interval):
                logger.info("Stop signal received during check_acknowledgement_status.")
                return False
            continue

        # exit loop of all chunks have been queued
        if chunk_to_upload > total_number_of_chunks:
            logger.info(
                "All %d chunks uploaded to queue for category: %s - datasource: %s - table name: %s - "
                "acknowledgment ID: %s",
                total_number_of_chunks,
                category,
                datasource,
                table_name,
                acknowledgement_id,
            )
            break
        if stop_event.wait(timeout=sleep_interval):
            logger.info("Stop signal received during check_acknowledgement_status.")
            return False
        iterations += 1

        # Prevent infinite loop
        if iterations == 3 * max_iterations:
            logger.error(
                "Max iterations reached, exiting loop for category: %s - datasource: %s - acknowledgment ID: %s",
                category,
                datasource,
                acknowledgement_id,
            )
            return False
    return True


def queue_chunks_for_upload(
    token: str,
    config: Config,
    metrics: AvevaUEMetrics,
    num_worker_threads: int,
    chunk_queue: Queue,
    queue_chunks_failures: Queue,
    stop_event: threading.Event,
) -> None:
    """Iterate through data sources one at a time, get an acknowledgement ID and check if chunks are available.

    If chunks are available, they are individually added to the chunk queue for processing.

    Args:
        token: Bearer token for authentication.
        config: Extractor configuration object.
        metrics: Metrics instance for tracking.
        num_worker_threads: Number of worker threads.
        chunk_queue: A queue to add chunks for processing.
        queue_chunks_failures: A queue to log any failures during chunk queuing.
        stop_event: A threading event to signal stopping the process.

    Returns:
        None

    """
    # cycle through data sources one at a time
    for source_config in config.aveva.source_configs:
        datasource = source_config.datasource
        category = source_config.category
        table_name = source_config.table_name

        if stop_event.is_set():
            logger.info(
                "Stop event set in queue_chunks_for_upload, "
                "exiting chunk queuing for datasource: %s - category: %s - table: %s",
                datasource,
                category,
                table_name,
            )
            break

        logger.info("Running on datasource: %s - category: %s - table: %s", datasource, category, table_name)

        # get data request
        metrics.sources_attempted.labels(item_type="sources").inc()
        data_request_succeed, acknowledgement_id = get_data_request(token, datasource, category, table_name)
        if data_request_succeed:
            metrics.sources_succeeded.labels(item_type="sources").inc()
        else:
            metrics.sources_errors.labels(item_type="sources").inc()
            logger.error(
                "Data request failed for datasource: %s - category: %s - table: %s. Continuing to next source.",
                datasource,
                category,
                table_name,
            )
            # enqueue failure record
            queue_chunks_failures.put(
                {
                    "function": "get_data_request",
                    "datasource": datasource,
                    "category": category,
                    "table_name": table_name,
                    "acknowledgement_id": "",
                }
            )
            continue

        # check acknowledgement status
        metrics.acknowledgements_attempted.labels(item_type="acknowledgements").inc()
        acknowledgement_succeed = check_acknowledgement_status(
            token, config, datasource, category, table_name, acknowledgement_id, chunk_queue, stop_event
        )
        if acknowledgement_succeed:
            metrics.acknowledgements_succeeded.labels(item_type="acknowledgements").inc()
        else:
            metrics.acknowledgements_errors.labels(item_type="acknowledgements").inc()
            logger.error(
                "Acknowledgement status check failed for datasource: %s - category: %s - ack id: %s. "
                "Continuing to next source.",
                datasource,
                category,
                acknowledgement_id,
            )
            queue_chunks_failures.put(
                {
                    "function": "check_acknowledgement_status",
                    "datasource": datasource,
                    "category": category,
                    "table_name": table_name,
                    "acknowledgement_id": acknowledgement_id,
                }
            )
            continue

    # signal end of queuing
    for _ in range(num_worker_threads):
        chunk_queue.put(None)


def process_chunks(
    token: str,
    config: Config,
    metrics: AvevaUEMetrics,
    chunk_queue: Queue,
    process_failures: Queue,
    raw_upload_queue: RawUploadQueue,
    stop_event: threading.Event,
) -> None:
    """Process individual data chunks, request data from Aveva and add to CDF raw upload queue.

    Args:
        token: Bearer token for authentication.
        config: Extractor configuration object.
        metrics: Metrics instance for tracking.
        chunk_queue: A queue to add chunks for processing.
        process_failures: A queue to log any failures during chunk processing.
        raw_upload_queue: RawUploadQueue to add rows for uploading to CDF Raw.
        stop_event: A threading event to signal stopping the process.

    Returns:
        None

    """
    while not stop_event.is_set():
        try:
            chunk = chunk_queue.get(timeout=1)
        except Empty:
            continue

        metrics.chunks_attempted.labels(item_type="chunks").inc()
        # no more chunks to process
        if chunk is None:
            chunk_queue.task_done()
            break
        try:
            if stop_event.is_set():
                logging.info("Stop signal received during process_chunks.")
                chunk_queue.task_done()
                break

            category, datasource, table_name, acknowledgement_id, chunk_index = chunk
            logger.info(
                "Processing chunk %d for datasource: %s - category: %s - table name: %s - acknowledgement_id: %s",
                chunk_index,
                datasource,
                category,
                table_name,
                acknowledgement_id,
            )

            # Retrieve chunk data
            url = f"https://eu.ais.connect.aveva.com/data/api/v1.1/datasources/{category}/{datasource}/acknowledgements/{acknowledgement_id}/chunks/{chunk_index}"
            headers = {
                "Authorization": f"Bearer {token}",
                "User-Agent": "CogniteExtractor/0.0.1",
            }
            response = requests.get(url, headers=headers)

            response.raise_for_status()  # Raise an error for bad status codes

            # parse data and add to Raw
            data_string = response.json()["dataDto"][0]["data"]
            data_list = json.loads(data_string)
            raw_table_name = f"{datasource}:{category}:{table_name}"
            db_name = config.raw.storage_db
            for data_row, entry in enumerate(data_list, start=1):  # cycle through rows of data
                raw_key = f"{chunk_index}:{data_row}"
                output_dict = {unquote(col_name): unquote(col_value) for col_name, col_value in entry.items()}
                raw_row = Row(key=raw_key, columns=output_dict)
                # add raw_row to Raw upload queue here
                raw_upload_queue.add_to_upload_queue(database=db_name, table=raw_table_name, raw_row=raw_row)
            metrics.chunks_succeeded.labels(item_type="chunks").inc()

        except Exception as e:
            metrics.chunks_errors.labels(item_type="chunks").inc()
            logger.error(
                "Error processing chunk %s for datasource: %s - category: %s - acknowledgement_id: %s - Error: %s",
                chunk_index,
                datasource,
                category,
                acknowledgement_id,
                e,
            )

            # update failure_queue
            failure_item = {
                "chunk_index": chunk_index,
                "datasource": datasource,
                "category": category,
                "acknowledgement_id": acknowledgement_id,
                "exception": str(e),
            }
            process_failures.put(failure_item)
        finally:
            chunk_queue.task_done()


def create_state_store(client: CogniteClient, config: RawConfig) -> tuple[RawStateStore, str, float, datetime.datetime]:
    """Creates a RawStateStore in CDF to store the last run time of the last succcessful extractor run.

    Args:
        client: To access Statestore in CDF Raw
        config: The full configuration object containing ProArc and Raw settings.

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
    current_utc_time = datetime.datetime.now(tz=datetime.UTC)

    return state_store, last_run_utc, last_run_epoch, current_utc_time


def run_extractor(cognite: CogniteClient, states: RawStateStore, config: Config, stop_event: Event) -> None:
    """run_extractor function for Aveva technical extractor.

    Args:
        cognite: To access CDF.
        states: RawStateStore.
        config: The full configuration object containing Aveva and Raw settings.
        stop_event: default extractor stop event.

    Returns:
        None

    """
    start_time = time.time()
    run_succeed = True
    extraction_pipeline_ext_id = config.cognite.extraction_pipeline.external_id

    # create threads
    chunk_queue: Queue[tuple[str, str, str, str, int]] = Queue()
    process_failures: Queue[dict[str, str | int]] = Queue()
    queue_chunks_failures: Queue[dict[str, str]] = Queue()
    num_worker_threads = config.aveva.number_worker_threads

    try:
        # Get metrics instance and manually create last run time
        metrics = safe_get(AvevaUEMetrics, extractor_name="aveva_technical_extractor", extractor_version="1.0.0")
        state_store, _last_run_utc, _last_run_epoch, current_run_time = create_state_store(cognite, config.raw.last_run)
        token = config.aveva.bearer_token

        # START DATA PROCESSING
        with RawUploadQueue(cdf_client=cognite, max_queue_size=10_000) as raw_upload_queue:
            # start chunk queuing thread
            queue_chunks_thread = threading.Thread(
                target=queue_chunks_for_upload,
                args=(token, config, metrics, num_worker_threads, chunk_queue, queue_chunks_failures, stop_event),
            )
            queue_chunks_thread.start()

            # start chunk processing threads
            chunk_processing_threads = [
                threading.Thread(
                    target=process_chunks,
                    args=(token, config, metrics, chunk_queue, process_failures, raw_upload_queue, stop_event),
                )
                for _ in range(num_worker_threads)
            ]
            for thread in chunk_processing_threads:
                thread.start()

            queue_chunks_thread.join()
            chunk_queue.join()
            for thread in chunk_processing_threads:
                thread.join()

        if stop_event.is_set():
            logger.info("Stop signal received in run_extractor. Exiting extractor run.")
            return

        # CHECK FAILURE QUEUES
        # check for queue_chunks failures
        queue_failures = []
        while not queue_chunks_failures.empty():
            queue_failures.append(queue_chunks_failures.get_nowait())

        if queue_failures:
            logger.warning("Extractor run contained chunk queuing failures.")
            run_succeed = False
        else:
            logger.info("Extractor run completed with no chunk queuing failures.")

        # check for processing failures
        failures = []
        while not process_failures.empty():
            failures.append(process_failures.get_nowait())

        if failures:
            logger.warning("Extractor run contained chunk processing failures.")
            run_succeed = False
        else:
            logger.info("Extractor run completed with no chunk processing failures.")

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
                    message="Extraction run completed with handled exceptions.",
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
