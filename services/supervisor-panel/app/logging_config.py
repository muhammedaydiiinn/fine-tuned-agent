import logging


class AccessNoiseFilter(logging.Filter):
    """Hide successful probes/polling while preserving failures and actions."""

    QUIET_PATHS = {
        "/health",
    }
    QUIET_SUFFIXES = {
        "/conversation",
        "/live-summary",
        "/voice-events",
    }

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if not isinstance(args, tuple) or len(args) < 5:
            return True
        path = str(args[2]).split("?", 1)[0]
        status = int(args[4])
        is_quiet = path in self.QUIET_PATHS or any(
            path.endswith(suffix) for suffix in self.QUIET_SUFFIXES
        )
        return status >= 400 or not is_quiet


def configure_access_logging() -> None:
    logging.getLogger("uvicorn.access").addFilter(AccessNoiseFilter())
    logging.getLogger("httpx").setLevel(logging.WARNING)
