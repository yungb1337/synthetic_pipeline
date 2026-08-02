"""Minimal outbox/event publisher for the parser module.

The parser emits ONE top-level event (`document.parsed.v1` or
`document.parse_failed`) that downstream workflow layers consume. v0.1
defaults to a console sink; swapping to a real broker is a Store-like seam.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Callable

Sink = Callable[[str, dict], None]


def _console(name: str, payload: dict) -> None:
    line = json.dumps({"event": name, "time": f"{time.time():.3f}", **payload}, ensure_ascii=False, default=str)
    print(f"[event] {line}")


def _silent(name: str, payload: dict) -> None:
    """For batch/long-running pipelines events go to a broker, not stdout."""
    return None


@dataclass
class EventPublisher:
    sink: Sink = field(default_factory=lambda: _console)

    def emit(self, name: str, payload: dict) -> None:
        self.sink(name, payload)