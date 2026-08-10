"""LayoutDetector: multi-column + layout-complexity + spatial fragmentation."""
from __future__ import annotations

from ..inspectors import InspectorFeatures
from .base import Detector, DetectorResult


class LayoutDetector(Detector):
    name = "layout"
    version = "1.0.0"

    def can_evaluate(self, feats: InspectorFeatures) -> bool:
        return feats.page_count > 0

    def _evaluate(self, feats: InspectorFeatures) -> DetectorResult:
        sigs = []
        multi = feats.est_multi_column_pages
        if multi:
            sigs.append(self._signal(
                "metric_multi_column_probability",
                round(len(multi) / max(1, feats.page_count), 3),
                confidence=0.85,
                evidence=f"{len(multi)} multi-column page(s)",
            ))
        else:
            sigs.append(self._signal(
                "metric_multi_column_probability", 0.0, confidence=0.9,
                evidence="no multi-column page detected",
            ))

        # layout complexity from text-block distribution across pages
        counts = feats.block_count_per_page or [0]
        peak = max(counts)
        sigs.append(self._signal(
            "metric_layout_complexity", round(min(1.0, peak / 40.0), 3),
            evidence=f"max {peak} blocks on a page",
        ))
        # spatial fragmentation: pages dominated by scattered tiny blocks
        blocks_total = sum(counts)
        pages = max(1, feats.page_count)
        per_page_avg = blocks_total / pages
        frag = min(1.0, (per_page_avg / 40.0) + (len(multi) / max(1, pages)))
        sigs.append(self._signal("metric_block_fragmentation", round(frag, 3)))

        return DetectorResult(self.name, self.version, "ok", signals=sigs)