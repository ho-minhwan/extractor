"""main entry point for Hanwha ProArc file extractor."""

from cognite.extractorutils.metrics import safe_get

from proarc_extractor import __version__
from proarc_extractor.config import Config
from proarc_extractor.extractor import run_extractor
from proarc_extractor.metrics import ProArcMetrics
from proarc_extractor.service import ExtractorService


def main() -> None:
    """Runs the ProArc file extractor."""
    # Create metrics instance for debugging
    metrics = safe_get(ProArcMetrics, extractor_name="proarc_extractor", extractor_version=__version__)
    mode = "sync"

    with ExtractorService(
        name="proarc_extractor",
        description="Extractor for ProArc ingestion",
        config_class=Config,
        run_handle=run_extractor,
        version=__version__,
        config_file_path=r"config.yaml",
        metrics=metrics,
    ) as extractor:
        extractor.run(mode=mode)


if __name__ == "__main__":
    main()
