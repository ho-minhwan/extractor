"""The service module of RMS extractor."""

import logging
from threading import Event
from time import localtime
from typing import Any

import arrow
import cronex
from cognite.client.data_classes.extractionpipelines import ExtractionPipelineRun
from cognite.extractorutils import Extractor

from rms_extractor import __version__
from rms_extractor.config import Config
from rms_extractor.extractor import run_extractor
from rms_extractor.metrics import RMSMetrics


class ExtractorService(Extractor):
    """Wrap single execution run into service."""

    def __init__(
        self,
        name: str = "rms_extractor",
        description: str = "RMS database extractor",
        config_class: Config = Config,
        run_handle: Any = run_extractor,
        config_file_path: str = "config.yaml",
        version: str = __version__,
        metrics: RMSMetrics | None = None,
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

    def _schedule_condition(self, config: Config) -> bool:
        result = False
        if config.schedule is not None:
            try:
                result = self._check_schedule(config.schedule.schedule)
                if result:
                    self._logger.info("Triggering execution for RMS extractor")
            except Exception:
                self._logger.exception("Schedule check has failed for RMS extractor")
                raise
        return result

    def _check_schedule(self, schedule: str) -> bool:
        exp = cronex.CronExpression(schedule)
        return bool(exp.check_trigger(localtime()[:5], self._utc_offset))

    def run_service(self) -> None:
        self._logger.info("Service is up and running with version %s", str(__version__))
        while not self.cancelation_token.wait(timeout=(arrow.utcnow().ceil("minute") - arrow.utcnow()).total_seconds()):
            try:
                if self._schedule_condition(self.config):
                    self.run()

            except Exception as exp:
                logging.exception("service execution has failed")

                def report_failure(config: Config, exception: Exception) -> None:
                    cognite_client = config.cognite.get_cognite_client("rms_extractor")
                    cognite_client.extraction_pipelines.runs.create(
                        ExtractionPipelineRun(
                            extpipe_external_id=config.cognite.extraction_pipeline.external_id,
                            status="failure",
                            message=f"Extractor run failed: {exception!s}",
                        )
                    )

                report_failure(self.config, exp)

    def __enter__(self):
        super().__enter__()
        return self

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> bool:
        self.cancelation_token.set()
        return bool(super().__exit__(exc_type, exc_value, traceback))
