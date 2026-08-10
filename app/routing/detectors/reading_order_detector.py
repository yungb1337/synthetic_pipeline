"""ReadingOrderDetector: ambiquity in the reading order. Decision-free: it
REPORTS ambiguity; it never reorders any blocks (spec §3, §5)."""
from __future__ import annotations

from ..inspectors import InspectorFeatures
from .base import Detector, DetectorResult


class ReadingOrderDetector(Detector):
    name = "reading_order"
    version = "1.0.0"

    def can_evaluate(self, feats: InspectorFeatures) -> bool:
        return feats.page_count > 0

    def _evaluate(self, feats: InspectorFeatures) -> DetectorResult:
        sigs = []
        # ambiguity ~ multi-column presence + high block fragmentation
        pages = max(1, feats.page_count)
        col_ratio = len(feats.est_multi_column_pages) / pages
        blocks_total = sum(feats.block_count_per_page)
        frag = min(1.0, (blocks_total / max(1, pages)) / 40.0)
        ambiguity = round(min(1.0, 0.7 * col_ratio + 0.3 * frag), 3)
        if ambiguity == 0.0:
            sigs.append(self._signal("metric_reading_order_ambiguity", 0.0,
                                     confidence=0.9, evidence="linear reading order"))
        else:
            sigs.append(self._signal("metric_reading_order_ambiguity", ambiguity,
                                     confidence=0.8,
                                     evidence=f"reading-order ambiguity {ambiguity:.2f}"))
        return DetectorResult(self.name, self.version, "ok", signals=sigs)