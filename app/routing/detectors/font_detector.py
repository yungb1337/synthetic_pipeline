"""FontDetector: font diversity / embedding / unusual usage (spec §9 Typography).

Reads font info from the text spans (`features.fonts`). Missing font info is
`missing`, never defaulted to a positive."""
from __future__ import annotations

from ..inspectors import InspectorFeatures
from .base import Detector, DetectorResult

_KOWN_FON = ("helv", "times", "tiro", "nimbus", "arial", "courier", "symbol")


class FontDetector(Detector):
    name = "font"
    version = "1.0.0"

    def can_evaluate(self, feats: InspectorFeatures) -> bool:
        return bool(feats.fonts)  # no font info -> nothing to assess

    def _evaluate(self, feats: InspectorFeatures) -> DetectorResult:
        sigs = []
        fonts = feats.fonts
        diversity = min(1.0, len(fonts) / 8.0)
        sigs.append(self._signal(
            "metric_font_diversity", round(diversity, 3),
            confidence=0.9 if len(fonts) else 1.0,
            evidence=f"{len(fonts)} distinct font(s)",
        ))
        unusual = [fn for fn in fonts if not any(k in fn.lower() for k in _KOWN_FON)]
        sigs.append(self._signal(
            "metric_unusual_font", bool(unusual),
            evidence=f"{len(unusual)} unusual font(s)" if unusual else "only standard fonts",
        ))
        sigs.append(self._signal(
            "metric_font_embedded", len(fonts) > 0, confidence=1.0,
            evidence=f"{len(fonts)} font(s) observed",
        ))
        return DetectorResult(self.name, self.version, "ok", signals=sigs)