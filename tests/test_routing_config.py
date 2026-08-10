"""Wave A2: immutable config snapshot tests (spec §6, §17)."""
from __future__ import annotations

from app.routing.config import RoutingConfig


def test_bands_cover_0_100_contiguously():
    rc = RoutingConfig()
    lo0 = 0
    names = []
    for lo, hi, name in rc.bands:
        assert lo == lo0
        lo0 = hi + 1
        names.append(name)
    assert lo0 == 101  # band end covers 100 inclusive
    assert names == ["native", "enrichment", "docling"]


def test_all_weights_non_negative_and_cover_signals():
    rc = RoutingConfig()
    assert all(w >= 0 for w in rc.weights.values())
    # every complexity-driving signal we expect is present
    for k in ("metric_scanned_page_probability", "metric_ocr_required",
              "metric_low_text_ratio", "metric_multi_column_probability"):
        assert k in rc.weights
    assert len(rc.weights) >= 10


def test_snapshot_stable_and_json_safe():
    rc = RoutingConfig()
    a, b = rc.snapshot(), rc.snapshot()
    assert a == b
    assert isinstance(a["weights"], dict)
    assert isinstance(a["bands"], list)


def test_low_conf_thresholds_in_unit_range():
    rc = RoutingConfig()
    for band in ("native", "enrichment", "docling"):
        t = rc.low_conf_threshold(band)
        assert 0.0 <= t <= 1.0