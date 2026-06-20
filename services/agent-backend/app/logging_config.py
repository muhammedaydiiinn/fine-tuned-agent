import logging


class AccessNoiseFilter(logging.Filter):
    """Hide successful probes while preserving failures and product traffic."""

    QUIET_PATHS = {"/health"}

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if not isinstance(args, tuple) or len(args) < 5:
            return True
        path = str(args[2]).split("?", 1)[0]
        status = int(args[4])
        return status >= 400 or path not in self.QUIET_PATHS


def configure_access_logging() -> None:
    logging.getLogger("uvicorn.access").addFilter(AccessNoiseFilter())
