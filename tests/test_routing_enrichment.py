"""Wave H: enrichment OCR post-pass on native recovery (ADR-012, spec §7)."""
from __future__ import annotations

from app.parser import detection
from app.parser.config import ParserConfig
from app.parser.loaders.enrichment import enrich_scanned_pages
from app.parser.loaders.loaders import Loaders

from .routing_fixtures import mixed_pdf, text_pdf

_FAKE_OCR = "FAKE OCR LINE from the scanned page"


def _native_rec(pdf: bytes, name: str = "m.pdf"):
    loader = Loaders(ParserConfig(layout_backend="native"))
    det = detection.detect(pdf, name)
    return loader._pdf(pdf, det), det


def _fake_ocr(png):  # noqa: ANN001
    assert png[:8] == b"\x89PNG\r\n\x1a\n"          # a rendered PNG reached OCR
    return [(_FAKE_OCR, (10.0, 10.0, 50.0, 20.0), 0.94)]


def test_enrichment_ocrs_empty_pages_only():
    pdf = mixed_pdf(text_pages=1, image_pages=1)
    rec, _ = _native_rec(pdf)
    assert rec.page_count == 2
    # page 1 (image-only) has no text blocks
    rec2 = enrich_scanned_pages(rec, ParserConfig(ocr_enabled=True), data=pdf, ocr_fn=_fake_ocr)
    ocr = [b for b in rec2.blocks if b.source == "ocr"]
    assert ocr
    assert ocr[0].page == 1
    assert ocr[0].text == _FAKE_OCR
    assert ocr[0].ocr_engine is not None  # provenance/observability from block-level


def test_enrichment_skips_fully_text_pdf():
    pdf = text_pdf(pages=1)
    rec, _ = _native_rec(pdf)
    before = list(rec.blocks)
    rec2 = enrich_scanned_pages(rec, ParserConfig(ocr_enabled=True), data=pdf, ocr_fn=_fake_ocr)
    assert rec2.blocks == before            # no empty page -> no OCR, no double-read


def test_enrichment_deterministic():
    pdf = mixed_pdf(text_pages=1, image_pages=2)
    cfg = ParserConfig(ocr_enabled=True)
    r1, _ = _native_rec(pdf)
    r2, _ = _native_rec(pdf)
    s1 = enrich_scanned_pages(r1, cfg, data=pdf, ocr_fn=_fake_ocr)
    s2 = enrich_scanned_pages(r2, cfg, data=pdf, ocr_fn=_fake_ocr)
    assert [(b.page, b.text, b.source) for b in s1.blocks] == \
           [(b.page, b.text, b.source) for b in s2.blocks]


def test_enrichment_survives_page_failure():
    """A page that fails to render/OCR is skipped; never crashes the doc (§11)."""
    pdf = mixed_pdf(text_pages=1, image_pages=2)

    def failing(png):  # noqa: ANN001
        raise RuntimeError("ocr down")

    rec, _ = _native_rec(pdf)
    # first empty page fails, but the pass still completes for others
    rec2 = enrich_scanned_pages(rec, ParserConfig(ocr_enabled=True), data=pdf,
                                ocr_fn=failing)
    assert rec2 is not None            # never crashes
    # OCR that failed -> no ocr blocks added (recorded, continues)
    assert all(b.source == "text" for b in rec2.blocks)