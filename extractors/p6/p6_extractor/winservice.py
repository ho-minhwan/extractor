"""Windows service implementation for the P6 extractor."""

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

from p6_extractor import __version__
from p6_extractor.config import Config
from p6_extractor.extractor import run_extractor
from p6_extractor.metrics import P6Metrics
from p6_extractor.service import ExtractorService

SERVICE_NAME = "cognite_p6_extractor"
SERVICE_DISPLAY_NAME = "Cognite Primavera P6 Extractor"
SERVICE_DESCRIPTION = "Cognite Primavera P6 EPPM REST API Extractor"


class WindowsService(win32serviceutil.ServiceFramework):
    """Windows service hosting the P6 extractor."""

    _svc_name_ = SERVICE_NAME
    _svc_display_name_ = SERVICE_DISPLAY_NAME
    _svc_description_ = SERVICE_DESCRIPTION

    @classmethod
    def parse_command_line(cls) -> None:
        win32serviceutil.HandleCommandLine(cls)

    def __init__(self, args: str) -> None:
        win32serviceutil.ServiceFramework.__init__(self, args)
        self.hWaitStop = win32event.CreateEvent(None, 0, 0, None)
        socket.setdefaulttimeout(60)
        self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=5)
        self.stop_event = Event()

    def SvcStop(self) -> None:
        self.stop_event.set()
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        win32event.SetEvent(self.hWaitStop)

    def SvcDoRun(self) -> None:
        servicemanager.LogMsg(
            servicemanager.EVENTLOG_INFORMATION_TYPE,
            servicemanager.PYS_SERVICE_STARTED,
            (SERVICE_NAME, ""),
        )
        os.chdir(os.path.dirname(sys.executable))
        try:
            metrics = safe_get(P6Metrics, extractor_name="p6_extractor", extractor_version=__version__)
            with ExtractorService(
                config_class=Config,
                run_handle=run_extractor,
                config_file_path="config.yaml",
                version=__version__,
                metrics=metrics,
            ) as extractor:
                extractor.cancelation_token = self.stop_event
                extractor.run_service()
        except Exception as error:
            logging.exception("Unexpected error while running P6 Windows service")
            error_event_log(f"Unexpected error while running P6 Windows service: {error}")
            raise SystemExit(3) from error


def error_event_log(message: str, additional: str | None = None) -> None:
    """Write a warning to the Windows event log."""
    win32evtlogutil.ReportEvent(
        SERVICE_NAME,
        0,
        eventType=win32evtlog.EVENTLOG_WARNING_TYPE,
        strings=message,
        data=(additional or "").encode(),
    )


def init() -> None:
    """Start the service dispatcher or process service commands."""
    if len(sys.argv) == 1:
        servicemanager.Initialize()
        servicemanager.PrepareToHostSingle(WindowsService)
        servicemanager.StartServiceCtrlDispatcher()
    else:
        win32serviceutil.HandleCommandLine(WindowsService)


if __name__ == "__main__":
    init()
