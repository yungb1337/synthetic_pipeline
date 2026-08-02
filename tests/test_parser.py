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