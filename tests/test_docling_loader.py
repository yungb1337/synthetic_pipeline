"""Tests for the Docling layout/table backend (ADR-007).

The Docling path is gated behind `ParserConfig.layout_backend == "docling"`.
Docling is an optional dependency, so tests that exercise the real backend skip
when it is not installed (matching the OCR lazy-engine pattern); the
degradation-to-native test runs on every environment.
"""
from __future__ import annotations

import pytest


def _extractor(tmp_path, config):
    from app.parser.events import EventPublisher
    from app.parser.extraction import Extractor
    from app.parser.storage import FilesystemStore

    store = FilesystemStore(str(tmp_path / "store"))
    pub = EventPublisher(sink=lambda name, payload: None)
    return Extractor(config, store, events=pub), store


def _docling_config():
    from app.parser.config import ParserConfig

    return ParserConfig(layout_backend="docling")


def _pdf_bytes():
    import fitz

    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 100), "Clinical Report", fontsize=20)
    page.insert_text((72, 130), "The patient has stable diabetes on metformin.", fontsize=11)
    # a tiny bordered table so table-structure has something to find (API-version
    # dependent; skip if the installed PyMuPDF has no table-insertion support)
    try:
        tab = page.new_table()
        tab.add_row(["patient_id", "age"])
        tab.add_row(["P0001", "62"])
        tab.add_row(["P0002", "31"])
        page.insert_table((72, 200), tab, border=0.5)
    except Exception:
        pass
    return doc.tobytes()


def test_docling_mapping_logic(tmp_path):
    """White-box: DoclingDocument item → RecoveredDocument mapping works without
    Docling installed (fake items exercise _map_item/_map_table/_map_image)."""
    from PIL import Image

    from app.parser.loaders import docling_loader
    from app.parser.parts import RecoveredDocument

    class FakeLabel:
        def __init__(self, v):
            self.value = v

    class FakeBBox:
        def __init__(self, l, t, r, b):
            self.l, self.t, self.r, self.b = l, t, r, b

    class FakeProv:
        def __init__(self, page, bbox):
            self.page_no = page
            self.bbox = FakeBBox(*bbox)

    class FakeItem:
        def __init__(self, label, text="", page=0, bbox=(0, 0, 10, 10)):
            self.label = FakeLabel(label)
            self.text = text
            self.prov = [FakeProv(page, bbox)]

    class FakeCell:
        def __init__(self, r, c, text):
            self.start_row_offset_idx = r
            self.start_col_offset_idx = c
            self.text = text

    class FakeTable:
        table_cells = [FakeCell(0, 0, "h1"), FakeCell(0, 1, "h2"),
                       FakeCell(1, 0, "a"), FakeCell(1, 1, "b")]

    class FakeTableItem:
        label = FakeLabel("table")
        prov = [FakeProv(0, (0, 0, 50, 50))]
        table = FakeTable()

        def export_to_dataframe(self):
            raise AttributeError("force the cell-grid fallback")

    class FakePictureItem:
        label = FakeLabel("picture")
        prov = [FakeProv(1, (10, 10, 20, 20))]
        image = Image.new("RGB", (4, 4))

    rec = RecoveredDocument()
    for item in [FakeItem("section_header", "Intro", 0, (0, 0, 100, 20)),
                 FakeItem("text", "Body text here", 0, (0, 30, 200, 50)),
                 FakeItem("list_item", "- item", 0, (0, 60, 50, 80)),
                 FakeItem("code", "x=1", 0, (0, 90, 50, 100)),
                 FakeTableItem(),
                 FakePictureItem()]:
        docling_loader._map_item(item, rec)

    kinds = [b.kind for b in rec.blocks]
    assert kinds == ["heading", "paragraph", "list_item", "code"]
    assert rec.tables and rec.tables[0].header == ["h1", "h2"]
    assert rec.tables[0].rows and rec.tables[0].rows[0] == ["a", "b"]
    assert rec.images and rec.images[0].mime == "image/png"
    assert rec.images[0].page == 1


def test_default_layout_backend_is_auto():
    """ADR-007 amendment: the default flips from "native" to "auto" so the
    intelligent router (ADR-011) picks the tier per document. Manual "native"/
    "docling" overrides remain valid (asserted elsewhere)."""
    from app.parser.config import default_config

    assert default_config().layout_backend == "auto"


def test_docling_missing_falls_back_to_native(tmp_path):
    """With layout_backend=docling but Docling absent, PDFs still parse via the
    cheap native path — deterministic degrade, never a crash."""
    from app.parser.loaders import docling_loader

    if docling_loader.engine_available():
        pytest.skip("Docling installed; the fallback path is not exercised here")

    ex, _ = _extractor(tmp_path, _docling_config())
    out = ex.extract(_pdf_bytes(), "report.pdf")
    assert out.ok
    assert out.detected.slug == "pdf"
    assert out.document.num_blocks() > 0
    assert out.document.provenance.docling_version is None


def test_docling_path_records_provenance_and_order(tmp_path):
    """When Docling is installed, the docling backend runs for layout_backend=docling:
    provenance records the engine, and reading order is authoritative (a strict chain)."""
    pytest.importorskip("docling")

    ex, _ = _extractor(tmp_path, _docling_config())
    raw = _pdf_bytes()  # generate once: PyMuPDF's /ID makes two calls differ
    out = ex.extract(raw, "complex.pdf")
    assert out.ok
    prov = out.document.provenance
    assert prov.docling_version  # docling package version recorded
    assert len(out.document.reading_order) == out.document.num_blocks()
    # source content-addressing still holds on the docling path
    out2 = ex.extract(raw, "complex2.pdf")
    assert out.document_id == out2.document_id
