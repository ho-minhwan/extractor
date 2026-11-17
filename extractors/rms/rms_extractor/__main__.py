"""main entry point for RMS extractor."""

from cognite.extractorutils.metrics import safe_get

from rms_extractor import __version__
from rms_extractor.config import Config
from rms_extractor.extractor import run_extractor
from rms_extractor.metrics import RMSMetrics
from rms_extractor.service import ExtractorService


def main() -> None:
    """Runs the RMS extractor."""
    metrics = safe_get(RMSMetrics, extractor_name="rms_extractor", extractor_version=__version__)

    with ExtractorService(
        name="rms_extractor",
        description="Extractor for RMS database ingestion",
        config_class=Config,
        run_handle=run_extractor,
        version=__version__,
        config_file_path=r"config.yaml",
        metrics=metrics,
    ) as extractor:
        extractor.run()


if __name__ == "__main__":
    main()
