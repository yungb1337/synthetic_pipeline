"""Minimal outbox/event publisher for the parser module.

The parser emits ONE top-level event (`document.parsed.v1` or
`document.parse_failed`) that downstream workflow layers consume. v0.1
defaults to a console sink; swapping to a real broker is a Store-like seam.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .utils import get_logger

logger = get_logger(__name__)

Sink = Callable[[str, dict], None]


def _console(name: str, payload: dict) -> None:
    line = json.dumps({"event": name, "time": f"{time.time():.3f}", **payload}, ensure_ascii=False, default=str)
    print(f"[event] {line}")


def _silent(name: str, payload: dict) -> None:
    """For batch/long-running pipelines events go to a broker, not stdout."""
    return None


def silent_sink() -> Sink:
    """Public factory for a no-op sink (batch/long-running pipelines)."""
    return _silent


def file_sink(log_path: str) -> Sink:
    """Append events as JSON lines to log_path.

    Returns a sink function that writes each event as a JSON line.
    Creates parent directories if needed. Errors are logged but do not
    crash the pipeline.

    Args:
        log_path: Path to the event log file (e.g., "output/events.jsonl")
    """
    def _write(name: str, payload: dict) -> None:
        try:
            p = Path(log_path)
            p.parent.mkdir(parents=True, exist_ok=True)
            line = json.dumps({"event": name, "time": time.time(), **payload}, ensure_ascii=False, default=str)
            with open(p, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception as e:
            logger.error(f"Failed to write event to {log_path}: {e}")
    return _write


@dataclass
class EventPublisher:
    sink: Sink = field(default_factory=lambda: _console)

    def emit(self, name: str, payload: dict) -> None:
        self.sink(name, payload)