"""Logging module for system-tender.

Cross-platform logging with syslog (macOS), journald (Linux), file rotation,
and rich console output for interactive use.
"""

from __future__ import annotations

import logging
import platform
import sys
from logging.handlers import RotatingFileHandler, SysLogHandler
from pathlib import Path


LOGGER_NAME = "system-tender"
DEFAULT_LOG_DIR = Path.home() / ".config" / "system-tender" / "logs"
LOG_FORMAT = "%(asctime)s %(name)s [%(context)s] %(levelname)s %(message)s"
SYSLOG_FORMAT = "system-tender[%(context)s]: %(levelname)s %(message)s"
MAX_SYSLOG_MSG = 1024  # syslog has a message length limit
MAX_LOG_BYTES = 10 * 1024 * 1024  # 10MB
BACKUP_COUNT = 5


class TruncatingSysLogHandler(SysLogHandler):
    """SysLogHandler that truncates messages to avoid 'Message too long' errors."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            if len(msg) > MAX_SYSLOG_MSG:
                record.msg = record.getMessage()[:MAX_SYSLOG_MSG - 20] + "...(truncated)"
                record.args = None
            super().emit(record)
        except OSError:
            pass  # Silently drop if syslog fails


class ContextFilter(logging.Filter):
    """Injects task_name and run_id into every log record as [task:run_id]."""

    def __init__(self, task_name: str | None = None, run_id: str | None = None):
        super().__init__()
        self.task_name = task_name or "global"
        self.run_id = run_id or "none"

    def filter(self, record: logging.LogRecord) -> bool:
        record.context = f"{self.task_name}:{self.run_id}"  # type: ignore[attr-defined]
        return True


def _add_syslog_handler(logger: logging.Logger) -> bool:
    """Attach a syslog handler appropriate for the current platform.

    macOS: uses /var/run/syslog
    Linux: tries journald (systemd.journal), falls back to /dev/log

    Returns True if a handler was attached.
    """
    system = platform.system()
    formatter = logging.Formatter(SYSLOG_FORMAT)

    if system == "Darwin":
        try:
            handler = TruncatingSysLogHandler(
                address="/var/run/syslog",
                facility=SysLogHandler.LOG_CRON,
            )
            handler.setFormatter(formatter)
            logger.addHandler(handler)
            return True
        except OSError:
            return False

    if system == "Linux":
        # Try journald first
        try:
            from systemd.journal import JournalHandler  # type: ignore[import-untyped]

            handler = JournalHandler(SYSLOG_IDENTIFIER="system-tender")
            handler.setFormatter(formatter)
            logger.addHandler(handler)
            return True
        except (ImportError, OSError):
            pass

        # Fall back to /dev/log
        try:
            handler = TruncatingSysLogHandler(
                address="/dev/log",
                facility=SysLogHandler.LOG_CRON,
            )
            handler.setFormatter(formatter)
            logger.addHandler(handler)
            return True
        except OSError:
            return False

    return False


def _add_file_handler(logger: logging.Logger, log_dir: Path) -> bool:
    """Attach a rotating file handler. Returns True on success."""
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / "system-tender.log"
        handler = RotatingFileHandler(
            log_file,
            maxBytes=MAX_LOG_BYTES,
            backupCount=BACKUP_COUNT,
        )
        handler.setFormatter(logging.Formatter(LOG_FORMAT))
        logger.addHandler(handler)
        return True
    except OSError:
        return False


def _add_console_handler(logger: logging.Logger) -> None:
    """Attach a console handler. Uses Rich if available, plain StreamHandler otherwise."""
    try:
        from rich.logging import RichHandler

        handler = RichHandler(
            rich_tracebacks=True,
            show_path=False,
            markup=True,
        )
    except ImportError:
        handler = logging.StreamHandler(sys.stderr)  # type: ignore[assignment]
        handler.setFormatter(logging.Formatter(LOG_FORMAT))

    logger.addHandler(handler)


def setup_logging(
    task_name: str | None = None,
    run_id: str | None = None,
    verbose: bool = False,
    log_dir: Path | None = None,
) -> logging.Logger:
    """Configure and return the system-tender logger.

    Args:
        task_name: Name of the task being run (appears in log prefix).
        run_id: Unique identifier for this run (appears in log prefix).
        verbose: If True, set DEBUG level; otherwise INFO.
        log_dir: Directory for log files. Defaults to ~/.config/system-tender/logs/

    Returns:
        Configured logging.Logger instance.
    """
    logger = logging.getLogger(LOGGER_NAME)
    logger.handlers.clear()
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)

    # Structured context filter on every record
    logger.addFilter(ContextFilter(task_name=task_name, run_id=run_id))

    # Console always present (interactive or piped)
    if sys.stderr.isatty():
        _add_console_handler(logger)
    else:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter(LOG_FORMAT))
        logger.addHandler(handler)

    # Syslog/journald - best effort
    _add_syslog_handler(logger)

    # File handler - guaranteed fallback
    _add_file_handler(logger, log_dir or DEFAULT_LOG_DIR)

    return logger
