"""Functions for custom ProArc file extractor."""

import datetime
import time
from logging import getLogger
from mimetypes import guess_type
from threading import Event
from typing import Any

import requests
from cognite.client import CogniteClient
from cognite.client.data_classes import ExtractionPipelineRunWrite, Row
from cognite.client.data_classes.data_modeling import NodeId
from cognite.client.data_classes.data_modeling.extractor_extensions.v1 import CogniteExtractorFileApply
from cognite.extractorutils.metrics import safe_get
from cognite.extractorutils.statestore import RawStateStore
from cognite.extractorutils.uploader import BytesUploadQueue, RawUploadQueue

from proarc_extractor.config import Config, ProArcConfig, ProArcRepoConfig, RawConfig
from proarc_extractor.metrics import ProArcMetrics

# DEBUG
import json
################

logger = getLogger(__name__)


def _build_criteria(criteria: dict[str, Any], variable_swap: dict[str, str]) -> dict[str, Any]:
    """Replace values in criteria dict with variable_swap values if matched.

    Args:
        criteria: Dictionary of criteria to process.
        variable_swap: Dictionary of variable replacements.

    Returns:
        dict: The updated criteria dictionary with replacements applied.

    """
    from typing import Any

    def swap_value(val: Any) -> Any:
        if isinstance(val, str):
            return variable_swap.get(val, val)
        return val

    def recursive_swap(d: Any) -> Any:
        if isinstance(d, dict):
            return {k: recursive_swap(v) for k, v in d.items()}
        if isinstance(d, list):
            return [recursive_swap(v) for v in d]
        return swap_value(d)

    return recursive_swap(criteria)  # type: ignore[no-any-return]


def _file_size(file: dict) -> int | str:
    """Return FI_SIZE from the file root, falling back to metaItems."""
    size = file.get("FI_SIZE")
    if size is None:
        meta = file.get("metaItems") or {}
        if isinstance(meta, dict):
            size = meta.get("FI_SIZE", 0)
        else:
            size = 0
    return size


def get_raw_keys(client: CogniteClient, db_name: str, table_name: str) -> list[str]:
    """Get a list of keys in raw table.

    Args:
        client: To access CDF
        db_name: Raw database name
        table_name: Raw table name

    Returns:
        raw_keys: List of raw row keys from the raw table

    """
    row_list = client.raw.rows.list(db_name, table_name, limit=None)
    return [row.key for row in row_list]


def delete_raw_rows(client: CogniteClient, db_name: str, table_name: str, raw_keys: list[str]) -> bool:
    """Delete rows from a raw table.

    Args:
        client: To access CDF
        db_name: Raw database name
        table_name: Raw table name
        raw_keys: List of raw row keys to delete
    Returns:
        bool: True if deletion was successful, False otherwise.

    """
    try:
        if raw_keys:
            client.raw.rows.delete(db_name, table_name, raw_keys)
            logger.info("Deleted %d rows from %s", len(raw_keys), table_name)
            logger.info("Rows deleted: %s", raw_keys)
            return True
        logger.info("No rows to delete from %s", table_name)
        return True

    except Exception as e:
        logger.error("Error deleting raw rows for %s: %s", table_name, e)
        return False


def delete_nodes(client: CogniteClient, node_id_list: list[NodeId]) -> bool:
    """Delete Nodes from CDF.

    Args:
        client: To access CDF
        node_id_list: List of NodeIds to delete
    Returns:
        bool: True if deletion was successful, False otherwise.

    """
    try:
        if node_id_list:
            client.data_modeling.instances.delete(node_id_list)
            logger.info("Deleted %d Nodes from CDF:%s", len(node_id_list), node_id_list)
            return True
        logger.info("No Nodes to delete from CDF")
        return True

    except Exception as e:
        logger.error("Error deleting Nodes from CDF: %s,%s", node_id_list, e)
        return False


def get_proarc_token(config: ProArcConfig) -> str:
    """Get access token from ProArc API.

    Args:
        config: The full configuration object containing ProArc and Raw settings.

    Returns:
        access_token: ProArc token for API authentication.

    """
    data = requests.post(
        config.access_token_url,
        data=f"grant_type=password&username={config.user_name}&password={config.password}",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Authorization": f"Basic {config.access_token_creds}",
        },
    )
    data_json: dict[str, str] = data.json()
    return data_json["access_token"]


def create_state_store(client: CogniteClient, config: RawConfig) -> tuple[RawStateStore, str, float]:
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

    return state_store, last_run_utc, last_run_epoch


def search_documents(
    client: CogniteClient,
    access_token: str,
    upload_queue: RawUploadQueue,
    config: Config,
    last_run_utc: str,
    last_run_epoch: float,
    variable_swap_dict: dict,
    stop_event: Event,
    mode: str,
    metrics: ProArcMetrics = None,
) -> tuple[list[str], datetime.datetime, bool]:
    """Searches ProArc for approved documents modified since the last run and uploads metadata.

    Args:
        client: To access CDF
        access_token: ProArc token for API authentication.
        upload_queue: Queue for uploading document metadata to CDF Raw.
        config: The full configuration object containing ProArc and Raw settings.
        last_run_utc: The last successful extractor run time in UTC.
        last_run_epoch: The last successful extractor run epoch time.
        variable_swap_dict: dict for variable replacements in search criteria.
        stop_event: Event to signal stopping the search.
        mode: Extractor mode, either normal or sync.
        metrics: ProArcMetrics object for tracking metrics (optional).

    Returns:
        A tuple containing:
        - list[str]: A list of ProArc document Rnos modified since the last extractor run.
        - datetime.datetime: The current UTC time when the search API submitted.
        - bool: True if the document search and metadata extraction succeeded, False otherwise.

    """
    document_rnos_added = set()  # new document rnos added to raw (will match extracted in normal mode)
    document_rnos_extracted = set()  # full list of document rnos extracted from proarc
    raw_entries_extracted = set()  # raw keys extracted from proarc - needed incase raw key does not match document rno
    raw_entries_delete = []  # raw keys marked for deletion
    current_utc_time = datetime.datetime.now(tz=datetime.UTC)

    # Build search criteria from config
    if mode == "sync":
        search_criteria = _build_criteria(
            criteria=config.proarc.sync_documents_criteria, variable_swap=variable_swap_dict
        )
    else:
        search_criteria = _build_criteria(
            criteria=config.proarc.search_documents_criteria, variable_swap=variable_swap_dict
        )

    # pull data from existing raw table
    raw_table_name = (
        f"{variable_swap_dict['REPO_NUM']}_{variable_swap_dict['PROJECT_NUM']}_{config.raw.document_md.table}"
    )
    existing_raw_keys = get_raw_keys(client, config.raw.document_md.database, raw_table_name) if mode == "sync" else []

    no_records = 10000
    page_no = 1

    while not stop_event.is_set():
        try:
            ## DEBUG ###########################################################
            logger.info(
                "Document search URL: %s",
                config.proarc.search_documents_url.format(
                    REPO_NUM=variable_swap_dict["REPO_NUM"]
                ),
            )

            logger.info(
                "Document search criteria:\n%s",
                json.dumps(search_criteria, indent=2, default=str),
            )

            logger.info(
                "Document search repository=%s, project=%s, mode=%s",
                variable_swap_dict.get("REPO_NUM"),
                variable_swap_dict.get("PROJECT_NUM"),
                mode,
            )
            ###############################################################

            data = requests.post(
                config.proarc.search_documents_url.format(REPO_NUM=variable_swap_dict["REPO_NUM"]),
                headers={"Authorization": f"Bearer {access_token}"},
                json={
                    "criteria": search_criteria,
                    "noOfRecords": no_records,
                    "pageNo": page_no,
                    "presentation": {
                        "documentMetaItems": ["*"],
                        "revisionMetaItems": ["*"],
                    },
                },
            )

            ## DEBUG ######################################################
            logger.info(
                "Document search response: status=%s, page=%s",
                data.status_code,
                page_no,
            )

            logger.info(
                "Document search response body: %s",
                data.text[:5000],
            )
            ##############################################################

            data.raise_for_status()

            out_data = data.json()
            # Update metric
            if metrics:
                metrics.documents_md_identified.labels(item_type="documents").inc(len(out_data["items"]))

            # write metadata to Raw
            raw_key = config.raw.document_md_key
            for document in out_data["items"]:
                # add entry to raw
                if str(document[raw_key]) not in existing_raw_keys:
                    upload_queue.add_to_upload_queue(
                        database=config.raw.document_md.database,
                        table=raw_table_name,
                        raw_row=Row(key=document[raw_key], columns=document["documentMetaItems"]),
                    )
                    document_rnos_added.add(str(document["rno"]))
            logger.info("Number of documents extracted: %d on page: %d", len(data.json()["items"]), page_no)
            document_rnos_extracted.update([str(document["rno"]) for document in out_data["items"]])
            raw_entries_extracted.update([str(document[raw_key]) for document in out_data["items"]])
            # check for more pages
            if len(out_data["items"]) < no_records:
                document_succeed = True
                break
            page_no += 1
        except (ValueError, KeyError, requests.exceptions.HTTPError) as e:
            logger.error("Error searching documents: %s", e)
            document_succeed = False
            break

    if mode == "sync":
        logger.info("Total number of documents extracted from Proarc: %d", len(document_rnos_extracted))
        logger.info("Number of documents added to %s: %d", raw_table_name, len(document_rnos_added))
        logger.info("Documents added to %s: %s", raw_table_name, document_rnos_added)
        # raw deletion
        raw_entries_delete = list(set(existing_raw_keys) - raw_entries_extracted)
        delete_success = delete_raw_rows(client, config.raw.document_md.database, raw_table_name, raw_entries_delete)
        if not delete_success:
            document_succeed = False

    # exit for stop event
    if stop_event.is_set():
        logger.info("Stop event set in search_documents.")
        return [], current_utc_time, False

    return list(document_rnos_extracted), current_utc_time, document_succeed


def get_revisions(
    client: CogniteClient,
    access_token: str,
    upload_queue: RawUploadQueue,
    config: Config,
    document_rnos: list,
    variable_swap_dict: dict,
    stop_event: Event,
    mode: str,
    metrics: ProArcMetrics = None,
) -> tuple[dict, bool]:
    """Retrieves approved revisions for a list of documents and uploads their metadata.

    Args:
        client: To access CDF
        access_token: ProArc token for API authentication.
        upload_queue: Queue for uploading revision metadata to CDF Raw.
        config: The full configuration object containing ProArc and Raw settings.
        document_rnos: A list of document Rnos to check.
        variable_swap_dict: dict for variable replacements in revision criteria.
        stop_event: Event to signal stopping the revision fetch.
        mode: Extractor mode, either normal or sync.
        metrics: ProArcMetrics object for tracking metrics (optional).

    Returns:
        A tuple containing:
        - dict: A dictionary of {document Rno: [revision Rnos]}.
        - bool: True if all revision fetches were successful, False otherwise.

    """
    # Build revision criteria from config
    revision_criteria = _build_criteria(criteria=config.proarc.get_revisions_criteria, variable_swap=variable_swap_dict)

    all_revisions = []
    revision_nos_extracted = {}  # {document_rno: [revision_rnos]}
    raw_entries_extracted = set()  # full list of raw keys extracted from proarc
    raw_entries_added = set()  # added raw
    revision_succeed = True

    # pull data from existing raw table
    raw_table_name = (
        f"{variable_swap_dict['REPO_NUM']}_{variable_swap_dict['PROJECT_NUM']}_{config.raw.revision_md.table}"
    )
    existing_raw_keys = get_raw_keys(client, config.raw.document_md.database, raw_table_name) if mode == "sync" else []

    for document in document_rnos:
        # check for stop event
        if stop_event.is_set():
            logger.info("Stop event set in get_revisions.")
            return {}, False

        url = config.proarc.get_revisions_url.format(document=document)
        data = requests.post(
            url,
            headers={"Authorization": f"Bearer {access_token}"},
            json={
                "criteria": revision_criteria,
                "noOfRecords": 1000,
                "pageNo": 1,
                "presentation": {"metaItems": ["*"]},
            },
        )
        try:
            data.raise_for_status()
            all_revisions.extend(data.json()["items"])
            # revision metrics
            if metrics:
                metrics.revision_md_identified.labels(item_type="revisions").inc(len(data.json()["items"]))

            # write metadata to Raw
            for revision in data.json()["items"]:
                raw_key = ":".join([str(revision[key]) for key in config.raw.revision_md_key])
                if raw_key not in existing_raw_keys:
                    upload_queue.add_to_upload_queue(
                        database=config.raw.revision_md.database,
                        table=raw_table_name,
                        raw_row=Row(key=raw_key, columns=revision["metaItems"]),
                    )
                    raw_entries_added.add(raw_key)
                raw_entries_extracted.add(raw_key)

            revision_nos_extracted[document] = [x["revisionRno"] for x in data.json()["items"]]
            if mode != "sync":
                logger.info(
                    "Number of document revisions extracted for document: %s : %d",
                    document,
                    len(data.json()["items"]),
                )
            # revision metrics
            if metrics and len(data.json()["items"]) > 0:
                metrics.revision_md_uploaded.labels(item_type="revisions").inc(len(data.json()["items"]))
        except (ValueError, KeyError, requests.exceptions.HTTPError) as e:
            logger.error("Error getting revisions for document: %s : %s", document, e)
            # revision error metric
            if metrics:
                metrics.revision_md_error.labels(item_type="revisions").inc()
            revision_succeed = False
            continue

    if mode == "sync":
        logger.info("Total number of revisions extracted from Proarc: %d", len(raw_entries_extracted))
        logger.info("Number of revisions added to %s: %d", raw_table_name, len(raw_entries_added))
        logger.info("Revisions added to %s: %s", raw_table_name, raw_entries_added)
        # raw deletion
        raw_entries_delete = list(set(existing_raw_keys) - raw_entries_extracted)
        delete_success = delete_raw_rows(client, config.raw.revision_md.database, raw_table_name, raw_entries_delete)
        if not delete_success:
            revision_succeed = False

    return revision_nos_extracted, revision_succeed


def create_file_md(file: dict, instance_space: str) -> CogniteExtractorFileApply:
    """Creates CogniteExtractorFileApply object for a given file.

    Args:
        file (dict): The file dictionary containing metadata.
        instance_space (str): The space where the file instance is located.

    Returns:
        CogniteExtractorFileApply: The created CogniteExtractorFileApply object.

    """
    # name
    for character in ["\\", "/"]:
        file["fileName"] = file["fileName"].replace(character, "_")
    clean_file_name = file["fileName"]

    # mime type
    mime_type = guess_type(file["fileName"])[0] or "application/octet-stream"

    return CogniteExtractorFileApply(
        space=instance_space, external_id=file["fileRno"], name=clean_file_name, mime_type=mime_type
    )


def get_files(
    client: CogniteClient,
    access_token: str,
    md_upload_queue: RawUploadQueue,
    file_upload_queue: BytesUploadQueue,
    config: Config,
    repo_config: ProArcRepoConfig,
    revision_nos: dict,
    variable_swap_dict: dict,
    stop_event: Event,
    mode: str,
    metrics: ProArcMetrics = None,
) -> bool:
    """Searches for approved files within specified revisions, uploads their metadata and content to CDF.

    Args:
        client: To access CDF
        access_token: ProArc token for API authentication.
        md_upload_queue: Queue for uploading file metadata to CDF Raw.
        file_upload_queue: Queue for uploading file content to CDF CogniteFiles.
        config: The full configuration object containing ProArc and Raw settings.
        repo_config: ProArcRepoConfig object containing repository configuration.
        revision_nos: A dictionary mapping document RNOs to a list of revision RNOs.
        variable_swap_dict: dict for variable replacements in file criteria.
        stop_event: Event to signal stopping the file search.
        mode: Extractor mode, either normal or sync.
        metrics: ProArcMetrics object for tracking metrics (optional).

    Returns:
        bool: True if all file processing succeeded without an unhandled exception, False otherwise.

    """
    # Build file criteria from config
    file_criteria = _build_criteria(criteria=config.proarc.get_files_criteria, variable_swap=variable_swap_dict)

    all_files = []
    raw_key = config.raw.file_md_key
    file_instance_space = repo_config.file_instance_space
    file_rnos_added = set()  # new file rnos added to raw
    raw_entries_extracted = set()  # full list of raw keys extracted from proarc

    # pull data from existing raw table
    raw_table_name = f"{variable_swap_dict['REPO_NUM']}_{variable_swap_dict['PROJECT_NUM']}_{config.raw.file_md.table}"
    existing_raw_keys = get_raw_keys(client, config.raw.file_md.database, raw_table_name) if mode == "sync" else []

    run_succeed = True
    for document, revisions in revision_nos.items():
        for revision in revisions:
            if stop_event.is_set():
                logger.info("Stop event set in get_files.")
                return False
            try:
                search_url = config.proarc.get_files_search_url.format(document=document, revision=revision)
                file_search_response = requests.post(
                    search_url,
                    headers={"Authorization": f"Bearer {access_token}"},
                    json={
                        "criteria": file_criteria,
                        "noOfRecords": 1000,
                        "pageNo": 1,
                        "presentation": {"metaItems": ["*"]},
                    },
                )
                file_search_response.raise_for_status()
                file_search_data = file_search_response.json()
                if metrics:
                    metrics.file_md_identified.labels(item_type="files").inc(len(file_search_data["items"]))

                all_files.extend(file_search_data["items"])
                for file in file_search_data["items"]:
                    # Skip non-file records
                    if str(_file_size(file)) == "0":
                        logger.warning(
                            "Skipping file with size 0. "
                            "document=%s revision=%s fileRno=%s fileName=%s",
                            document,
                            revision,
                            file.get("fileRno"),
                            file.get("fileName"),
                        )
                        continue

                    if stop_event.is_set():
                        logger.info("Stop event set in get_files.")
                        return False
                    try:
                        raw_entries_extracted.add(str(file[raw_key]))
                        # write metadata to Raw
                        if str(file[raw_key]) not in existing_raw_keys:
                            md_upload_queue.add_to_upload_queue(
                                database=config.raw.file_md.database,
                                table=raw_table_name,
                                raw_row=Row(key=str(file[raw_key]), columns=file["metaItems"]),
                            )
                            file_rnos_added.add(str(file["fileRno"]))

                            # Update file metadata upload metric
                            if metrics:
                                metrics.file_md_uploaded.labels(item_type="file_metadata").inc()

                            # get export filePath
                            export_url = config.proarc.get_files_export_url.format(file_rno=file["fileRno"])
                            file_export_response = requests.post(
                                export_url, headers={"Authorization": f"Bearer {access_token}"}, json={}
                            )
                            file_export_response.raise_for_status()

                            export_link = file_export_response.json().get("filePath", "")

                            if not export_link:
                                logger.error(
                                    "No export filePath found for document: %s - revision: %s - file: %s",
                                    document,
                                    revision,
                                    file["fileRno"],
                                )
                                continue

                            # get file content
                            export_content = requests.get(
                                f"{export_link}",
                                headers={"Authorization": f"Bearer {access_token}"},
                            )

                            export_content.raise_for_status()

                            # add file to upload queue
                            file_md = create_file_md(file, file_instance_space)
                            file_content_bytes = export_content.content

                            # Update file size metric
                            if metrics:
                                metrics.files_size_in_bytes.labels(item_type="files").inc(len(file_content_bytes))

                            file_upload_queue.add_to_upload_queue(content=file_content_bytes, file_meta=file_md)
                            # Update file upload metric
                            if metrics:
                                metrics.files_uploaded_to_cdf.labels(item_type="files").inc()
                    except Exception as e:
                        logger.error(
                            "Error processing file for document: %s - revision: %s - fileRno: %s - fileName: %s : %s",
                            document,
                            revision,
                            file.get("fileRno", "N/A"),
                            file.get("fileName", "N/A"),
                            e,
                        )
                        run_succeed = False
                        continue
                # Log number of files processed for this document and revision
                if mode != "sync":
                    logger.info(
                        "Number of files extracted for document: %s - revision: %s - %d",
                        document,
                        revision,
                        len(file_search_data["items"]),
                    )

            except (ValueError, KeyError, requests.exceptions.HTTPError) as e:
                logger.error(
                    "Error processing files for document: %s - revision: %s : %s",
                    document,
                    revision,
                    e,
                )
                if metrics:
                    metrics.file_md_error.labels(item_type="files").inc()
                run_succeed = False
                continue
    # file md raw deletion
    if mode == "sync":
        logger.info("Total number of files extracted from Proarc: %d", len(raw_entries_extracted))
        logger.info("Number of files added to %s: %d", raw_table_name, len(file_rnos_added))
        logger.info("Files added to %s and uploaded to CDF: %d", raw_table_name, len(file_rnos_added))
        # raw deletion
        raw_entries_delete = list(set(existing_raw_keys) - raw_entries_extracted)
        delete_success = delete_raw_rows(client, config.raw.file_md.database, raw_table_name, raw_entries_delete)
        if not delete_success:
            run_succeed = False
        # CogniteFile deletion
        CogniteFile_delete_list = [NodeId(file_instance_space, raw_key) for raw_key in raw_entries_delete]
        node_delete_success = delete_nodes(client, CogniteFile_delete_list)
        if not node_delete_success:
            run_succeed = False

    return run_succeed


def run_extractor(cognite: CogniteClient, states: RawStateStore, config: Config, stop_event: Event, mode: str) -> None:
    """run_extractor function for ProArc file extractor.

    Args:
        cognite: To access CDF.
        states: RawStateStore.
        config: The full configuration object containing ProArc and Raw settings.
        stop_event: default extractor stop event.
        mode: The mode in which the extractor is running. Either normal or sync.

    Returns:
        None

    """
    start_time = time.time()
    run_succeed = True
    extraction_pipeline_ext_id = config.cognite.extraction_pipeline.external_id
    logger.info("ProArc extractor run started in '%s' mode.", mode)

    try:
        # Get metrics instance and manually create time series
        metrics = safe_get(ProArcMetrics, extractor_name="proarc_extractor", extractor_version="1.0.0")
        access_token = get_proarc_token(config.proarc)
        state_store, last_run_utc, last_run_epoch = create_state_store(cognite, config.raw.last_run)
        if mode == "sync":
            variable_swap_dict = {}
        else:
            variable_swap_dict = {"LAST_RUN_ISO_TIME": last_run_utc, "INCREMENT_VAR": config.proarc.increment_variable}

        # cycle through repos
        for repo_config in config.proarc.repos:
            if stop_event.is_set():
                break
            variable_swap_dict["REPO_NUM"] = repo_config.repo_no
            variable_swap_dict["PROJECT_NUM"] = repo_config.project_no
            logger.info(
                "Proarc extractor running for repository: %s  project: %s", repo_config.repo_no, repo_config.project_no
            )

            # Documents
            logger.info("Getting Documents")
            with RawUploadQueue(cdf_client=cognite, max_queue_size=10_000) as document_md_upload_queue:
                document_rnos, current_run_time, document_succeed = search_documents(
                    cognite,
                    access_token,
                    document_md_upload_queue,
                    config,
                    last_run_utc,
                    last_run_epoch,
                    variable_swap_dict,
                    stop_event,
                    mode,
                    metrics,
                )

            # Update document metrics
            if document_succeed:
                metrics.document_md_uploaded.labels(item_type="documents").inc(len(document_rnos))
            else:
                metrics.document_md_error.labels(item_type="documents").inc()
                logger.error(
                    "Error extracting documents for repository: %s  project: %s",
                    repo_config.repo_no,
                    repo_config.project_no,
                )
                run_succeed = False
                continue
            if not document_rnos:
                logger.info(
                    "No new or updated documents found for repository: %s  project: %s",
                    repo_config.repo_no,
                    repo_config.project_no,
                )
                continue

            # Revisions
            logger.info("Getting Revisions")
            with RawUploadQueue(cdf_client=cognite, max_queue_size=10_000) as revision_md_upload_queue:
                revision_nos, revision_succeed = get_revisions(
                    cognite,
                    access_token,
                    revision_md_upload_queue,
                    config,
                    document_rnos,
                    variable_swap_dict,
                    stop_event,
                    mode,
                    metrics,
                )

            if not revision_succeed:
                run_succeed = False
            if not revision_nos:
                logger.info(
                    "No revisions found for updated documents for repository: %s  project: %s",
                    repo_config.repo_no,
                    repo_config.project_no,
                )
                continue

            # check stop event
            if stop_event.is_set():
                logger.info("Stop event set, exiting extractor run.")
                break

            # Files
            logger.info("Getting Files")
            with (
                RawUploadQueue(cdf_client=cognite, max_queue_size=10_000) as file_md_upload_queue,
                BytesUploadQueue(
                    cdf_client=cognite, max_queue_size=1_000, overwrite_existing=True, trigger_log_level="INFO"
                ) as file_upload_queue,
            ):
                file_succeed = get_files(
                    cognite,
                    access_token,
                    file_md_upload_queue,
                    file_upload_queue,
                    config,
                    repo_config,
                    revision_nos,
                    variable_swap_dict,
                    stop_event,
                    mode,
                    metrics,
                )
            if not file_succeed:
                run_succeed = False

        if stop_event.is_set():
            logger.info("Stop event set, exiting extractor run.")
            return

        # synchronize last run time in Raw
        if run_succeed:
            if mode != "sync":
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
