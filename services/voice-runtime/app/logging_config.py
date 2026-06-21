import logging
import sys


_NOISY_HTTP = (
    "httpx", "httpcore", "websockets.client",
    "aiohttp.access", "urllib3.connectionpool",
)

_FMT = "%(asctime)s %(levelname)-8s %(name)-30s %(message)s"
_DATE = "%Y-%m-%d %H:%M:%S"


class _ExtraFormatter(logging.Formatter):
    """Standard format + any extra structured fields appended as key=value."""

    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        extras = {
            k: v
            for k, v in record.__dict__.items()
            if k
            not in logging.LogRecord.__dict__
            and not k.startswith("_")
            and k
            not in (
                "message", "asctime", "exc_text", "stack_info",
                "color_message",  # uvicorn internal
            )
        }
        if extras:
            kv = "  " + "  ".join(f"{k}={v}" for k, v in extras.items())
            return base + kv
        return base


def _install() -> None:
    """Replace LiveKit's JsonFormatter with a readable one.

    LiveKit Agents calls setup_logging() from cli.py after our module is
    imported. We monkey-patch that module-level name so our formatter wins
    instead of the default JsonFormatter.
    """
    import livekit.agents.cli.cli as _lk_cli

    def _readable_setup(log_level: str, devmode: bool, console: bool, compact: bool = False) -> None:
        root = logging.getLogger()
        # Remove any handlers livekit already installed (re-entrant safety).
        for h in root.handlers[:]:
            root.removeHandler(h)

        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(_ExtraFormatter(_FMT, datefmt=_DATE))
        root.addHandler(handler)
        root.setLevel(log_level)

        for name in _NOISY_HTTP:
            logging.getLogger(name).setLevel(logging.WARNING)

        # Keep livekit.* at the requested level (not silenced).
        logging.getLogger("livekit").setLevel(log_level)

        # Propagate level to any registered LiveKit plugins.
        try:
            from livekit.agents.plugin import Plugin
            from livekit.agents.log import logger as _lk_logger

            if _lk_logger.level == logging.NOTSET:
                _lk_logger.setLevel(log_level)

            def _cfg_plugin(plugin: Plugin) -> None:
                if plugin.logger is not None and plugin.logger.level == logging.NOTSET:
                    plugin.logger.setLevel(log_level)

            for p in Plugin.registered_plugins:
                _cfg_plugin(p)
            Plugin.emitter.on("plugin_registered", _cfg_plugin)
        except Exception:
            pass

    _lk_cli.setup_logging = _readable_setup
