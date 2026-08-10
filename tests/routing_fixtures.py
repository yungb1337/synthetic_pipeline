"""Shared PDF builders for the routing test files (spec §17).

Not a test module (no `test_` prefix) so pytest does not collect it.
Mirrors the inline `_pdf_bytes()` helpers used elsewhere in `tests/`.
"""
from __future__ import annotations

import fitz


def text_pdf(pages: int = 1, lines: int = 3) -> bytes:
    """A plain text PDF — an easy, single-column, native-tier document."""
    doc = fitz.open()
    for _ in range(pages):
        p = doc.new_page(width=595, height=842)
        p.insert_text((72, 100), "Clinical Report", fontsize=20)
        p.insert_text((72, 140), "The patient has stable diabetes on metformin.", fontsize=11)
        if lines > 2:
            p.insert_text((72, 160), "Monitoring of renal function is advised annually.", fontsize=11)
    return doc.tobytes()


def image_only_pdf(pages: int = 1) -> bytes:
    """Pages holding ONLY a full-bleed raster (no text) — scanned-like."""
    doc = fitz.open()
    for _ in range(pages):
        p = doc.new_page(width=595, height=842)
        pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 560, 800))
        pix.clear_with(210)
        p.insert_image(fitz.Rect(10, 10, 585, 832), pixmap=pix)
    return doc.tobytes()


def mixed_pdf(text_pages: int = 1, image_pages: int = 1) -> bytes:
    """Some text pages + some scanned/image-only pages — enrichment-tier."""
    doc = fitz.open()
    for _ in range(text_pages):
        p = doc.new_page(width=595, height=842)
        p.insert_text((72, 100), "Intro", fontsize=16)
        p.insert_text((72, 140), "Some body text follows here.", fontsize=11)
    for _ in range(image_pages):
        p = doc.new_page(width=595, height=842)
        pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 560, 800))
        pix.clear_with(120)
        p.insert_image(fitz.Rect(10, 10, 585, 832), pixmap=pix)
    return doc.tobytes()


def many_columns_pdf() -> bytes:
    """A two-column-ish layout to nudge the multi-column heuristic."""
    doc = fitz.open()
    p = doc.new_page(width=595, height=842)
    # left column
    p.insert_text((40, 100), "Left column heading", fontsize=12)
    p.insert_text((40, 125), "Left body word", fontsize=10)
    p.insert_text((40, 145), "More left text", fontsize=10)
    # right column (well separated x)
    p.insert_text((400, 100), "Right column", fontsize=12)
    p.insert_text((400, 125), "Right body text", fontsize=10)
    p.insert_text((400, 145), "Right continued", fontsize=10)
    return doc.tobytes()


def certificate_pdf() -> bytes:
    """A bordered, logo-bearing but MOSTLY-TEXT certificate (the coordinator's
    spurious-scan concern): a decorative raster (border/logo) plus substantial
    embedded text must NOT be treated as a scanned page."""
    doc = fitz.open()
    p = doc.new_page(width=595, height=842)
    # a decorative border raster (covers most of the page, no text glyphs inside)
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 560, 800))
    pix.clear_with(245)
    p.insert_image(fitz.Rect(15, 15, 580, 827), pixmap=pix)   # large but decorative
    # real embedded text on top (the certificate body)
    p.insert_text((80, 120), "CERTIFICATE OF COMPLETION", fontsize=20)
    p.insert_text((80, 180), "This certifies that the course on clinical safety", fontsize=12)
    p.insert_text((80, 210), "has been successfully completed on this date.", fontsize=12)
    p.insert_text((80, 280), "Signed by the Medical Director", fontsize=12)
    return doc.tobytes()