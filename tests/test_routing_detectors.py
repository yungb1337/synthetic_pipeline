"""Wave C: detector contract + failure isolation + registry + sign coverage
(spec §4, §5, §11; Gap B)."""
from __future__ import annotations

from app.routing.config import RoutingConfig
from app.routing.detectors import get_detectors, register_detector
from app.routing.detectors.base import Detector, DetectorResult
from app.routing.inspectors import FastInspector, InspectorFeatures

from .routing_fixtures import certificate_pdf, image_only_pdf, text_pdf


class _RaisingDetector(Detector):
    name = "boom"
    version = "1.0.0"

    def can_evaluate(self, feats):  # noqa: ANN001
        return True

    def _evaluate(self, feats):  # noqa: ANN001
        raise RuntimeError("evaluation exploded")


class _NeverApplicable(Detector):
    name = "never"
    version = "1.0.0"

    def can_evaluate(self, feats):  # noqa: ANN001
        return False

    def _evaluate(self, feats):  # noqa: ANN001
        raise AssertionError("must not run when can_evaluate is False")


class _Extra(Detector):
    name = "extra"
    version = "1.0.0"

    def can_evaluate(self, feats):  # noqa: ANN001
        return True

    def _evaluate(self, feats):  # noqa: ANN001
        return DetectorResult(self.name, self.version, "ok")


def test_text_pdf_signals_are_covered_by_weights():
    """Gap B: every emitted Signal.name must exist in RoutingConfig.weights."""
    rc = RoutingConfig()
    pdf = text_pdf()
    feats = FastInspector().inspect(pdf)
    assert feats is not None
    for det in get_detectors():
        res = det.evaluate(feats)
        if res.status in ("failed", "not_applicable"):
            continue
        for s in res.signals:
            assert s.name in rc.weights, f"{det.name} emits unknown signal {s.name}"


def test_image_only_detects_scan_high_signal():
    feats = FastInspector().inspect(image_only_pdf())
    from app.routing.detectors.image_detector import ImageDetector

    res = ImageDetector().evaluate(feats)
    scan = next(s for s in res.signals if s.name == "metric_scanned_page_probability")
    assert scan.status == "ok"
    assert float(scan.value) > 0.5  # full-bleed raster / no text -> strong scan


def test_certificate_with_text_is_not_spuriously_scanned():
    """The coordinator's concern: a certificate with a decorative border/logo
    raster but real embedded text must NOT be flagged as scanned. The scanned
    probability is driven by the continuous image ratio but GUARDED by text."""
    from app.routing.detectors.image_detector import ImageDetector

    feats = FastInspector().inspect(certificate_pdf())
    res = ImageDetector().evaluate(feats)
    scan = next(s for s in res.signals if s.name == "metric_scanned_page_probability")
    assert float(scan.value) < 0.05     # text present => not scanned
    # image density still reflects the large decorative raster (auditable ratio)
    density = next(s for s in res.signals if s.name == "metric_image_density")
    assert float(density.value) > 0.5


def test_can_evaluate_false_is_not_applicable_not_negative():
    res = _NeverApplicable().evaluate(InspectorFeatures(page_count=1))
    assert res.status == "not_applicable"
    assert res.signals == []  # no positive/negative-leaning signals emitted


def test_raising_detector_is_isolated():
    res = _RaisingDetector().evaluate(InspectorFeatures(page_count=1))
    assert res.status == "failed"
    assert res.error
    # a failed detector must not manufacture a positive/valid negative signal
    assert all(s.status == "failed" for s in res.signals)


def test_register_detector_is_additive():
    before = len(get_detectors())
    register_detector(_Extra)
    now = get_detectors()
    assert len(now) == before + 1
    assert any(d.name == "extra" for d in now)