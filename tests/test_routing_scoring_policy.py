"""Wave D: WeightedHeuristicScorer + RoutingPolicy (spec §6, §14)."""
from __future__ import annotations

from app.routing.config import RoutingConfig
from app.routing.inspectors import InspectorFeatures
from app.routing.policy import RoutingPolicy
from app.routing.scoring import WeightedHeuristicScorer
from app.routing.schema import Signal

_CLEAN = {
    "metric_scanned_page_probability": 0.0,
    "metric_low_char_density": 0.0,
    "metric_low_text_ratio": 0.0,
    "metric_multi_column_probability": 0.0,
    "metric_reading_order_ambiguity": 0.0,
    "metric_table_probability": 0.0,
    "metric_form_probability": 0.0,
    "metric_image_density": 0.0,
    "metric_font_diversity": 0.0,
    "metric_ocr_required": 0.0,
    # newly-weighted layout signals (calibration 2026-08-10) — measured, not complex
    "metric_layout_complexity": 0.0,
    "metric_block_fragmentation": 0.0,
}


def _sigs(mapping: dict) -> list[Signal]:
    return [Signal(detector="t", version="1.0.0", name=k, value=v, status="ok")
            for k, v in mapping.items()]


def _scorer():
    return WeightedHeuristicScorer(RoutingConfig())


def test_clean_text_low_complexity_high_confidence():
    sc = _scorer().score(_sigs(_CLEAN), InspectorFeatures(page_count=1))
    assert sc.complexity < 30          # below the native/enrichment boundary
    assert sc.confidence > 0.5         # safely above the native fallback
    assert sc.confidence >= 0.8        # well-measured


def test_scan_heavy_doc_routes_enrichment_tier():
    heavy = dict(_CLEAN)
    heavy.update({
        "metric_scanned_page_probability": 1.0,
        "metric_low_text_ratio": 1.0,
        "metric_low_char_density": 1.0,
        "metric_ocr_required": 0.9,
        "metric_image_density": 1.0,
    })
    # a scan-heavy (but not table+multi-column) doc is a localized complexity =>
    # Enrichment (OCR) — cheap and sufficient (spec §7), NOT a premature Docling.
    sc = _scorer().score(_sigs(heavy), InspectorFeatures(page_count=1))
    assert 31 <= sc.complexity < 61


def test_multi_concern_complex_doc_reaches_docling():
    """The top tier stays reachable: a doc that is scanned AND multi-column AND
    table-bearing (whole-document complexity) must route to Docling."""
    complex_sig = dict(_CLEAN)
    complex_sig.update({
        "metric_scanned_page_probability": 1.0,
        "metric_low_text_ratio": 1.0,
        "metric_low_char_density": 1.0,
        "metric_ocr_required": 0.9,
        "metric_image_density": 1.0,
        "metric_multi_column_probability": 1.0,
        "metric_reading_order_ambiguity": 0.8,
        "metric_table_probability": 0.4,
    })
    sc = _scorer().score(_sigs(complex_sig), InspectorFeatures(page_count=1))
    assert sc.complexity >= 61


def test_missing_signal_drops_confidence_but_is_not_a_negative():
    full = _scorer().score(_sigs(_CLEAN), InspectorFeatures(page_count=1)).confidence
    partial = {k: v for k, v in _CLEAN.items() if k != "metric_ocr_required"}
    low = _scorer().score(_sigs(partial), InspectorFeatures(page_count=1)).confidence
    assert low < full                  # more missing => less certain
    assert low > 0.0                   # never crushes confidence to a false-negative


def test_band_boundaries():
    rc = RoutingConfig()
    pol = RoutingPolicy(rc)
    assert pol.route(30, 0.99) == "native"
    assert pol.route(31, 0.99) == "enrichment"
    assert pol.route(60, 0.99) == "enrichment"
    assert pol.route(61, 0.99) == "docling"
    assert pol.route(100, 0.99) == "docling"


def test_escalate_on_low_confidence_never_downgrade():
    rc = RoutingConfig()
    pol = RoutingPolicy(rc)
    # low confidence pushes one tier toward the more capable pipeline
    assert pol.route(20, 0.1) == "enrichment"    # native -> enrichment
    assert pol.route(40, 0.1) == "docling"       # enrichment -> docling
    assert pol.route(80, 0.1) == "docling"       # docling bounded (no further)
    # high confidence stays put
    assert pol.route(20, 0.9) == "native"
    assert pol.route(40, 0.9) == "enrichment"
    assert pol.route(80, 0.9) == "docling"