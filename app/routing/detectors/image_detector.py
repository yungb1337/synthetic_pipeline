"""ImageDetector: image presence/density + scanned-page probability (spec §4)."""
from __future__ import annotations

from ..inspectors import InspectorFeatures
from .base import Detector, DetectorResult


class ImageDetector(Detector):
    name = "image"
    version = "1.0.0"

    def can_evaluate(self, feats: InspectorFeatures) -> bool:
        return feats.page_count > 0

    def _evaluate(self, feats: InspectorFeatures) -> DetectorResult:
        sigs = []
        sigs.append(self._signal("metric_image_count", feats.image_count,
                                 evidence=f"{feats.image_count} images"))
        sigs.append(self._signal(
            "metric_images_per_page",
            round(feats.image_count / max(1, feats.page_count), 3),
        ))
        full = feats.full_image_pages
        sigs.append(self._signal(
            "metric_full_image_page_count", len(full),
            evidence=f"pages {full} are full-bleed raster" if full else None,
        ))

        if feats.page_count <= 0:
            return DetectorResult(self.name, self.version, "ok", signals=sigs)

        # verified absence (a real measurement) is 0 — NOT a missing signal.
        ratios = [feats.pages_image_ratio.get(p, 0.0) for p in range(feats.page_count)]
        density = round(min(1.0, sum(ratios) / max(1, feats.page_count)), 3)
        sigs.append(self._signal("metric_image_density", density, confidence=0.95,
                                 evidence=f"image area avg {density:.3f}/page"))

        # Scanned probability is DRIVEN by the continuous per-page image-ownership
        # ratio, GUARDED by text: a page with real embedded text (even a bordered /
        # logo-heavy certificate) is NOT scanned, whatever its image coverage.
        # Text-less image pages dominate (conservative single-page max) so one
        # truly scanned page escalates a mixed doc toward enrichment/docling.
        scan_pages = [
            r for p, r in enumerate(ratios)
            if feats.pages_char_count.get(p, 0) == 0
        ]
        worst = max(scan_pages) if scan_pages else 0.0
        sigs.append(self._signal(
            "metric_scanned_page_probability", round(min(1.0, worst), 3),
            confidence=0.9 if scan_pages else 1.0,
            evidence=(f"worst text-less page ratio {worst:.3f}"
                      if scan_pages else "no text-less image page"),
        ))
        return DetectorResult(self.name, self.version, "ok", signals=sigs)