"""Scheduled service wrapper for the P6 extractor."""

import logging
from threading import Event
from time import localtime
from typing import Any

import arrow
import cronex
from cognite.client.data_classes.extractionpipelines import ExtractionPipelineRun
from cognite.extractorutils import Extractor

from p6_extractor import __version__
from p6_extractor.config import Config
from p6_extractor.extractor import run_extractor
from p6_extractor.metrics import P6Metrics


class ExtractorService(Extractor):
    """Run the P6 extractor on its configured cron schedule."""

    def __init__(
        self,
        name: str = "p6_extractor",
        description: str = "Extractor for Oracle Primavera P6 EPPM",
        config_class: Config = Config,
        run_handle: Any = run_extractor,
        config_file_path: str = "config.yaml",
        version: str = __version__,
        metrics: P6Metrics | None = None,
    ) -> None:
        super().__init__(
            name=name,
            description=description,
            config_class=config_class,
            run_handle=run_handle,
            config_file_path=config_file_path,
            version=version,
            metrics=metrics,
        )
        self._logger = logging.getLogger(__name__)
        self._utc_offset = arrow.now("local").utcoffset().seconds // 3600
        self.cancelation_token = Event()

    def _check_schedule(self, schedule: str) -> bool:
        return bool(cronex.CronExpression(schedule).check_trigger(localtime()[:5], self._utc_offset))

    def run_service(self) -> None:
        """Run scheduled extractions until cancellation is requested."""
        self._logger.info("P6 extractor service started with version %s", __version__)
        while not self.cancelation_token.wait(
            timeout=(arrow.utcnow().ceil("minute") - arrow.utcnow()).total_seconds()
        ):
            try:
                if self._check_schedule(self.config.schedule.schedule):
                    self.run()
            except Exception as error:
                self._logger.exception("P6 service execution failed")
                client = self.config.cognite.get_cognite_client("p6_extractor")
                client.extraction_pipelines.runs.create(
                    ExtractionPipelineRun(
                        extpipe_external_id=self.config.cognite.extraction_pipeline.external_id,
                        status="failure",
                        message=f"Extractor run failed: {error!s}",
                    )
                )

    def __enter__(self):  # type: ignore
        super().__enter__()
        return self

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> bool:  # type: ignore
        self.cancelation_token.set()
        return bool(super().__exit__(exc_type, exc_value, traceback))
