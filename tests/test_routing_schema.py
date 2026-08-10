"""Wave A1: leaf schema tests (spec §4, §11)."""
from __future__ import annotations

import pytest

from app.routing.schema import RoutingDecision, Signal


def test_missing_signal_roundtrips():
    s = Signal(detector="text", version="1.0.0", name="metric_page_char_density_none",
               value=None, status="missing")
    d = s.model_dump_json()
    s2 = Signal.model_validate_json(d)
    assert s2.value is None
    assert s2.status == "missing"


def test_failed_missing_never_positive_evidence():
    failed = Signal(detector="table", version="1.0.0", name="metric_table_present",
                    value=None, status="failed")
    missing = Signal(detector="ocr", version="1.0.0", name="metric_ocr_confidence",
                     value=None, status="missing")
    # a failure/missing is a placeholder, never a usable positive evidence
    assert failed.is_evidence() is False
    assert missing.is_evidence() is False
    payload = failed.model_dump()
    assert payload["value"] is None  # serializes as null, not 0/False


def test_signal_value_is_bounded_to_scalars():
    with pytest.raises(Exception):
        Signal(detector="d", version="1", name="m", value=[1, 2])


def test_routing_decision_validates():
    dec = RoutingDecision(
        route="native", complexity_score=5, confidence=0.9,
        reasons=["clean text"], signals=[],
        router_version="router-v0.1.0", policy_version="policy-v0.1.0",
        scoring_version="scoring-v0.1.0", inspection_time_ms=1.2,
        bands={"native": (0, 30)},
    )
    assert dec.route == "native"
    assert dec.bands["native"] == (0, 30)
    # an empty signals list (e.g. a fully failed detector set) is valid
    assert dec.signals == []
    dec.model_dump_json()


def test_routing_decision_full_example_with_signal():
    sig = Signal(detector="text", version="1.0.0", name="metric_low_text_ratio",
                 value=1.0, confidence=0.9, status="ok")
    dec = RoutingDecision(
        route="docling", complexity_score=80, confidence=0.7, reasons=["scan"],
        signals=[sig], router_version="r", policy_version="p", scoring_version="s",
        inspection_time_ms=5.0, bands={"docling": (61, 100)},
    ).model_dump()
    assert dec["signals"][0]["name"] == "metric_low_text_ratio"