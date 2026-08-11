"""Tests for the parser module (Extraction -> DOM)."""
from __future__ import annotations

import json

import pytest


def _env(tmp_path):
    """Returns (Extractor, FilesystemStore) sharing a temp store."""
    from app.parser.config import default_config
    from app.parser.events import EventPublisher
    from app.parser.extraction import Extractor
    from app.parser.storage import FilesystemStore

    store = FilesystemStore(str(tmp_path / "store"))
    pub = EventPublisher(sink=lambda name, payload: None)
    return Extractor(default_config(), store, events=pub), store


def _extractor(tmp_path):
    return _env(tmp_path)[0]


# ---------------------------------------------------------------------------
def test_detection_core_cases():
    from app.parser.detection import detect

    assert detect(b"%PDF-1.7 x").slug == "pdf"
    assert detect(b'{\n"k": 1}\n').slug == "json"
    assert detect(b"a,b\n1,2\n").slug == "csv"
    assert detect(b"a\tb\n1\t2\n").slug == "tsv"
    assert detect(b"# Heading\nbody").slug == "markdown"
    assert detect(b"<?xml version='1.0'?><a/>").slug == "xml"
    assert detect(b"\x89PNG\r\n\x1a\n....").slug == "png"


def test_csv_to_table(tmp_path):
    data = b"patient_id,age,diagnosis\nP0001,62,Diabetes\nP0002,31,Asthma\n"
    out = _extractor(tmp_path).extract(data, "records.csv")
    assert out.ok
    assert out.detected.slug == "csv"
    assert out.document.num_tables() == 1
    tbl = out.document.pages[0].tables[0]
    assert tbl.header == ["patient_id", "age", "diagnosis"]
    assert len(tbl.rows) == 2
    assert tbl.rows[1].cells[0].text == "P0002"


def test_markdown_headings(tmp_path):
    data = b"# Introduction\n\nSome background text here.\n\n## Methods\n\nWe did things.\n\n- item one\n- item two\n"
    out = _extractor(tmp_path).extract(data, "notes.md")
    assert out.ok
    kinds = [b.kind for b in out.document.pages[0].blocks]
    assert "heading" in kinds
    assert "list_item" in kinds
    # reading order must be non-empty and a strict chain
    assert len(out.document.reading_order) == len(out.document.pages[0].blocks)


def test_pdf_extraction(tmp_path):
    raw = _make_bytes()
    out = _extractor(tmp_path).extract(raw, "report.pdf")
    assert out.ok
    assert out.detected.slug == "pdf"
    doc = out.document
    assert len(doc.pages) == 1
    assert doc.num_blocks() > 0
    text = " ".join(b.text for p in doc.pages for b in p.blocks)
    assert "Clinical" in text or "diabetes" in text.lower()
    # reading order present and valid
    assert len(doc.reading_order) == doc.num_blocks()
    # DOM serializes
    json.loads(doc.model_dump_json())


def test_idempotent_parse(tmp_path):
    raw = _make_bytes()
    a = _extractor(tmp_path).extract(raw, "r.pdf")
    b = _extractor(tmp_path).extract(raw, "r2.pdf")
    assert a.ok and b.ok
    # content-addressed doc id and deterministic output
    assert a.document_id == b.document_id
    assert a.document.num_blocks() == b.document.num_blocks()
    assert a.document.reading_order == b.document.reading_order
    assert a.document.source_hash == b.document.source_hash


def test_unsupported_flagged(tmp_path):
    raw = b"{\\rtf1\\ansi hello}"
    out = _extractor(tmp_path).extract(raw, "note.rtf")
    # rtf detection -> unsupported loader
    assert not out.ok


def test_store_writes_dom_and_raw(tmp_path):
    ex = _extractor(tmp_path)
    data = b"# Head\nbody text\n"
    out = ex.extract(data, "x.md")
    assert out.ok
    raw_key = out.report["raw_key"]
    dom_key = out.report["dom_key"]
    assert ex.store.get(raw_key) == data
    assert ex.store.get(dom_key) is not None


def test_txt_plaintext_loader(tmp_path):
    """Regression: `plaintext`/`txt` dispatch used to hit a missing `_plain`."""
    data = b"Patient A: stable.\nPatient B: improving.\n"
    out = _extractor(tmp_path).extract(data, "notes.txt")
    assert out.ok
    assert out.detected.slug == "plaintext"
    assert out.document.num_blocks() == 2
    text = " ".join(b.text for p in out.document.pages for b in p.blocks)
    assert "Patient A" in text and "Patient B" in text


def test_image_doc_reparse_deterministic(tmp_path):
    """Same bytes -> identical DOM bytes AND identical storage keys.

    ADR-011: the auto route now records `provenance.routing`, which carries a
    measured `inspection_time_ms` — a wall-clock measurement, NOT part of the
    routing decision. We normalize it before the byte-equality assertion so the
    DOM stays byte-deterministic while the routing measurement is preserved.
    """
    raw = _make_pdf_with_image()
    a = _extractor(tmp_path).extract(raw, "img1.pdf")
    b = _extractor(tmp_path).extract(raw, "img2.pdf")
    assert a.ok and b.ok
    assert a.document_id == b.document_id
    for doc in (a.document, b.document):
        if doc.provenance and doc.provenance.routing:
            doc.provenance.routing.inspection_time_ms = 0.0
    assert a.document.model_dump_json() == b.document.model_dump_json()
    assert a.report["dom_key"] == b.report["dom_key"]
    assert a.report["raw_key"] == b.report["raw_key"]
    keys_a = [i.storage_ref for p in a.document.pages for i in p.images]
    keys_b = [i.storage_ref for p in b.document.pages for i in p.images]
    assert keys_a and keys_a == keys_b
    # versioned layout: dom/<doc_id>/dom-v{version}.docJSON
    assert "/dom-v" in a.report["dom_key"]


def _make_bytes() -> bytes:
    return _make_pdf_bytes()


def _make_pdf_bytes():
    import fitz
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 100), "Clinical Report", fontsize=20)
    page.insert_text((72, 130), "The patient has stable diabetes on metformin.", fontsize=11)
    page.insert_text((72, 150), "Monitoring of renal function is advised.", fontsize=11)
    return doc.tobytes()


def _make_pdf_with_image():
    import fitz
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 100), "Image report", fontsize=14)
    # a small solid-gradient RGB pixmap so the PDF carries an embedded image
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 64, 64))
    for y in range(64):
        for x in range(64):
            pix.set_pixel(x, y, (x * 3 % 255, y * 3 % 255, 128))
    page.insert_image(fitz.Rect(72, 150, 172, 250), pixmap=pix)
    return doc.tobytes()


# ---------------------------------------------------------------------------
def test_ocr_pil_image_converted_to_ndarray(monkeypatch):
    """A PIL image passed to ocr_image must be converted to a numpy array, and
    the rapidocr v6 `RapidOCROutput` (.txts/.boxes/.scores) parsed into
    (text, bbox, conf) — regression for the 2026-08-05 PIL-rejection bug and the
    2026-08-11 v6 engine unification."""
    from app.parser import ocr

    import numpy as np

    seen = {}

    class FakeOutput:
        def __init__(self):
            self.txts = ("hello",)
            self.boxes = np.array([[[0, 0], [10, 0], [10, 5], [0, 5]]], dtype=float)
            self.scores = (0.91,)

    def fake_engine(image):
        seen["type"] = type(image).__module__ + "." + type(image).__name__
        return FakeOutput()

    monkeypatch.setattr(ocr, "engine_available", lambda: True)
    monkeypatch.setattr(ocr, "_engine", fake_engine)

    from PIL import Image

    img = Image.new("RGB", (20, 20), "white")
    result = ocr.ocr_image(img)
    assert seen["type"] == "numpy.ndarray", f"engine received {seen['type']}, expected ndarray"
    assert [t for t, _, _ in result] == ["hello"]
    assert result[0][1] == (0.0, 0.0, 10.0, 5.0)  # quad -> bbox
    assert round(result[0][2], 2) == 0.91