"""Windows service implementation for SCM extractor."""

import concurrent.futures
import logging
import os
import socket
import sys
from threading import Event

import servicemanager
import win32event
import win32evtlog
import win32evtlogutil
import win32service
import win32serviceutil
from cognite.extractorutils.metrics import safe_get

from scm_extractor import __version__
from scm_extractor.config import Config
from scm_extractor.extractor import run_extractor
from scm_extractor.metrics import SCMMetrics
from scm_extractor.service import ExtractorService

SERVICE_NAME: str = "cognite_scm_extractor"
SERVICE_DISPLAY_NAME: str = "Cognite SCM Extractor"
SERVICE_DESCRIPTION: str = "Cognite SCM RESTful API Extractor"


class WindowsService(win32serviceutil.ServiceFramework):
    """Windows service for the SCM extractor.

    Args:
        win32serviceutil (ServiceFramework): Service framework.

    Raises:
        SystemExit: Stop the service on error.

    """

    # Required attributes for Windows service
    _svc_name_ = SERVICE_NAME
    _svc_display_name_ = SERVICE_DISPLAY_NAME
    _svc_description_ = SERVICE_DESCRIPTION

    @classmethod
    def parse_command_line(cls) -> None:
        """Parse command line arguments."""
        win32serviceutil.HandleCommandLine(cls)

    def __init__(self, args: str) -> None:
        """Initialization of the windows service.

        Args:
            args (str): default args.

        """
        win32serviceutil.ServiceFramework.__init__(self, args)
        self.hWaitStop = win32event.CreateEvent(None, 0, 0, None)
        socket.setdefaulttimeout(60)
        self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=5)
        self.stop_event = Event()

    def SvcStop(self) -> None:
        """Stop the service."""
        self.stop_event.set()
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        win32event.SetEvent(self.hWaitStop)

    def SvcDoRun(self) -> None:
        """Service run implementation.

        Raises:
            SystemExit: Stop the service on error.

        """
        servicemanager.LogMsg(
            servicemanager.EVENTLOG_INFORMATION_TYPE,
            servicemanager.PYS_SERVICE_STARTED,
            (SERVICE_NAME, ""),
        )
        os.chdir(os.path.dirname(sys.executable))

        try:
            metrics = safe_get(SCMMetrics, extractor_name="scm_extractor", extractor_version=__version__)
            with ExtractorService(
                name="scm_extractor",
                description="SCM extractor",
                config_class=Config,
                run_handle=run_extractor,
                config_file_path="config.yaml",
                version=__version__,
                metrics=metrics,
            ) as extractor:
                extractor.cancelation_token = self.stop_event
                extractor.run_service()

        except SystemExit as e:
            if e.code != 0:
                self.ReportServiceStatus(win32service.SERVICE_ERROR_CRITICAL)
            logging.exception("Exiting...")
            raise SystemExit(2) from e  # crash and notify service manager that something went wrong
        except Exception as e:
            logging.exception("Unexpected error during run:")
            error_event_log(f"Unexpected error during run: {e}")
            raise SystemExit(3) from e  # crash and notify service manager that something went wrong


def error_event_log(message: str, additional: str | None = None):  # type: ignore
    """Write an error message to the Windows event log.

    Args:
        message (str): Error message.
        additional (str | None, optional): Additional information. Defaults to None.

    """
    if additional is None:
        additional = ""

    win32evtlogutil.ReportEvent(
        SERVICE_NAME,
        0,
        eventType=win32evtlog.EVENTLOG_WARNING_TYPE,
        strings=message,
        data=additional.encode(),
    )


def init() -> None:
    """Function to run a service."""
    if len(sys.argv) == 1:
        servicemanager.Initialize()
        servicemanager.PrepareToHostSingle(WindowsService)
        servicemanager.StartServiceCtrlDispatcher()
    else:
        win32serviceutil.HandleCommandLine(WindowsService)


if __name__ == "__main__":
    init()
