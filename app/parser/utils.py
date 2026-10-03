"""Parser utilities: atomic writes, structured logging, and common helpers."""

from __future__ import annotations

import json
import logging
import os
import secrets
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from _typeshed import StrOrBytesPath

# Module-level logger cache
_loggers: dict[str, logging.Logger] = {}


def write_atomic(
    path: StrOrBytesPath,
    data: str | bytes,
    encoding: str | None = "utf-8",
) -> None:
    """Write data atomically via temp + os.replace.

    Creates a temporary file in the same directory, writes the content,
    then atomically replaces the target. Works on Windows and POSIX.
    Parent directories are created if they don't exist.

    Args:
        path: Target file path
        data: Content to write (str or bytes)
        encoding: Encoding for str data (default utf-8), ignored for bytes

    Raises:
        OSError: If write or replace fails
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)

    # Create temp file in same directory (required for atomic os.replace)
    tmp_name = f"{target.name}.tmp{secrets.token_hex(4)}"
    tmp_path = target.parent / tmp_name

    try:
        if isinstance(data, bytes):
            tmp_path.write_bytes(data)
        else:
            tmp_path.write_text(data, encoding=encoding or "utf-8")
        # Atomic replace (Windows + POSIX)
        os.replace(tmp_path, target)
    except Exception:
        # Clean up temp file on failure
        try:
            tmp_path.unlink()
        except Exception:
            pass
        raise


def get_logger(name: str) -> logging.Logger:
    """Get or create a structured logger for the given name.

    Returns a logger configured with:
    - Console handler (INFO level, simple format)
    - File handler if PARSER_LOG_FILE env var is set (JSON format)
    - Propagation disabled to avoid duplicate messages

    Loggers are cached per name.
    """
    if name in _loggers:
        return _loggers[name]

    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    # Console handler (simple format)
    if not any(isinstance(h, logging.StreamHandler) for h in logger.handlers):
        console = logging.StreamHandler(sys.stdout)
        console.setLevel(logging.INFO)
        console.setFormatter(logging.Formatter("[%(levelname)s] %(name)s: %(message)s"))
        logger.addHandler(console)

    # File handler (JSON format) if env var set
    log_file = os.environ.get("PARSER_LOG_FILE")
    if log_file and not any(
        isinstance(h, logging.FileHandler) for h in logger.handlers
    ):
        file_handler = logging.FileHandler(log_file, mode="a", encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(JsonFormatter())
        logger.addHandler(file_handler)

    _loggers[name] = logger
    return logger


class JsonFormatter(logging.Formatter):
    """Format log records as JSON lines."""

    def format(self, record: logging.LogRecord) -> str:
        log_obj = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S.%fZ"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Add exception info if present
        if record.exc_info:
            log_obj["exc_info"] = self.formatException(record.exc_info)

        # Add any extra fields from record.__dict__
        for key, value in record.__dict__.items():
            if key not in (
                "name",
                "msg",
                "args",
                "created",
                "filename",
                "funcName",
                "levelname",
                "levelno",
                "lineno",
                "module",
                "msecs",
                "message",
                "pathname",
                "process",
                "processName",
                "relativeCreated",
                "thread",
                "threadName",
                "exc_info",
                "exc_text",
                "stack_info",
            ):
                log_obj[key] = value

        return json.dumps(log_obj, default=str)


class LedgerCorruptionError(Exception):
    """Raised when a ledger file is corrupted and cannot be recovered."""
