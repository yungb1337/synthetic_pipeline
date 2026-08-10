"""Routing regression against the real test_cases corpus (spec §18).

These pin the calibrated expectations so future detector/weight changes cannot
silently alter routing behavior. The corpus is the user's `test_cases` folder;
each test rebuilds the PDF bytes, routes, and asserts the band.

Calibrated 2026-08-10 (absolute-sum scorer, scan cluster capped below Docling):
  scanned tickets / receipts / Report  -> ENRICHMENT (OCR)
  complex academic papers + electronics -> DOCLING   (layout/reading-order)
  simple text                          -> NATIVE     (covered by a synthetic doc)
"""
from __future__ import annotations

import pathlib

import pytest

from app.parser.detection import detect
from app.routing import Router

_CORPUS = pathlib.Path(r"C:/Users/Asus/Downloads/test_cases")
ROUTER = Router()

if not _CORPUS.is_dir():
    pytest.skip(f"routing corpus {_CORPUS} not present — skipping live calibration pins",
                allow_module_level=True)


def _route(filename: str) -> tuple[str, int]:
    data = (_CORPUS / filename).read_bytes()
    det = detect(data, filename=filename)
    dec = ROUTER.route(data, det)
    assert dec is not None, f"{filename}: router returned no decision"
    return dec.route, dec.complexity_score


@pytest.mark.parametrize("name", [
    "2503.14023v2.pdf", "2504.12322v2.pdf", "3548785.3548793.pdf",
    "PDF v3.pdf",
])
def test_complex_academic_papers_route_docling(name):
    route, cpx = _route(name)
    assert route == "docling", f"{name}: {route} (cpx={cpx}) expected docling"


def test_electronics_paper_limitation_routes_at_least_enrichment():
    """KNOWN-LIMITATION marker (ADR-011 / questions.md follow-up): the cheap
    PyMuPDF detectors under-report the layout complexity of MDPI-style academic
    papers (reading_order=0.24, multi_column=0.21), so this genuinely-complex
    paper only reaches Enrichment, not the Docling band. Once the reading-order
    / multi-column / layout detectors are refined, this should route docling.
    Pinned at Enrichment so a silent detector change can't regress it to native."""
    route, cpx = _route("electronics-13-03509.pdf")
    assert route in ("enrichment", "docling"), f"electronics: {route} (cpx={cpx})"
    assert cpx >= 31


@pytest.mark.parametrize("name", [
    "Nizammudin to Mathura.pdf",
    "Ticket Agra to Nizam.pdf",
    "Ticket Tundla To PRYJ.pdf",
    "receipt1.pdf",
    "receipt2.pdf",
    "Report.pdf",
])
def test_scanned_docs_route_enrichment_ocr(name):
    """Scanned tickets/receipts need OCR — Enrichment, NOT the full Docling
    pipeline (spec §5: a scanned doc may not need Docling)."""
    route, cpx = _route(name)
    assert route == "enrichment", f"{name}: {route} (cpx={cpx}) expected enrichment"


def test_image_cert_routes_enrichment():
    route, cpx = _route("AWS Certified AI Practitioner certificate.pdf")
    assert route == "enrichment", f"cert: {route} (cpx={cpx})"


def test_simple_text_pdf_routes_native(tmp_path):
    """A clean single-column text PDF must stay on the cheap native path."""
    import fitz

    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    for i in range(30):
        page.insert_text((72, 100 + i * 22),
                         f"Plain paragraph {i} with ordinary body text about nothing in particular.")
    data = doc.tobytes()

    det = detect(data, filename="simple.pdf")
    dec = ROUTER.route(data, det)
    assert dec is not None
    assert dec.route == "native", f"simple text: {dec.route} (cpx={dec.complexity_score}) expected native"
    assert dec.complexity_score <= 30


def test_docling_band_is_reachable_on_corpus():
    """The 61-100 Docling band must not be dead config (ADR-011 challenge)."""
    routes = [_route(n)[0] for n in
              ("2503.14023v2.pdf", "Nizammudin to Mathura.pdf", "receipt1.pdf",
               "AWS Certified AI Practitioner certificate.pdf")]
    assert "docling" in routes
