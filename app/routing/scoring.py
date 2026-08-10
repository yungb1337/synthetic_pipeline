"""Scoring abstraction (spec §6, architecture §5).

`Scorer` is behind a `Protocol` so it can later be heuristics / rules /
statistical / a learned model without touching the router or pipeline. v1 =
`WeightedHeuristicScorer` — a pure, deterministic weighted sum of detector
signals with a confidence derived from measurement coverage.

The scorer knows NOTHING about bands — bands are applied by the policy outside
this module (architecture §5). It never reads `routing_config.layout_backend`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, TypeVar

from .config import RoutingConfig
from .inspectors import InspectorFeatures
from .schema import Signal

_Value = TypeVar("_Value")


@dataclass
class Score:
    complexity: float        # 0..100
    confidence: float        # 0..1
    reasons: list[str] = field(default_factory=list)


class Scorer(Protocol):
    def score(self, signals: list[Signal], features: InspectorFeatures) -> Score:
        """Turning detector evidence into a normalized complexity + confidence
        (+ human reasons). No knowledge of the output band."""


def _to_unit(value) -> float:
    """Coerce a signal value to a 0..1 magnitude (True=>1, False=>0, clip)."""
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    try:
        f = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, f))


class WeightedHeuristicScorer:
    """Deterministic, config-driven scorer (ADR-011 §6; the v1 impl)."""

    def __init__(self, routing_config: RoutingConfig):
        self.config = routing_config
        # positive weights are the only complexity-driving signals
        self._pos = {name: w for name, w in routing_config.weights.items() if w > 0}
        self._pos_total = sum(self._pos.values()) or 1.0

    def score(self, signals: list[Signal], features: InspectorFeatures) -> Score:
        # `measured` = every signal actually observed (incl. value 0) — drives
        # CONFIDENCE (a measured 0 is a real result: "I looked and it's not an
        # issue"). `positive` = only signals with value>0 — drives COMPLEXITY.
        # This keeps a genuinely simple doc at both low complexity AND high
        # confidence, while a doc with strong positive evidence concentrates it.
        measured: dict[str, float] = {}
        positive: dict[str, float] = {}
        for s in signals:
            if s.name not in self._pos:
                continue  # informational / unweighted signals never drive the score
            if s.status == "ok" and s.value is not None:
                v = _to_unit(s.value)
                measured[s.name] = v
                if v > 0.0:
                    positive[s.name] = v

        # complexity = ABSOLUTE weighted sum of positive evidence, 0..100.
        # This matches the spec's example weighting (§6) and makes the band the
        # direct job of the config weights: the scan cluster is calibrated to
        # cap below the 61 Docling threshold so a simply-scanned doc lands in
        # Enrichment (OCR), while a doc with genuine LAYOUT complexity on top
        # (columns / reading-order / tables) accumulates past 61 -> Docling.
        score = sum(self._pos[name] * v for name, v in positive.items())
        complexity = min(100.0, max(0.0, score))

        # confidence = share of ALL band-driving weight-mass that was measured.
        # Missing/failed signals reduce confidence (they are NOT a negative);
        # a measured zero does NOT lower confidence (it is a real result).
        solid_measured = sum(self._pos[name] for name in measured)
        confidence = solid_measured / self._pos_total if self._pos_total else 0.0

        reasons = self._reasons(signals)
        return Score(complexity=round(complexity, 3), confidence=round(confidence, 4), reasons=reasons)

    def _reasons(self, signals: list[Signal]) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        for s in signals:
            if s.name not in self._pos or s.name in seen:
                continue
            seen.add(s.name)
            if s.status == "ok" and s.value is not None and s.is_strong(0.5):
                evidence = s.evidence or f"{s.detector} reports {s.value}"
                out.append(f"high {s.name} ({s.value:.2f}) — {evidence}")
            elif s.status in ("missing", "failed"):
                out.append(f"{s.name} not measured ({s.status})")
        return out[:8]


class _DummyScorer:
    """Test seam that yields a deterministic fixed score (used by router tests)."""

    def __init__(self, complexity: float = 40.0, confidence: float = 0.9,
                 reasons: list[str] | None = None):
        self._complexity = complexity
        self._confidence = confidence
        self._reasons = reasons or ["dummy scorer"]

    def score(self, signals: list[Signal], features: InspectorFeatures) -> Score:
        return Score(complexity=self._complexity, confidence=self._confidence, reasons=self._reasons)