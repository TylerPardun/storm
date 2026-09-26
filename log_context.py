"""Context stamped on every log line: full UTC date and time, an ID for this
run of STORM, and which case is open ("archive 2024-04-27" or "live") --
so log lines from different runs and different archive days can be told
apart when the same log file is read later.
"""
import logging
import secrets
import time

SESSION_ID = secrets.token_hex(3)          # e.g. "a3f09c"; new each time STORM starts
_case = "starting"

FORMAT = "%(asctime)s  %(levelname)-8s  [%(session)s %(case)s]  %(name)s  %(message)s"
DATEFMT = "%Y-%m-%d %H:%M:%SZ"


def set_case(label: str) -> None:
    global _case
    _case = label


class ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.session = SESSION_ID
        record.case = _case
        return True


class UTCFormatter(logging.Formatter):
    converter = time.gmtime


def install(handler: logging.Handler) -> None:
    """Give a handler the dated, session-stamped format."""
    handler.addFilter(ContextFilter())
    handler.setFormatter(UTCFormatter(FORMAT, datefmt=DATEFMT))
