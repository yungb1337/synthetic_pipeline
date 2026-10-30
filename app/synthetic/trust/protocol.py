"""Trust Gate Protocol and Result Schema."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from app.synthetic.schema import SyntheticRecord


@dataclass
class GateResult:
    """Standardized result returned by all trust verification gates."""

    passed: bool
    score: float | None = None
    message: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class TrustGate(Protocol):
    """Protocol for trust and verification gates."""

    def evaluate(self, record: SyntheticRecord, **kwargs: Any) -> GateResult:
        """Evaluates a synthetic record, returning a typed GateResult."""
        ...
