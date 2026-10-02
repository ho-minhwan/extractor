"""The service module of SCM extractor."""

import logging
from threading import Event
from time import localtime
from typing import Any

import arrow
import cronex
from cognite.client.data_classes.extractionpipelines import ExtractionPipelineRun
from cognite.extractorutils import Extractor

from scm_extractor import __version__
from scm_extractor.config import Config
from scm_extractor.extractor import run_extractor
from scm_extractor.metrics import SCMMetrics


class ExtractorService(Extractor):
    """The class that wraps single execution run into service."""

    def __init__(
        self,
        name: str = "scm_extractor",
        description: str = "SCM extractor",
        config_class: Config = Config,
        run_handle: Any = run_extractor,
        config_file_path: str = "config.yaml",
        version: str = __version__,
        metrics: SCMMetrics | None = None,
    ) -> None:
        """Initialization of the extractor service.

        Args:
            name (str): name of the extractor. Defaults to "scm_extractor".
            description (str): Description of the extractor. Defaults to "SCM extractor".
            config_class (Config): Configuration class for the extractor. Defaults to Config.
            run_handle (Any): Function or method to run the extractor. Defaults to run_extractor.
            config_file_path (str): Path to the configuration file. Defaults to "config.yaml".
            version (str): Version of the extractor. Defaults to __version__.
            metrics: Metrics instance for the extractor. Defaults to None.

        """
        # super(ExtractorService, self).__init__(
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
                    self._logger.info("Triggering execution for SCM extractor")
            except Exception as e:
                self._logger.exception("Schedule check has failed for SCM extractor")
                raise e

        return result

    def _check_schedule(self, schedule: str) -> bool:
        exp = cronex.CronExpression(schedule)
        return bool(exp.check_trigger(localtime()[:5], self._utc_offset))

    def run_service(self) -> None:
        """Run service extractor."""
        self._logger.info("Service is up and running with version %s", str(__version__))
        while not self.cancelation_token.wait(timeout=(arrow.utcnow().ceil("minute") - arrow.utcnow()).total_seconds()):
            try:
                if self._schedule_condition(self.config):
                    self.run()  # plan threads for execution

            except Exception as exp:
                logging.exception("service execution has failed")

                def report_failure(config: Config, exception: Exception) -> None:
                    cognite_client = config.cognite.get_cognite_client("scm_extractor")
                    cognite_client.extraction_pipelines.runs.create(
                        ExtractionPipelineRun(
                            extpipe_external_id=config.cognite.extraction_pipeline.external_id,
                            status="failure",
                            message=f"Extractor run failed: {exception!s}",
                        )
                    )

                report_failure(self.config, exp)

    def __enter__(self):  # type: ignore
        """Enter the runtime context related to this object."""
        # super(ExtractorService, self).__enter__()
        super().__enter__()
        return self

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> bool:  # type: ignore
        """Exit the runtime context related to this object.

        Args:
            exc_type (Any): Exception type.
            exc_value (Any): Exception value.
            traceback (Any): Traceback object.

        Returns:
            bool: return value of the superclass exit method

        """
        self.cancelation_token.set()  # for service implementation exit should set the intention
        # return bool(super(ExtractorService, self).__exit__(exc_type, exc_value, traceback))
        return bool(super().__exit__(exc_type, exc_value, traceback))
