"""Wave B1: decision-free inspector tests (spec §3, §4)."""
from __future__ import annotations

from dataclasses import asdict

from app.routing.inspectors import FastInspector

from .routing_fixtures import certificate_pdf, image_only_pdf, text_pdf


def test_text_only_pdf_features():
    b = text_pdf(pages=2)
    f = FastInspector().inspect(b)
    assert f is not None
    assert f.page_count == 2
    assert f.text_ratio and f.text_ratio > 0
    assert f.full_image_pages == []
    assert f.pages_char_count.get(0, 0) > 0


def test_full_bleed_image_page_flagged_with_zero_chars():
    b = image_only_pdf(pages=1)
    f = FastInspector().inspect(b)
    assert f is not None
    assert f.pages_char_count.get(0, 0) == 0        # no embedded text on that page
    assert 0 in f.full_image_pages                  # flagged as scanned


def test_non_pdf_returns_none():
    bad = b"%PDF-1.7 this is not a real pdf document ----------"
    assert FastInspector().inspect(bad) is None


def test_empty_bytes_returns_none():
    assert FastInspector().inspect(b"") is None


def test_deterministic_features():
    b = text_pdf(pages=3)
    fi = FastInspector()
    first = asdict(fi.inspect(b))
    second = asdict(fi.inspect(b))
    assert first == second  # no hidden RNG / ordering


def test_pages_image_ratio_is_auditable_evidence():
    """The inspector records a continuous, exact per-page image-ownership ratio
    (0..1) — the evidence the scanned-probability heuristic is audited against."""
    f = FastInspector().inspect(image_only_pdf())
    assert f.pages_image_ratio
    r = f.pages_image_ratio[0]
    assert 0.0 <= r <= 1.0
    assert r > 0.9                         # a full-bleed raster ~ 0.94
    # a plain text page has effectively no image ownership
    f2 = FastInspector().inspect(text_pdf())
    assert f2.pages_image_ratio.get(0, 0.0) == 0.0


def test_certificate_like_page_is_an_image_page_not_scan_evidence():
    """A bordered/logo+text certificate still gets a large image ratio but the
    CONTINUOUS ratio is recorded, so the detector can gate on text (see the
    detector tests)."""
    f = FastInspector().inspect(certificate_pdf())
    assert f.pages_image_ratio[0] > 0.7      # decorative raster covers most of page
    assert f.pages_char_count.get(0, 0) > 0  # ... yet there IS embedded text