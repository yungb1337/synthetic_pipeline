"""Routing policy: tiers + conservative low-confidence fallback (spec §6, §14).

Bands are config, not constants (§6, §17). On LOW confidence we escalate ONE
tier toward the more capable pipeline (native→enrichment, enrichment→docling,
docling bounded to docling) and NEVER downgrade — an uncertain simple doc is
over-sent (safe, just wasteful) rather than an uncertain complex doc sent to a
weaker pipeline (loses fidelity)."""
from __future__ import annotations

from .config import RoutingConfig


class RoutingPolicy:
    def __init__(self, routing_config: RoutingConfig):
        self.config = routing_config
        self._by_name: dict[str, tuple[int, int]] = routing_config.band_names

    def bounded_band(self, complexity: float) -> str:
        """Band for a raw complexity score (no confidence handling)."""
        for lo, hi, name in self.config.bands:
            if lo <= int(complexity) <= hi:
                return name
        # guards against a floating rounding-out-of-range score
        return self.config.bands[-1][2] if complexity > 100 else self.config.bands[0][2]

    def _escalate(self, band: str) -> str:
        order = [b for _lo, _hi, b in self.config.bands]
        idx = order.index(band) if band in order else 0
        return order[min(idx + 1, len(order) - 1)]   # bounded: never past docling

    def route(self, complexity: float, confidence: float) -> str:
        band = self.bounded_band(complexity)
        if confidence < self.config.low_conf_threshold(band):
            return self._escalate(band)
        return band