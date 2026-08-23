"""Shared logging setup: timestamped output to stdout.

Each service calls setup_logging("<service>") once at startup. Output goes to
stdout (so it shows in the VS Code panel), and the start scripts redirect that
stream into logs/<service>.log — so redirection is the single source of the log
file (no double-writing). Uncaught exceptions are routed through logging too.
"""

from __future__ import annotations

import logging
import sys

_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"


def setup_logging(service: str, *, level: int = logging.INFO) -> logging.Logger:
    root = logging.getLogger()
    if getattr(root, "_usertgbot_configured", False):
        return logging.getLogger(service)

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(logging.Formatter(_FORMAT, _DATEFMT))

    root.setLevel(level)
    root.handlers.clear()
    root.addHandler(console)
    root._usertgbot_configured = True  # type: ignore[attr-defined]

    def _excepthook(exc_type, exc, tb) -> None:
        logging.getLogger(service).critical("uncaught exception", exc_info=(exc_type, exc, tb))

    sys.excepthook = _excepthook

    return logging.getLogger(service)
