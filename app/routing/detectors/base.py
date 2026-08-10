"""Detector contract + failure isolation + registry (architecture §3, §5).

Each detector owns ONE concern, decides whether the document even supports it
(`can_evaluate` -> `not_applicable`, never a negative), and returns a
`DetectorResult` of structured `Signal`s. `evaluate` is wrapped in try/except
so a failing detector records a `failed` result (never re-raises, never
manufactures a negative signal) — one bad detector cannot crash a document or
routing (spec §11).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from ..inspectors import InspectorFeatures
from ..schema import Signal


@dataclass
class DetectorResult:
    detector: str
    version: str
    status: str                  # "ok" | "failed" | "not_enough/data"
    error: str | None = None
    signals: list[Signal] = field(default_factory=list)


class Detector(ABC):
    """A decision-free observation unit; one per concern (spec §5)."""

    #: stable id, e.g. "text" (§10 independence relies on version being stable)
    name: str = ""
    version: str = "0.0.0"

    @abstractmethod
    def can_evaluate(self, feats: InspectorFeatures) -> bool:
        """Cheap predicate: can this concern be observed at all on `feats`?
        Returning False is recorded as `not_applicable`, never as negative
        evidence (spec §5)."""

    @abstractmethod
    def _evaluate(self, feats: InspectorFeatures) -> DetectorResult:
        """Decision-free observation; returns >=0 Signals."""

    # -- failure-isolated entry point ----------------------------------------
    def evaluate(self, feats: InspectorFeatures) -> DetectorResult:
        try:
            if not self.can_evaluate(feats):
                return DetectorResult(self.name, self.version, "not_applicable")
            return self._evaluate(feats)
        except Exception as e:                # spec §11: record, never re-raise
            return DetectorResult(
                self.name,
                self.version,
                "failed",
                error=str(e),
                signals=[
                    Signal(
                        detector=self.name,
                        version=self.version,
                        name="metric_detector_failed",
                        value=None,
                        status="failed",
                        evidence=f"{self.name} evaluation failed: {e}",
                    )
                ],
            )

    def _signal(self, name, value=None, confidence=None, evidence=None, status="ok") -> Signal:
        return Signal(
            detector=self.name,
            version=self.version,
            name=name,
            value=value,
            confidence=confidence,
            evidence=evidence,
            status=status,
        )

    def _sig_missing(self, name: str, evidence: str | None = None) -> Signal:
        """A 'missing' signal: value None, status missing — never a 0/False."""
        return Signal(detector=self.name, version=self.version, name=name,
                      value=None, status="missing", evidence=evidence)