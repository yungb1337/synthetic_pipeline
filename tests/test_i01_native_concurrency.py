"""I-01 regression tests — per-document locking in NativePdfEngine.

Verifies the concurrency contract introduced by the I-01 fix:

1. Pages of DIFFERENT documents extract CONCURRENTLY (the old process-global
   lock serialized the whole native band — this test fails on the old code).
2. Pages of the SAME document still serialize (one fitz.Document handle is
   not thread-safe across pages).
3. Extraction results are identical to the single-threaded expectation
   (no output regression from the locking change).
4. close() clears the doc cache and the engine keeps working afterwards.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import fitz
import pytest

from app.parser.config import ParserConfig
from app.parser.engines.native_pdf import NativePdfEngine, _native_page_from_doc


def _make_pdf(path, pages: int = 3, tag: str = "A") -> str:
    doc = fitz.open()
    for i in range(pages):
        p = doc.new_page(width=595, height=842)
        p.insert_text((72, 100), f"Doc {tag} page {i} text", fontsize=11)
    doc.save(path)
    doc.close()
    return str(path)


class _CriticalSectionProbe:
    """Tracks concurrent holders of the per-page extraction critical section.

    Wraps `_native_page_from_doc` (called UNDER the document lock inside
    `extract_page`) and sleeps briefly so overlapping executions are
    deterministic: with a 150 ms hold and immediate submission, pages that
    CAN run concurrently WILL overlap; pages that serialize CANNOT.
    """

    def __init__(self, hold_s: float = 0.15):
        self.hold_s = hold_s
        self._count = 0
        self._guard = threading.Lock()
        self.active: list[str] = []  # src_paths currently inside
        self.max_concurrent = 0
        self.same_path_overlap = 0  # violations: same doc twice at once

    def wrap(self, src_path, page_index, config, body_med=None):
        with self._guard:
            self._count += 1
            same = self.active.count(src_path)
            if same:
                self.same_path_overlap += same
            self.active.append(src_path)
            self.max_concurrent = max(self.max_concurrent, len(self.active))
        try:
            import time

            time.sleep(self.hold_s)
            return _native_page_from_doc(
                src_path, page_index, config, body_med=body_med
            )
        finally:
            with self._guard:
                self.active.remove(src_path)
                self._count -= 1


@pytest.fixture()
def probe(monkeypatch):
    p = _CriticalSectionProbe()
    monkeypatch.setattr("app.parser.engines.native_pdf._native_page_from_doc", p.wrap)
    return p


def test_i01_different_documents_extract_concurrently(probe, tmp_path):
    """3 documents submitted together must overlap inside the critical section.

    On the pre-I-01 code (one process-global RLock) max_concurrent stays 1 and
    this test fails — that is the regression detector for the old behavior.
    """
    paths = [
        _make_pdf(tmp_path / f"doc{tag}.pdf", pages=1, tag=tag)
        for tag in ("A", "B", "C")
    ]
    engine = NativePdfEngine(ParserConfig())
    with ThreadPoolExecutor(max_workers=3) as pool:
        futs = [pool.submit(engine.extract_page, p, 0) for p in paths]
        results = [f.result(timeout=30) for f in futs]

    assert all(r.status.value == "ok" for r in results), [
        (r.status, r.errors) for r in results
    ]
    # Overlap happened across documents (deterministic: 150 ms hold, 3 threads)
    assert probe.max_concurrent >= 2, (
        "expected pages of different documents to run concurrently; "
        f"observed max_concurrent={probe.max_concurrent} (global-lock regression?)"
    )
    # Per-document serialization held: no two holders shared one document
    assert probe.same_path_overlap == 0
    engine.close()


def test_i01_same_document_pages_still_serialize(probe, tmp_path):
    """4 pages of ONE document must never overlap (fitz handle safety)."""
    path = _make_pdf(tmp_path / "one.pdf", pages=4, tag="S")
    engine = NativePdfEngine(ParserConfig())
    with ThreadPoolExecutor(max_workers=4) as pool:
        futs = [pool.submit(engine.extract_page, path, i) for i in range(4)]
        results = [f.result(timeout=30) for f in futs]

    assert all(r.status.value == "ok" for r in results), [
        (r.status, r.errors) for r in results
    ]
    assert probe.same_path_overlap == 0
    # Exactly one document handle was opened + cached for the whole doc
    assert len(engine._doc_cache) == 1
    engine.close()


def test_i01_results_identical_to_single_threaded(probe, tmp_path):
    """Output parity: concurrent extraction == sequential extraction."""
    path = _make_pdf(tmp_path / "parity.pdf", pages=3, tag="P")
    engine = NativePdfEngine(ParserConfig())

    sequential = [engine.extract_page(path, i) for i in range(3)]
    with ThreadPoolExecutor(max_workers=3) as pool:
        futs = [pool.submit(engine.extract_page, path, i) for i in range(3)]
        concurrent = [f.result(timeout=30) for f in futs]

    for s, c in zip(sequential, concurrent):
        assert s.status == c.status
        assert [b.text for b in s.blocks] == [b.text for b in c.blocks]
        assert [b.kind for b in s.blocks] == [b.kind for b in c.blocks]
        assert len(s.tables) == len(c.tables)
        assert len(s.images) == len(c.images)
    assert all("Doc P page" in b.text for r in concurrent for b in r.blocks)
    engine.close()


def test_i01_close_clears_cache_and_engine_survives(probe, tmp_path):
    path = _make_pdf(tmp_path / "cycle.pdf", pages=2, tag="Z")
    engine = NativePdfEngine(ParserConfig())
    r1 = engine.extract_page(path, 0)
    assert r1.status.value == "ok"
    assert len(engine._doc_cache) == 1

    engine.close()
    assert engine._doc_cache == {}

    # Engine is reusable after close (handle reopened transparently)
    r2 = engine.extract_page(path, 1)
    assert r2.status.value == "ok"
    assert any("page 1" in b.text for b in r2.blocks)
    engine.close()
