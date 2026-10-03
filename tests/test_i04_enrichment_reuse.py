"""I-04 regression tests — enrichment engine reuse (kills the O(N²)).

The old `EnrichmentEngine.process` built a fresh `NativePdfEngine` per page,
so every page paid a full document re-open + document-wide median re-scan.
This pins the new contract: one inner engine, reused across pages.
"""

from __future__ import annotations

import fitz
import pytest

from app.parser.config import ParserConfig
from app.parser.engines.base import PageWorkItem
from app.parser.engines.enrichment import EnrichmentEngine
from app.parser.engines.native_pdf import NativePdfEngine


def _make_pdf(path, pages: int = 4) -> str:
    doc = fitz.open()
    for i in range(pages):
        p = doc.new_page(width=595, height=842)
        p.insert_text((72, 100), f"Enrich page {i} text", fontsize=11)
    doc.save(path)
    doc.close()
    return str(path)


def _item(src: str, idx: int) -> PageWorkItem:
    return PageWorkItem(
        doc_id="d-e",
        source_hash="sha",
        src_path=src,
        page_index=idx,
        route="enrichment",
        ocr_enabled=False,
    )


def test_i04_inner_engine_is_created_once_and_reused():
    eng = EnrichmentEngine(ParserConfig())
    assert isinstance(eng._native, NativePdfEngine)
    inner_before = eng._native
    # Two process() calls must reuse the SAME inner engine instance.
    # (fitz document missing -> contained FAILED is fine; identity is the point.)
    eng.process(_item("/nonexistent/x.pdf", 0))
    eng.process(_item("/nonexistent/x.pdf", 1))
    assert eng._native is inner_before
    eng.close()


def test_i04_single_open_and_median_per_document(tmp_path):
    """4 pages of one doc -> exactly 1 fitz.open + 1 median scan (was 4 + 4)."""
    src = _make_pdf(tmp_path / "e.pdf", pages=4)

    opens = {"n": 0}
    real_open = fitz.open

    def counting_open(*a, **k):
        opens["n"] += 1
        return real_open(*a, **k)

    eng = EnrichmentEngine(ParserConfig())
    monkeyfitz = pytest.MonkeyPatch()
    monkeyfitz.setattr(fitz, "open", counting_open)
    try:
        results = [eng.process(_item(src, i)) for i in range(4)]
    finally:
        monkeyfitz.undo()

    assert all(r.status.value == "ok" for r in results), [
        (r.status, r.errors) for r in results
    ]
    # I-04 contract: one document open for all 4 pages (the OCR render path is
    # skipped here: pages have text). Before the fix this was 4 opens.
    assert opens["n"] == 1, f"expected 1 fitz.open, got {opens['n']}"
    # And the median was computed once: the cached entry exists for the doc.
    assert len(eng._native._doc_cache) == 1
    eng.close()


def test_i04_scheduler_close_propagates(monkeypatch, tmp_path):
    """Scheduler.close() closes the hoisted enrichment engine (fitz handles)."""
    from app.parser.scheduler import Scheduler

    closed = {"flag": False}

    def fake_close(self):
        closed["flag"] = True

    monkeypatch.setattr(NativePdfEngine, "close", fake_close)
    sched = Scheduler(ParserConfig())
    sched.close()
    assert closed["flag"] is True
