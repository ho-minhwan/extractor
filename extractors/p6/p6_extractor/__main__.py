"""Command-line entry point for the P6 extractor."""

from cognite.extractorutils.metrics import safe_get

from p6_extractor import __version__
from p6_extractor.config import Config
from p6_extractor.extractor import run_extractor
from p6_extractor.metrics import P6Metrics
from p6_extractor.service import ExtractorService


def main() -> None:
    """Run one P6 extraction."""
    metrics = safe_get(P6Metrics, extractor_name="p6_extractor", extractor_version=__version__)
    with ExtractorService(
        config_class=Config,
        run_handle=run_extractor,
        version=__version__,
        metrics=metrics,
    ) as extractor:
        extractor.run()


if __name__ == "__main__":
    main()
