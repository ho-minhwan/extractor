"""RMS extractor implementation."""

import datetime
import json
from logging import getLogger

from cognite.client import CogniteClient
from cognite.client.data_classes import ExtractionPipelineRunWrite, Row
from cognite.extractorutils.metrics import safe_get
from cognite.extractorutils.statestore import RawStateStore
from cognite.extractorutils.uploader import RawUploadQueue
from sqlalchemy import create_engine, text

from rms_extractor.config import Config
from rms_extractor.metrics import RMSMetrics

logger = getLogger(__name__)


def create_state_store(client: CogniteClient, config) -> tuple[RawStateStore, str, float]:
    """Create the state store used to track successful execution."""
    state_store = RawStateStore(cdf_client=client, database=config.database, table=config.table)
    state_store.initialize()
    last_run_utc = state_store.get_state(external_id="last_run_utc")[1]
    last_run_epoch = state_store.get_state(external_id="last_run_epoch")[1]
    return state_store, last_run_utc, last_run_epoch


def _serialize_value(value):
    if value is None:
        return None
    if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
        return value.isoformat()
    if isinstance(value, (str, int, float, bool)):
        return value
    return json.dumps(value, default=str, ensure_ascii=False)


def get_db_engine(db_url: str):
    """Build a SQLAlchemy engine for the configured RMS database."""
    engine_kwargs = {}
    if db_url.startswith("sqlite"):
        engine_kwargs["connect_args"] = {"check_same_thread": False}
    return create_engine(db_url, **engine_kwargs)


def fetch_table_rows(engine, table_name: str):
    """Return all rows from a table as a list of dictionaries."""
    with engine.connect() as conn:
        result = conn.execute(text(f"SELECT * FROM {table_name}"))
        rows = result.mappings().all()
    return rows


def get_table_key(table_name: str, row: dict, config: Config) -> str:
    """Build a unique raw row key for the table."""
    raw_key_field = config.rms.raw_key_fields.get(table_name, "id")
    value = row.get(raw_key_field)
    if value is not None:
        return str(value)

    if table_name == "risk_cycle":
        risk_id = row.get("risk_id")
        year_month = row.get("year_month")
        if risk_id is not None and year_month is not None:
            return f"{risk_id}:{year_month}"

    return f"{table_name}:{hash(str(row))}"


def upload_table_rows(upload_queue: RawUploadQueue, table_name: str, db_name: str, rows: list[dict], config: Config):
    """Upload rows from a single RMS table to CDF Raw."""
    for row in rows:
        payload = {
            key: _serialize_value(value)
            for key, value in row.items()
            if value is not None
        }
        upload_queue.add_to_upload_queue(
            database=db_name,
            table=table_name,
            raw_row=Row(key=get_table_key(table_name, payload, config), columns=payload),
        )


def run_extractor(cognite: CogniteClient, states: RawStateStore, config: Config, stop_event) -> None:
    """Read RMS database tables and upload them to Cognite Raw."""
    start_time = datetime.datetime.now(tz=datetime.timezone.utc)
    extraction_pipeline_ext_id = config.cognite.extraction_pipeline.external_id

    metrics = safe_get(RMSMetrics, extractor_name="rms_extractor", extractor_version="1.0.0")
    state_store, _last_run_utc, _last_run_epoch = create_state_store(cognite, config.raw)

    db_url = config.rms.db.url
    engine = get_db_engine(db_url)
    try:
        with RawUploadQueue(cdf_client=cognite, max_queue_size=10_000) as upload_queue:
            for table_name in config.rms.table_names:
                if stop_event.is_set():
                    logger.info("Stop signal received. Exiting before table %s upload.", table_name)
                    return

                rows = fetch_table_rows(engine, table_name)
                metrics.risk_master_rows_read.labels(item_type=table_name).inc(len(rows)) if table_name == "risk_master" else None
                metrics.risk_cycle_rows_read.labels(item_type=table_name).inc(len(rows)) if table_name == "risk_cycle" else None

                if not rows:
                    logger.info("No rows found in table %s.", table_name)
                    continue

                upload_table_rows(upload_queue, table_name, config.rms.raw_database, rows, config)

                if table_name == "risk_master":
                    metrics.risk_master_rows_uploaded.labels(item_type="risk_master").inc(len(rows))
                elif table_name == "risk_cycle":
                    metrics.risk_cycle_rows_uploaded.labels(item_type="risk_cycle").inc(len(rows))

                logger.info("Uploaded %d rows from table %s to Raw.", len(rows), table_name)

        state_store.set_state(external_id="last_run_utc", high=start_time.isoformat())
        state_store.set_state(external_id="last_run_epoch", high=start_time.timestamp())
        state_store.synchronize()
        cognite.extraction_pipelines.runs.create(
            ExtractionPipelineRunWrite(
                status="success",
                extpipe_external_id=extraction_pipeline_ext_id,
                message="RMS ingestion completed successfully.",
            )
        )

        end_time = datetime.datetime.now(tz=datetime.timezone.utc)
        metrics.run_duration_seconds.set((end_time - start_time).total_seconds())
        logger.info("RMS extraction completed successfully.")

    except Exception as e:
        logger.exception("Unhandled exception: %s", e)
        cognite.extraction_pipelines.runs.create(
            ExtractionPipelineRunWrite(
                status="failure",
                extpipe_external_id=extraction_pipeline_ext_id,
                message=f"RMS ingestion failed: {e}",
            )
        )
        raise
    finally:
        engine.dispose()
