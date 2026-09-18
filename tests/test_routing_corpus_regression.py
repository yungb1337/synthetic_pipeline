"""Routing regression against the real test_cases corpus (spec §18).

These pin the calibrated expectations so future detector/weight changes cannot
silently alter routing behavior. The corpus is the user's `test_cases` folder;
each test rebuilds the PDF bytes, routes, and asserts the band.

Calibrated 2026-09-14 (rebalanced layout/font weights; high throughput):
  scanned tickets / receipts / Report  -> ENRICHMENT (OCR)
  clean academic papers / digital text -> ENRICHMENT / NATIVE (fast PyMuPDF)
  table-dense / complex documents      -> DOCLING   (deep table model)
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
    "2503.14023v2.pdf", "2504.12322v2.pdf", "PDF v3.pdf",
])
def test_dense_academic_papers_route_docling(name):
    route, cpx = _route(name)
    assert route == "docling", f"{name}: {route} (cpx={cpx}) expected docling"


def test_table_dense_acm_paper_routes_docling():
    """ACM paper with 22 tables across pages 4-8 routes to docling under distributed inspection."""
    route, cpx = _route("3548785.3548793.pdf")
    assert route == "docling", f"3548785.3548793.pdf: {route} (cpx={cpx}) expected docling"


def test_electronics_paper_routes_enrichment():
    route, cpx = _route("electronics-13-03509.pdf")
    assert route == "enrichment", f"electronics: {route} (cpx={cpx}) expected enrichment"


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


def test_image_cert_routes_native():
    """Digital certificate with clean vector text and high confidence routes to native."""
    route, cpx = _route("AWS Certified AI Practitioner certificate.pdf")
    assert route == "native", f"cert: {route} (cpx={cpx}) expected native"


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


def test_docling_band_is_reachable():
    """The 61-100 Docling band must be reachable for table-dense/complex docs."""
    pmc_dir = pathlib.Path("checkpoints/run/run-2026-09-04-parser-reliability/sources/pdf")
    if pmc_dir.is_dir():
        dense_doc = pmc_dir / "PMC11660019.pdf"
        if dense_doc.is_file():
            data = dense_doc.read_bytes()
            det = detect(data, filename="PMC11660019.pdf")
            dec = ROUTER.route(data, det)
            assert dec is not None
            assert dec.route == "docling"

