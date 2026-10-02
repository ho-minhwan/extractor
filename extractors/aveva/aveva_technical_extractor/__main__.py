"""main entry point for Hanwha Aveva UE technical extractor."""

from cognite.extractorutils.metrics import safe_get

from aveva_technical_extractor import __version__
from aveva_technical_extractor.config import Config
from aveva_technical_extractor.extractor import run_extractor
from aveva_technical_extractor.metrics import AvevaUEMetrics
from aveva_technical_extractor.service import ExtractorService


def main() -> None:
    """Runs the Aveva UE technical extractor."""
    # Create metrics instance for debugging
    metrics = safe_get(AvevaUEMetrics, extractor_name="aveva_technical_extractor", extractor_version=__version__)

    with ExtractorService(
        name="aveva_technical_extractor",
        description="Extractor for Aveva UE technical data",
        config_class=Config,
        run_handle=run_extractor,
        version=__version__,
        config_file_path=r"config.yaml",
        metrics=metrics,
    ) as extractor:
        extractor.run()


if __name__ == "__main__":
    main()
