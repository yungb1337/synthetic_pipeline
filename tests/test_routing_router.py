"""Wave E: Router assembly + stats + determinism (spec §8, §12, §13)."""
from __future__ import annotations

from app.routing.config import RoutingConfig
from app.routing.detectors.base import Detector, DetectorResult
from app.routing.router import RoutingStats, Router
from app.routing.scoring import Score
from app.routing.schema import Signal

from .routing_fixtures import certificate_pdf, text_pdf


def _det(data, name):  # noqa: ANN001, ANN201
    from app.parser import detection

    return detection.detect(data, name)


class _StubScorer:
    def score(self, signals, features):  # noqa: ANN001
        return Score(complexity=40.0, confidence=0.9, reasons=["stub scorer"])


class _StubPolicy:
    def route(self, complexity, confidence):  # noqa: ANN001
        return "enrichment"


class _BoomDetector(Detector):
    name = "boom"
    version = "1.0.0"

    def can_evaluate(self, feats):  # noqa: ANN001
        return True

    def _evaluate(self, feats):  # noqa: ANN001
        raise RuntimeError("boom")


class _WeirdDetector(Detector):
    name = "weird"
    version = "1.0.0"

    def can_evaluate(self, feats):  # noqa: ANN001
        return True

    def _evaluate(self, feats):  # noqa: ANN001
        return DetectorResult(self.name, self.version, "ok", signals=[
            Signal(detector=self.name, version=self.version, name="not_a_real_signal",
                   value=1.0, status="ok"),
        ])


def test_router_composition_with_stub_scorer_policy():
    r = Router(RoutingConfig(), scorer=_StubScorer(), policy=_StubPolicy())
    data = text_pdf()
    d = r.route(data, _det(data, "a.pdf"))
    assert d is not None
    assert d.route == "enrichment"
    assert d.complexity_score == 40
    assert d.confidence == 0.9


def test_failing_detector_does_not_crash_router():
    r = Router(RoutingConfig(), detectors=[_BoomDetector()])
    data = text_pdf()
    d = r.route(data, _det(data, "b.pdf"))
    assert d is not None
    assert any(s.status == "failed" for s in d.signals)   # failure recorded
    assert d.route in ("native", "enrichment", "docling")  # still decided


def test_router_skips_non_pdf():
    from app.parser import detection

    r = Router(RoutingConfig())
    det = detection.Detected("csv", "text/csv", "sniff", 0.9, "")
    assert r.route(b"a,b\n1,2\n", det) is None


def test_gap_b_unknown_signal_warn_skip_count():
    stats = RoutingStats()
    r = Router(RoutingConfig(), detectors=[_WeirdDetector()], stats=stats)
    data = text_pdf()
    d = r.route(data, _det(data, "w.pdf"))
    assert d is not None
    # the unweighted signal is skipped entirely (never a negative, never a crash)
    assert all(s.name != "not_a_real_signal" for s in d.signals)
    assert stats.unknown_signal_count >= 1


def test_determinism_same_bytes_same_decision():
    r = Router(RoutingConfig())
    data = text_pdf()
    d1 = r.route(data, _det(data, "a.pdf"))
    d2 = r.route(data, _det(data, "b.pdf"))
    assert d1 is not None and d2 is not None
    d1.inspection_time_ms = 0.0  # wall-clock measurement excluded from equality
    d2.inspection_time_ms = 0.0
    assert d1.model_dump() == d2.model_dump()   # same signals/versions/route


def test_certificate_not_routed_to_docling():
    """Audit regression (coordinator): a decorative border/logo raster PLUS real
    embedded text must not be treated as a scan and shipped to Docling."""
    r = Router(RoutingConfig())
    data = certificate_pdf()
    d = r.route(data, _det(data, "cert.pdf"))
    assert d is not None
    assert d.route != "docling"
    assert d.route in ("native", "enrichment")


def test_stats_counters_updated():
    stats = RoutingStats()
    r = Router(RoutingConfig(), stats=stats)
    data = text_pdf()
    r.route(data, _det(data, "s.pdf"))
    assert stats.inspected >= 1
    assert sum(stats.by_band.values()) >= 1
    s = stats.stats()
    assert "by_band" in s and "confidence_buckets" in s


def test_gap_a_image_not_sent_to_docling():
    """Standalone images keep the existing native OCR loader (Gap A ruling):
    the router ignores non-PDF input, and under the default "auto" config an
    image is never routed to the Docling engine."""
    from app.parser.config import default_config
    from app.parser.events import EventPublisher
    from app.parser.extraction import Extractor
    from app.parser.storage import FilesystemStore

    import io as _io
    import tempfile
    from pathlib import Path

    from PIL import Image

    buf = _io.BytesIO()
    Image.new("RGB", (2, 2), "white").save(buf, format="PNG")
    png = buf.getvalue()

    with tempfile.TemporaryDirectory() as d:
        store = FilesystemStore(str(Path(d) / "store"))
        ex = Extractor(default_config(), store, events=EventPublisher(sink=lambda n, p: None))
        out = ex.extract(png, "img.png")
        assert out.ok
        assert out.detected.slug == "png"
        # native image OCR path; never routed to Docling
        assert out.document.provenance.docling_version is None
        assert out.report.get("route") is None