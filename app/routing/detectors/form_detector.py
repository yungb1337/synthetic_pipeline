"""FormDetector: form-like probability from cheap line/block heuristics.

Form = key-value rows (e.g. "Name: X"). v1 has NO forms engine; this is a
cheap heuristic from short-label density. A doc where forms are impossible is
`not_applicable`, never a quiet "no form". A missing measurement is never
treated as no-form (spec §4, §11).
"""
from __future__ import annotations

from ..inspectors import InspectorFeatures
from .base import Detector, DetectorResult


class FormDetector(Detector):
    name = "form"
    version = "1.0.0"

    def can_evaluate(self, feats: InspectorFeatures) -> bool:
        # any PDF with text could theoretically be a form; stay evaluable.
        return feats.page_count > 0

    def _evaluate(self, feats: InspectorFeatures) -> DetectorResult:
        # Cheap cue: many short, label→value-looking rows, or many rows on a page.
        total_chars = sum(feats.pages_char_count.values())
        blocks = sum(feats.block_count_per_page)
        if blocks <= 0 or total_chars <= 0:
            return DetectorResult(self.name, self.version, "ok", signals=[
                self._sig_missing("metric_form_probability", "no text to assess form-ness"),
            ])
        avg_len = total_chars / blocks
        # forms tend to have short cells; more blocks/page + short avg => form-ish
        density = min(1.0, blocks / max(1, feats.page_count) / 40.0)
        short = min(1.0, max(0.0, (20.0 - avg_len) / 20.0)) if avg_len < 20 else 0.0
        prob = round(0.5 * density + 0.5 * short, 3)
        if prob > 0:
            conf = 0.6
            evidence = f"form-like density {prob:.2f} (avg_len={avg_len:.0f})"
        else:
            conf = 0.9
            evidence = "no form-like structure observed"
        sigs = [self._signal("metric_form_probability", prob, confidence=conf, evidence=evidence)]
        return DetectorResult(self.name, self.version, "ok", signals=sigs)