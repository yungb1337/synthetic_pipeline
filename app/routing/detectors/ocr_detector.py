"""OcrDetector: how likely OCR is required/possible. NEVER invokes OCR during
inspection (spec §4: inspection stays far cheaper than the processing it
avoids). A text-borne doc is `not_applicable` here — that is NOT a reading-order
penalty, just "no OCR concern" (§5)."""
from __future__ import annotations

from ..inspectors import InspectorFeatures, LOW_TEXT_CHARS
from .base import Detector, DetectorResult


class OcrDetector(Detector):
    name = "ocr"
    version = "1.0.0"

    def can_evaluate(self, feats: InspectorFeatures) -> bool:
        # OCR is a concern only once some page is text-poor (likely scanned).
        low = [p for p in range(feats.page_count) if feats.pages_char_count.get(p, 0) < LOW_TEXT_CHARS]
        return bool(low) or len(feats.full_image_pages) > 0

    def _evaluate(self, feats: InspectorFeatures) -> DetectorResult:
        pages = max(1, feats.page_count)
        low = [p for p in range(feats.page_count) if feats.pages_char_count.get(p, 0) < LOW_TEXT_CHARS]
        need = len(low) / pages
        image_scan = len(feats.full_image_pages) / pages
        required = round(min(1.0, 0.6 * need + 0.4 * image_scan), 3)
        confidence = 0.9 if required else 0.7
        sigs = [self._signal(
            "metric_ocr_required", required,
            confidence=confidence,
            evidence=(
                f"{len(low)} text-poor page(s), {len(feats.full_image_pages)} scanned"
                if required else "OCR optional"
            ),
        )]
        return DetectorResult(self.name, self.version, "ok", signals=sigs)