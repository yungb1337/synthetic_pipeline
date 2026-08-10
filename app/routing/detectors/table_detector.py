"""TableDetector: table presence. Key §11 rule: a FAILED table detection is
recorded as `failed` and NEVER re-phrase as a valid "no table" (`missing` is
not `0`)."""
from __future__ import annotations

from ..inspectors import InspectorFeatures
from .base import Detector, DetectorResult


class TableDetector(Detector):
    name = "table"
    version = "1.0.0"

    def can_evaluate(self, feats: InspectorFeatures) -> bool:
        return feats.page_count > 0

    def _evaluate(self, feats: InspectorFeatures) -> DetectorResult:
        sigs = []
        present = feats.detected_tables
        if present is None:
            # the finder had nothing / is unavailable -> NOT "no table"
            sigs.append(self._sig_missing("metric_table_present", "table detection unavailable/failed"))
            sigs.append(self._signal("metric_table_probability", None, status="missing",
                                     evidence="table probability unknown"))
            return DetectorResult(self.name, self.version, "ok", signals=sigs)
        if present > 0:
            prob = round(min(1.0, present / 4.0), 3)
            sigs.append(self._signal("metric_table_present", True,
                                     evidence=f"{present} table(s) found"))
            sigs.append(self._signal("metric_table_probability", prob, confidence=0.9,
                                     evidence=f"{present} table(s)"))
        else:
            sigs.append(self._signal("metric_table_present", False, confidence=1.0,
                                     evidence="no table detected (ok measurement)"))
            sigs.append(self._signal("metric_table_probability", 0.0, confidence=1.0,
                                     evidence="no table (measured absent)"))
        return DetectorResult(self.name, self.version, "ok", signals=sigs)