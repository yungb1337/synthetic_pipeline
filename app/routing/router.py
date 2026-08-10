"""Router assembly + observability (spec §8-§13, architecture §6).

`Router` is the ONLY place that turns inspected features into a
`RoutingDecision`: inspector -> detectors -> scorer -> policy. It is
decision-only — it NEVER calls the loaders, never extracts, never renders,
and carries no pipeline-execution logic.

Determinism (§12): routing is a pure function of `(data, detected,
RoutingConfig)`. No RNG, no environment-dependent thresholds. The measured
`inspection_time_ms` is observability metadata (a measurement, not part of the
decision), so the reproducible decision fields (route/complexity/confidence/
versions/signals) stay deterministic.
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict

from .config import RoutingConfig
from .detectors import Detector, get_detectors
from .inspectors import FastInspector
from .policy import RoutingPolicy
from .scoring import Scorer, WeightedHeuristicScorer
from .schema import RoutingDecision, Signal


class RoutingStats:
    """In-memory, thread-safe counters + a recent-decision ring (spec §13)."""

    def __init__(self, ring: int = 32):
        self._lock = threading.Lock()
        self.inspected = 0
        self.by_band: dict[str, int] = defaultdict(int)
        self.detector_failures: dict[str, int] = defaultdict(int)
        self.total_inspection_ms = 0.0
        self.max_inspection_ms = 0.0
        self.score_buckets: dict[int, int] = defaultdict(int)     # 0-100/10
        self.conf_buckets: dict[int, int] = defaultdict(int)      # 0-100/10
        self.missing_count = 0
        self.unknown_signal_count = 0            # Gap B: unweighted names
        self._decisions: list[RoutingDecision] = []
        self._ring = ring

    def record(self, decision: RoutingDecision, inspection_ms: float) -> None:
        with self._lock:
            self.inspected += 1
            self.by_band[decision.route] += 1
            self.total_inspection_ms += inspection_ms
            self.max_inspection_ms = max(self.max_inspection_ms, inspection_ms)
            self.score_buckets[int(decision.complexity_score) // 10] += 1
            self.conf_buckets[int(decision.confidence * 10)] += 1
            self._decisions.append(decision)
            if len(self._decisions) > self._ring:
                self._decisions.pop(0)
        for s in decision.signals:
            if s.status in ("missing",):
                self.missing_count += 1

    def detector_failure(self, detector: str) -> None:
        with self._lock:
            self.detector_failures[detector] += 1

    def note_unknown_signal(self, name: str = "") -> None:
        """Gap B: count an unweighted/unknown Signal name (warn+skip)."""
        with self._lock:
            self.unknown_signal_count += 1

    def last(self) -> dict:
        with self._lock:
            if not self._decisions:
                return {}
            return self._decisions[-1].model_dump()

    def stats(self) -> dict:
        with self._lock:
            insp = self.inspected or 1
            return {
                "inspected": self.inspected,
                "by_band": dict(self.by_band),
                "avg_inspection_ms": round(self.total_inspection_ms / insp, 3),
                "max_inspection_ms": round(self.max_inspection_ms, 3),
                "detector_failures": dict(self.detector_failures),
                "score_buckets": dict(self.score_buckets),
                "confidence_buckets": dict(self.conf_buckets),
                "missing_signal_count": self.missing_count,
                "unknown_signal_count": self.unknown_signal_count,
            }


class Router:
    def __init__(
        self,
        routing_config: RoutingConfig | None = None,
        detectors: list[Detector] | None = None,
        scorer: Scorer | None = None,
        policy: RoutingPolicy | None = None,
        inspector: FastInspector | None = None,
        stats: RoutingStats | None = None,
    ):
        self.config = routing_config or RoutingConfig()
        self.detectors = detectors if detectors is not None else get_detectors()
        self.scorer = scorer or WeightedHeuristicScorer(self.config)
        self.policy = policy or RoutingPolicy(self.config)
        self.inspector = inspector or FastInspector()
        self.stats = stats or RoutingStats()

    def route(self, data: bytes, detected) -> RoutingDecision | None:
        """Route one document. Returns None when routing is not applicable
        (non-PDF, unresolved), else a v full `RoutingDecision`."""
        if detected is None or getattr(detected, "unresolved", False):
            return None
        if getattr(detected, "slug", None) != "pdf":
            return None               # v1 routes PDFs only (Gap A: images stay native)

        t0 = time.time()
        features = self.inspector.inspect(data)
        if features is None:
            # cannot inspect this PDF -> no evidence -> no-route native at low conf
            decision = RoutingDecision(
                route="native", complexity_score=0, confidence=0.05,
                reasons=["cannot inspect this PDF (no routing evidence)"],
                signals=[], router_version=self.config.router_version,
                policy_version=self.config.policy_version,
                scoring_version=self.config.scoring_version,
                inspection_time_ms=round((time.time() - t0) * 1000, 3),
                bands=self.config.band_names,
            )
            self.stats.record(decision, (time.time() - t0) * 1000)
            return decision

        all_signals: list[Signal] = []
        for det in self.detectors:
            result = det.evaluate(features)
            if result.status == "failed":
                self.stats.detector_failure(det.name)
            for s in result.signals:
                all_signals.append(s)
            if result.status == "failed" and not result.signals:
                all_signals.append(
                    Signal(detector=det.name, version=det.version,
                           name="metric_detector_failed", value=None,
                           status="failed", evidence=result.error)
                )
        all_signals = self._filter_known(all_signals)

        score = self.scorer.score(all_signals, features)
        band = self.policy.route(score.complexity, score.confidence)
        inspection_ms = (time.time() - t0) * 1000

        decision = RoutingDecision(
            route=band,
            complexity_score=int(round(score.complexity)),
            confidence=round(score.confidence, 4),
            reasons=score.reasons,
            signals=all_signals,
            router_version=self.config.router_version,
            policy_version=self.config.policy_version,
            scoring_version=self.config.scoring_version,
            detector_versions={d.name: d.version for d in self.detectors},
            inspection_time_ms=round(inspection_ms, 3),
            bands=self.config.band_names,
        )
        self.stats.record(decision, inspection_ms)
        return decision

    def _filter_known(self, signals: list[Signal]) -> list[Signal]:
        """Gap B (orchestrator ruling): a Signal whose name is NOT in
        `RoutingConfig.weights` is warned, skipped, and counted into
        `RoutingStats` — never a crash and never treated as a negative."""
        known = self.config.weights
        out: list[Signal] = []
        for s in signals:
            if s.name not in known:
                self.stats.note_unknown_signal(s.name)
                continue
            out.append(s)
        return out