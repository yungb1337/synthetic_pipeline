"""MetadataDetector: document-level structural metadata (spec §4 'Document')."""
from __future__ import annotations

from ..inspectors import InspectorFeatures
from .base import Detector, DetectorResult


class MetadataDetector(Detector):
    name = "metadata"
    version = "1.0.0"

    def can_evaluate(self, feats: InspectorFeatures) -> bool:
        return True  # cheap doc-level metadata always observable for a PDF

    def _evaluate(self, feats: InspectorFeatures) -> DetectorResult:
        sigs = []
        available = bool(feats.pdf_format) or feats.pdf_version is not None
        sigs.append(self._signal(
            "metric_foundation_meta_available", available,
            confidence=1.0 if available else 0.0,
            evidence="PDF metadata present" if available else "no PDF metadata",
        ))
        if feats.pdf_version is not None:
            sigs.append(self._signal("metric_pdf_version", feats.pdf_version, evidence=f"PDF {feats.pdf_version}"))
        else:
            sigs.append(self._sig_missing("metric_pdf_version", "pdf version unavailable"))

        if feats.encrypted is not None:
            sigs.append(self._signal("metric_encrypted", bool(feats.encrypted),
                                     evidence="encrypted" if feats.encrypted else "not encrypted"))
        else:
            sigs.append(self._sig_missing("metric_encrypted", "encryption state unavailable"))

        if feats.producer or feats.creator:
            sigs.append(self._signal("metric_producer_present", True,
                                     evidence=f"producer={feats.producer or feats.creator}"))
        else:
            sigs.append(self._sig_missing("metric_producer_present",
                                          "no producer/creator (missing, not low quality)"))

        if feats.has_outline is not None:
            sigs.append(self._signal("metric_has_outline", bool(feats.has_outline),
                                     evidence="has outline" if feats.has_outline else None))
        else:
            sigs.append(self._sig_missing("metric_has_outline", "outline unavailable"))

        if feats.has_tag is not None:
            sigs.append(self._signal("metric_has_tag", bool(feats.has_tag),
                                     evidence="tagged PDF" if feats.has_tag else None))
        else:
            sigs.append(self._sig_missing("metric_has_tag", "tag unavailable"))

        return DetectorResult(self.name, self.version, "ok", signals=sigs)