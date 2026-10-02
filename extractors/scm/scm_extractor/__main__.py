"""main entry point for Hanwha SCM extractor."""

from cognite.extractorutils.metrics import safe_get

from scm_extractor import __version__
from scm_extractor.config import Config
from scm_extractor.extractor import run_extractor
from scm_extractor.metrics import SCMMetrics
from scm_extractor.service import ExtractorService


def main() -> None:
    """Runs the SCM extractor."""
    # Create metrics instance for debugging
    metrics = safe_get(SCMMetrics, extractor_name="scm_extractor", extractor_version=__version__)

    with ExtractorService(
        name="scm_extractor",
        description="Extractor for SCM ingestion",
        config_class=Config,
        run_handle=run_extractor,
        version=__version__,
        config_file_path=r"config.yaml",
        metrics=metrics,
    ) as extractor:
        extractor.run()


if __name__ == "__main__":
    main()
