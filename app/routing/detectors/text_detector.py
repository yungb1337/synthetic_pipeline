"""TextDetector: embedded/text density, ratio, density & extraction status."""
from __future__ import annotations

from ..inspectors import InspectorFeatures, LOW_TEXT_CHARS
from .base import Detector, DetectorResult


class TextDetector(Detector):
    name = "text"
    version = "1.0.0"

    def can_evaluate(self, feats: InspectorFeatures) -> bool:
        return feats.page_count > 0

    def _evaluate(self, feats: InspectorFeatures) -> DetectorResult:
        sigs = []
        total_chars = sum(feats.pages_char_count.values())
        sigs.append(self._signal("metric_total_char_count", total_chars,
                                 evidence=f"{total_chars} printable chars"))
        avg = total_chars / max(1, feats.page_count)
        sigs.append(self._signal("metric_char_per_page", round(avg, 2),
                                 evidence=f"~{avg:.0f} chars/page"))

        if feats.text_ratio is not None:
            sigs.append(self._signal("metric_text_ratio", round(feats.text_ratio, 4),
                                     evidence=f"text ratio {feats.text_ratio:.3f}"))
        else:
            sigs.append(self._sig_missing("metric_text_ratio", "text ratio unavailable"))

        # a text-poor page: a real observation of 'little/no text' — when it
        # happens it drives the OCR/scan heuristics, never a silent False.
        low_pages = sum(1 for p in range(feats.page_count) if feats.pages_char_count.get(p, 0) < LOW_TEXT_CHARS)
        low_text_ratio = low_pages / max(1, feats.page_count)
        sigs.append(self._signal(
            "metric_low_text_ratio", round(low_text_ratio, 3),
            confidence=0.9 if low_text_ratio else 1.0,
            evidence=f"{low_pages}/{feats.page_count} text-poor pages",
        ))

        # whole-document low char density (0..1; 1 => essentially no text)
        if total_chars == 0:
            density = 1.0 if feats.page_count else 0.0
            sigs.append(self._signal("metric_low_char_density", density,
                                     confidence=0.8, evidence="no embedded text"))
            for p in (p for p in range(feats.page_count) if feats.pages_char_count.get(p, 0) < LOW_TEXT_CHARS):
                sigs.append(self._signal("metric_page_char_density_none", None,
                                         status="missing", confidence=None,
                                         evidence=f"page {p} has no text blocks (scanned?)"))
        else:
            # normalize chars/page: below ~60 chars/page is a low-density doc
            per_page = [feats.pages_char_count.get(p, 0) for p in range(feats.page_count)]
            dense = per_page and min(per_page) >= max(LOW_TEXT_CHARS, 3)
            sigs.append(self._signal(
                "metric_low_char_density", 0.0 if dense else round(1 - (min(per_page) / 2000.0), 3),
                confidence=(1.0 if dense else 0.7),
                evidence="dense text" if dense else "some text-poor pages",
            ))

        return DetectorResult(self.name, self.version, "ok", signals=sigs)