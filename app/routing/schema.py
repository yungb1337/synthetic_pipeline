"""Leaf evidence models for the routing layer.

This module is a LEAF on purpose: it imports ONLY pydantic, so the parser's
DOM model can reference a `RoutingDecision` without a circular dependency and
without any parser->router coupling beyond a plain type reference
(architecture.md §2).

Semantics (spec §4, §11): a missing observation is `value=None` +
`status="missing"` — NEVER coerced to a 0/False "negative"; a failure is
`status="failed"` — also a missing placeholder, never a real evidence string.
"""
from __future__ import annotations

from typing import Literal, Optional, Union

from pydantic import BaseModel, Field

# The only value types a Signal may carry (spec §4: int/float/bool/str).
SignalValue = Union[int, float, bool, str, None]

SignalStatus = Literal["ok", "failed", "missing", "not_applicable"]


class Signal(BaseModel):
    """One unit of decision-free evidence emitted by a detector (spec §4)."""

    detector: str
    version: str
    name: str
    value: Optional[SignalValue] = None
    confidence: Optional[float] = None      # 0..1; None = not established
    evidence: Optional[str] = None          # short human-readable reason (§8)
    status: SignalStatus = "ok"

    def is_evidence(self) -> bool:
        """True when this is a usable (non-failed/non-missing) observation."""
        return self.status == "ok" and self.value is not None

    def is_strong(self, threshold: float = 0.5) -> bool:
        """A 'ok' signal whose magnitude actually leans toward complexity."""
        return self.status == "ok" and isinstance(self.value, (int, float)) and float(self.value) >= threshold


class RoutingDecision(BaseModel):
    """The full, versioned routing decision (architecture §6; spec §9, §10)."""

    route: str
    complexity_score: int
    confidence: float
    reasons: list[str] = Field(default_factory=list)
    signals: list[Signal] = Field(default_factory=list)
    router_version: str
    policy_version: str
    scoring_version: str
    detector_versions: dict[str, str] = Field(default_factory=dict)
    inspection_time_ms: float
    # band -> (lo, hi) co-ordinates for audit/regression (§6). Plain dict on
    # purpose so the tool preserves the ints (and JSON renders them as-is).
    bands: dict[str, tuple[int, int]] = Field(default_factory=dict)