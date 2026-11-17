"""RMS extractor implementation."""

import datetime as dt
import json
import re
from decimal import Decimal
from logging import getLogger
from typing import Any

from cognite.client import CogniteClient
from cognite.client.data_classes import ExtractionPipelineRunWrite, Row
from cognite.extractorutils.metrics import safe_get
from cognite.extractorutils.statestore import RawStateStore
from cognite.extractorutils.uploader import RawUploadQueue
from sqlalchemy import create_engine, text

from rms_extractor.config import Config
from rms_extractor.metrics import RMSMetrics

logger = getLogger(__name__)
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def create_state_store(client: CogniteClient, config) -> RawStateStore:
    """Initialize the CDF Raw state store."""
    state_store = RawStateStore(cdf_client=client, database=config.database, table=config.table)
    state_store.initialize()
    return state_store


def get_db_engine(db_url: str):
    """Build a SQLAlchemy engine; switching DB vendors is done by changing RMS_DB_URL."""
    options: dict[str, Any] = {"pool_pre_ping": True}
    if db_url.startswith("sqlite"):
        options["connect_args"] = {"check_same_thread": False, "timeout": 30}
    return create_engine(db_url, **options)


def serialize_value(value: Any) -> Any:
    """Convert common SQL driver values to JSON-compatible Raw column values."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Decimal):
        # Preserve numeric precision in Raw rather than converting to a potentially lossy float.
        return str(value)
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.hex()
    return json.dumps(value, default=str, ensure_ascii=False)


def get_row_key(table_name: str, row: dict[str, Any], config: Config) -> str:
    """Use the configured table primary key, with stable composite-key fallbacks."""
    key_field = config.rms.raw_key_fields.get(table_name)
    key_value = row.get(key_field) if key_field else None
    if key_value is not None:
        return str(key_value)

    if table_name in {"risk_cycle", "risk_action"}:
        risk_id = row.get("risk_id")
        year_month = row.get("year_month")
        suffix = row.get("action_no") if table_name == "risk_action" else None
        if risk_id is not None and year_month is not None:
            return f"{risk_id}:{year_month}" + (f":{suffix}" if suffix is not None else "")

    raise ValueError(f"No usable configured key for row in table {table_name!r}")


def iter_table_batches(engine, table_name: str, batch_size: int):
    """Stream table records in bounded batches to avoid loading the full DB into memory."""
    if not _IDENTIFIER.fullmatch(table_name):
        raise ValueError(f"Invalid SQL table identifier: {table_name!r}")

    with engine.connect() as connection:
        result = connection.execution_options(stream_results=True).execute(text(f"SELECT * FROM {table_name}"))
        while batch := result.mappings().fetchmany(batch_size):
            yield batch


def upload_table_rows(upload_queue: RawUploadQueue, table_name: str, database: str, rows, config: Config) -> int:
    """Queue one batch of RMS records for CDF Raw upload."""
    count = 0
    for source_row in rows:
        row = dict(source_row)
        columns = {name: serialize_value(value) for name, value in row.items()}
        upload_queue.add_to_upload_queue(
            database=database,
            table=table_name,
            raw_row=Row(key=get_row_key(table_name, row, config), columns=columns),
        )
        count += 1
    return count


def run_extractor(cognite: CogniteClient, states: RawStateStore, config: Config, stop_event) -> None:
    """Read configured RMS tables and upload them to Cognite Raw."""
    start_time = dt.datetime.now(tz=dt.timezone.utc)
    pipeline_external_id = config.cognite.extraction_pipeline.external_id
    metrics = safe_get(RMSMetrics, extractor_name="rms_extractor", extractor_version="1.0.0")
    state_store = create_state_store(cognite, config.raw)
    engine = get_db_engine(config.rms.db.url)

    # Restrict table names to known RMS model tables; also validate the SQL identifier.
    allowed_tables = {"risk_master", "risk_cycle", "risk_action"}
    unknown_tables = set(config.rms.table_names) - allowed_tables
    if unknown_tables:
        engine.dispose()
        raise ValueError(f"Unsupported RMS table(s): {', '.join(sorted(unknown_tables))}")

    try:
        with RawUploadQueue(cdf_client=cognite, max_queue_size=10_000) as upload_queue:
            for table_name in config.rms.table_names:
                if stop_event.is_set():
                    logger.info("Stop signal received before processing table %s.", table_name)
                    return

                total = 0
                for batch in iter_table_batches(engine, table_name, config.rms.db.batch_size):
                    if stop_event.is_set():
                        logger.info("Stop signal received while processing table %s.", table_name)
                        return
                    total += upload_table_rows(upload_queue, table_name, config.rms.raw_database, batch, config)

                if table_name == "risk_master":
                    metrics.risk_master_rows_read.labels(item_type=table_name).inc(total)
                    metrics.risk_master_rows_uploaded.labels(item_type=table_name).inc(total)
                elif table_name == "risk_cycle":
                    metrics.risk_cycle_rows_read.labels(item_type=table_name).inc(total)
                    metrics.risk_cycle_rows_uploaded.labels(item_type=table_name).inc(total)

                logger.info("Queued %d rows from RMS table %s.", total, table_name)

        completed_at = dt.datetime.now(tz=dt.timezone.utc)
        state_store.set_state(external_id="last_run_utc", high=completed_at.isoformat())
        state_store.set_state(external_id="last_run_epoch", high=completed_at.timestamp())
        state_store.synchronize()
        cognite.extraction_pipelines.runs.create(
            ExtractionPipelineRunWrite(
                status="success",
                extpipe_external_id=pipeline_external_id,
                message="RMS ingestion completed successfully.",
            )
        )
        metrics.run_duration_seconds.set((completed_at - start_time).total_seconds())
        metrics.finish.set_to_current_time()
        logger.info("RMS extraction completed successfully.")
    except Exception as exc:
        logger.exception("RMS extraction failed: %s", exc)
        cognite.extraction_pipelines.runs.create(
            ExtractionPipelineRunWrite(
                status="failure",
                extpipe_external_id=pipeline_external_id,
                message="RMS ingestion failed. See extractor logs for details.",
            )
        )
        raise
    finally:
        engine.dispose()
