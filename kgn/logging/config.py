"""KGN structured logging configuration.

Provides ``configure_logging()`` to initialise structlog with either
JSON (production/CI) or coloured console (development) output.

The configuration bridges stdlib ``logging`` so that existing
``logging.getLogger(__name__)`` calls throughout the codebase
automatically flow through the structlog pipeline.
"""

from __future__ import annotations

import logging
import sys

import structlog


def configure_logging(
    *,
    level: str = "INFO",
    fmt: str = "json",
    stderr: bool = False,
    cache_logger_on_first_use: bool = True,
) -> None:
    """Initialise KGN structured logging.

    Parameters
    ----------
    level:
        Log level name (``"DEBUG"``, ``"INFO"``, ``"WARNING"`` …).
    fmt:
        ``"json"`` for machine-readable output (production / CI),
        ``"console"`` for coloured human-readable output (development).
    stderr:
        If ``True``, all log output goes to **stderr**.
        Required when running MCP in stdio mode where stdout is the
        JSON-RPC protocol channel.
    cache_logger_on_first_use:
        If ``False``, structlog will re-create loggers on every call.
        Set to ``False`` in tests to ensure reconfiguration between tests
        takes effect immediately (R-005).
    """
    numeric_level = getattr(logging, level.upper(), logging.INFO)

    # ── Shared processors ──────────────────────────────────────────
    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
    ]

    # ── Renderer ──────────────────────────────────────────────────
    if fmt == "console":
        renderer: structlog.types.Processor = structlog.dev.ConsoleRenderer()
    else:
        renderer = structlog.processors.JSONRenderer(ensure_ascii=False)

    # ── stdlib logging integration ────────────────────────────────
    # ProcessorFormatter renders stdlib log records through structlog.
    log_output = sys.stderr if stderr else sys.stdout
    handler = logging.StreamHandler(log_output)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                renderer,
            ],
        ),
    )

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(numeric_level)

    # ── structlog configuration ───────────────────────────────────
    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=cache_logger_on_first_use,
    )
