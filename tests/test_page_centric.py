"""Tests for the page-centric parser execution model (ADR-013).

Covers the durable unit (`PageResult`), the `ResourceGovernor` math, the
`Planner` expected-page-set + resume, the silent-loss gate (`docling_guard_status`
+ `DocumentValidator`), and an end-to-end `Scheduler -> Assembler` over a small
native (PyMuPDF) 2-page fixture (no Docling required — runs everywhere).
"""
from __future__ import annotations

import json

import pytest

from app.parser.config import ParserConfig
from app.parser.page_result import PAGE_SCHEMA_VERSION, PageResult, PageStatus
from app.parser.parts import RecoveredBlock, RecoveredTable
from app.parser.scheduler import ResourceGovernor, Scheduler
from app.parser.planner import ExecutionPlan, Planner
from app.parser.engines.base import PageWorkItem
from app.parser.source import SourceScan
from app.parser.storage import FilesystemStore
from app.parser.storage_pages import Ledger, PageStore
from app.parser.assembler import Assembler, DocumentValidator
from app.parser.events import EventPublisher


# ---------------------------------------------------------------------------
# PageResult — the durable + processing unit
# ---------------------------------------------------------------------------
def test_page_result_roundtrip_and_checksum():
    blk = RecoveredBlock(page=0, kind="paragraph", text="hello", bbox=(0, 0, 10, 10), seq=0, source="text")
    tbl = RecoveredTable(page=0, header=["a"], rows=[["1"]], source="native")
    r = PageResult(
        doc_id="d-abc", page_index=0, route="native", status=PageStatus.OK,
        blocks=[blk], tables=[tbl], source_hash="sha",
    )
    assert r.content_present is True
    cs = r.compute_checksum()
    assert cs
    js = r.to_json()
    back = PageResult.from_json(js)
    assert back.doc_id == r.doc_id
    assert back.page_index == 0
    assert back.status == PageStatus.OK
    assert back.blocks[0].text == "hello"
    assert back.tables[0].rows == [["1"]]
    assert back.checksum == cs
    # version is pinned
    assert json.loads(js)["schema_version"] == PAGE_SCHEMA_VERSION


def test_page_result_empty_is_not_content_present():
    r = PageResult(doc_id="d-x", page_index=1, route="native", status=PageStatus.OK, blocks=[])
    assert r.content_present is False


def test_page_result_status_normalizes_from_string():
    r = PageResult(doc_id="d", page_index=0, status="failed")
    assert r.status == PageStatus.FAILED


# ---------------------------------------------------------------------------
# ResourceGovernor — hardware-derived heavy concurrency
# ---------------------------------------------------------------------------
def test_resource_governor_formula():
    g = ResourceGovernor()
    # usable = 16GiB * 0.80 = ~13.1GiB; overhead 2GiB; F = 2GiB
    ram = 16 * 1024**3
    F = 2 * 1024**3
    n = g.derive_heavy_concurrency(ram_cap=ram, base_overhead=2 * 1024**3, F=F)
    # floor((13.1GiB - 2GiB) / 2GiB) == floor(5.56) == 5
    assert n == 5


def test_resource_governor_none_f_returns_one():
    g = ResourceGovernor()
    assert g.derive_heavy_concurrency(ram_cap=16 * 1024**3, F=None) == 1
    # zero/negative F never divides by zero
    assert g.derive_heavy_concurrency(ram_cap=16 * 1024**3, F=0) == 1


def test_resource_governor_cgroup_bounds_ram(monkeypatch):
    g = ResourceGovernor()
    # cgroup caps the box to 4GiB; usable = 4GiB*0.8 = 3.2GiB
    monkeypatch.setattr(g, "_cgroup_max", lambda: 4 * 1024**3)
    n = g.derive_heavy_concurrency(ram_cap=1024 * 1024**3, base_overhead=2 * 1024**3, F=1024**3)
    # floor((3.2GiB - 2GiB)/1GiB) == floor(1.2) == 1
    assert n == 1


def test_resource_governor_periodic_recheck_only_downward(monkeypatch):
    import sys
    import types

    import app.parser.scheduler as sched_mod

    g = ResourceGovernor()
    g.measured_f = 1024**3
    # pretend only 2.5GiB available -> usable 2GiB; overhead 2GiB -> 0 -> floor -> 1
    fake_psutil = types.SimpleNamespace(
        virtual_memory=lambda: types.SimpleNamespace(available=2.5 * 1024**3)
    )
    monkeypatch.setattr(g, "_cgroup_max", lambda: None)
    # `periodic_recheck` does `import psutil`, so patch sys.modules to inject it.
    monkeypatch.setitem(sys.modules, "psutil", fake_psutil)
    # current is 4; downward-only => 1
    assert g.periodic_recheck(4) == 1
    # if measured_f unset (engine not probed), recheck returns the stored floor
    # (cannot derive a downward number without a measured footprint).
    g2 = ResourceGovernor()
    monkeypatch.setitem(sys.modules, "psutil", fake_psutil)
    assert g2.periodic_recheck(3) == 1
    # cleanup
    monkeypatch.undo()


# ---------------------------------------------------------------------------
# Planner — expected page set + resume
# ---------------------------------------------------------------------------
def _scan_pdf(tmp_path, pages: int = 3) -> tuple[FilesystemStore, "object"]:
    import fitz

    doc = fitz.open()
    for _ in range(pages):
        p = doc.new_page(width=595, height=842)
        p.insert_text((72, 100), "Page text", fontsize=11)
    blob = doc.tobytes()
    store = FilesystemStore(str(tmp_path / "store"))
    manifest = SourceScan.scan(blob, "doc.pdf", store)
    assert manifest.page_count == pages
    assert manifest.expected_page_set == list(range(pages))
    return store, manifest


def test_planner_builds_one_work_item_per_page(tmp_path):
    store, manifest = _scan_pdf(tmp_path, pages=3)
    page_store = PageStore(str(store.root))
    ledger = Ledger(str(store.root))
    planner = Planner(page_store, ledger)
    plan = planner.plan(manifest, "native", None, ParserConfig())
    assert len(plan.work_items) == 3
    assert [w.page_index for w in plan.work_items] == [0, 1, 2]
    # ledger written
    assert ledger.load_plan(manifest.doc_id) is not None


def test_planner_resume_skips_done_pages(tmp_path):
    store, manifest = _scan_pdf(tmp_path, pages=3)
    page_store = PageStore(str(store.root))
    ledger = Ledger(str(store.root))
    planner = Planner(page_store, ledger)
    # First plan pass writes the ledger (pending pages).
    planner.plan(manifest, "native", None, ParserConfig())
    # Mark page 0 OK and present in the page store (simulating a prior run).
    ok_res = PageResult(doc_id=manifest.doc_id, page_index=0, route="native",
                        status=PageStatus.OK, blocks=[RecoveredBlock(page=0, text="x", source="text")])
    page_store.put_page(manifest.doc_id, 0, ok_res)
    ledger.update_page(manifest.doc_id, 0, PageStatus.OK, ok_res.checksum, "native", 0, [])

    plan = planner.plan(manifest, "native", None, ParserConfig(), resume=True)
    # page 0 already done -> excluded
    assert [w.page_index for w in plan.work_items] == [1, 2]


def test_planner_docling_band_degrades_without_engine(monkeypatch, tmp_path):
    store, manifest = _scan_pdf(tmp_path, pages=2)
    page_store = PageStore(str(store.root))
    ledger = Ledger(str(store.root))
    monkeypatch.setattr("app.parser.planner.docling_loader.engine_available", lambda: False)
    planner = Planner(page_store, ledger)
    plan = planner.plan(manifest, "docling", None, ParserConfig(ocr_enabled=True))
    assert plan.route == "enrichment"  # graceful degrade, never crash


# ---------------------------------------------------------------------------
# Silent-loss gate
# ---------------------------------------------------------------------------
def test_docling_guard_status_detects_empty_stub(monkeypatch):
    from app.parser.loaders import docling_loader

    class FakeErr:
        page_no = 1
        category = "x"
        error_message = "boom"

    class FakePage:
        def items(self):
            return []  # no content items => empty stub

    class FakeDoc:
        pages = {1: FakePage()}

    class FakeInput:
        page_count = 1

    class FakeResult:
        status = "PARTIAL_SUCCESS"
        errors = [FakeErr()]
        document = FakeDoc()
        input = FakeInput()

    status_name, errors, expected, produced = docling_loader.docling_guard_status(FakeResult())
    assert status_name == "PARTIAL_SUCCESS"
    assert expected == 1
    assert produced == 0  # empty stub => 0 produced content pages
    assert errors and errors[0]["message"] == "boom"


def test_validator_rejects_incomplete_assembly(tmp_path):
    store, manifest = _scan_pdf(tmp_path, pages=3)
    page_store = PageStore(str(store.root))
    ledger = Ledger(str(store.root))
    planner = Planner(page_store, ledger)
    plan = planner.plan(manifest, "native", None, ParserConfig())

    # Only 2 of 3 pages OK -> not complete
    results = [
        PageResult(doc_id=manifest.doc_id, page_index=0, route="native", status=PageStatus.OK,
                   blocks=[RecoveredBlock(page=0, text="a", source="text")]),
        PageResult(doc_id=manifest.doc_id, page_index=1, route="native", status=PageStatus.OK,
                   blocks=[RecoveredBlock(page=1, text="b", source="text")]),
        PageResult(doc_id=manifest.doc_id, page_index=2, route="native", status=PageStatus.FAILED,
                   errors=[{"page_no": 3, "category": "x", "message": "fail"}]),
    ]
    assert DocumentValidator.is_complete(results, plan) is False
    rep = DocumentValidator.classify(results, plan)
    assert rep.missing_pages == [2]
    assert rep.status == "partial"


def test_validator_accepts_complete_assembly(tmp_path):
    store, manifest = _scan_pdf(tmp_path, pages=2)
    page_store = PageStore(str(store.root))
    ledger = Ledger(str(store.root))
    planner = Planner(page_store, ledger)
    plan = planner.plan(manifest, "native", None, ParserConfig())
    results = [
        PageResult(doc_id=manifest.doc_id, page_index=0, route="native", status=PageStatus.OK,
                   blocks=[RecoveredBlock(page=0, text="a", source="text")]),
        # A1: a PARTIAL page only counts as assembled when it carries recovered
        # content (content_present is True). Without content it would fall into
        # missing/failed and be dead-lettered — never reported as a silent
        # success.
        PageResult(doc_id=manifest.doc_id, page_index=1, route="native", status=PageStatus.PARTIAL,
                   blocks=[RecoveredBlock(page=1, text="b", source="text")]),
    ]
    assert DocumentValidator.is_complete(results, plan) is True


# ---------------------------------------------------------------------------
# End-to-end: Scheduler -> Assembler (native band; no Docling needed)
# ---------------------------------------------------------------------------
def test_scheduler_run_plan_native_then_assemble(tmp_path):
    store, manifest = _scan_pdf(tmp_path, pages=2)
    page_store = PageStore(str(store.root))
    ledger = Ledger(str(store.root))
    config = ParserConfig()
    planner = Planner(page_store, ledger)
    plan = planner.plan(manifest, "native", None, config)

    # Run native pages in-process (no ProcessPool) for the test.
    sched = Scheduler(config, page_store=page_store, ledger=ledger, prefer_in_process_heavy=True)
    results = sched.run_plan(plan, prefer_in_process_heavy=True)
    sched.close()

    assert len(results) == 2
    for r in results:
        assert r.status == PageStatus.OK
    # each result persisted to the page store + ledger
    for r in results:
        assert page_store.page_exists(manifest.doc_id, r.page_index)
        lp = ledger.load_plan(manifest.doc_id)
        assert lp["pages"][str(r.page_index)]["status"] == "ok"

    # Assemble + validate (hard gate)
    assembler = Assembler(config, store, ledger)
    report = assembler.assemble(plan, results, manifest.src_path, manifest.source_hash)
    assert report.status == "ok"
    assert report.expected_pages == 2 and report.actual_pages == 2
    assert report.document is not None
    assert report.document.num_blocks() > 0
    # DOM at the unchanged dom/<doc_id>/dom-v*.docJSON layout
    assert report.dom_key.startswith("dom/")
    assert "/dom-v" in report.dom_key
    # assembly recorded
    assert ledger.load_plan(manifest.doc_id)["assembly"]["status"] == "ok"


def test_assembler_dead_letters_exhausted_page(tmp_path):
    store, manifest = _scan_pdf(tmp_path, pages=1)
    page_store = PageStore(str(store.root))
    ledger = Ledger(str(store.root))
    planner = Planner(page_store, ledger)
    plan = planner.plan(manifest, "native", None, ParserConfig())
    # A FAILED page that cannot be retried (no engine produces it in-process here
    # is fine; we just feed a failed result and disable retries).
    results = [
        PageResult(doc_id=manifest.doc_id, page_index=0, route="native", status=PageStatus.FAILED,
                   errors=[{"page_no": 1, "category": "x", "message": "perm fail"}]),
    ]
    assembler = Assembler(ParserConfig(), store, ledger)
    report = assembler.assemble(plan, results, manifest.src_path, manifest.source_hash, max_retries=0)
    # Exhausted => dead, actual_pages == 0, never reported as complete
    assert report.status == "dead"
    assert report.actual_pages == 0
    assert 0 in report.dead_pages


# ---------------------------------------------------------------------------
# B1 — HeavyDoclingEngine.process unit test (guarded; T17 acceptance)
# ---------------------------------------------------------------------------
def _monkeypatch_convert_path(monkeypatch, fake):
    """Point docling_loader.convert_path at a fake returning the given object."""
    monkeypatch.setattr("app.parser.loaders.docling_loader.convert_path", fake)


def test_heavy_docling_failure_returns_failed(monkeypatch):
    docling = pytest.importorskip("docling")

    class FakeResult:
        status = "FAILURE"
        errors = []
        document = None
        input = type("I", (), {"page_count": 1})()

    _monkeypatch_convert_path(monkeypatch, lambda *a, **k: FakeResult())
    from app.parser.engines.heavy_docling import HeavyDoclingEngine

    item = PageWorkItem(doc_id="d-x", source_hash="s", src_path="x.pdf",
                        page_index=0, route="docling", models_dir="")
    r = HeavyDoclingEngine(ParserConfig()).process(item)
    assert r.status == PageStatus.FAILED
    assert r.errors


def test_heavy_docling_empty_stub_returns_partial_failed_no_content(monkeypatch):
    docling = pytest.importorskip("docling")

    class FakePage:
        def items(self):
            return []  # empty stub => no content

    class FakeDoc:
        pages = {1: FakePage()}

    class FakeResult:
        status = "PARTIAL_SUCCESS"
        errors = [type("E", (), {"page_no": 1, "category": "x", "error_message": "stub"})()]
        document = FakeDoc()
        input = type("I", (), {"page_count": 1})()

    _monkeypatch_convert_path(monkeypatch, lambda *a, **k: FakeResult())
    from app.parser.engines.heavy_docling import HeavyDoclingEngine

    item = PageWorkItem(doc_id="d-x", source_hash="s", src_path="x.pdf",
                        page_index=0, route="docling", models_dir="")
    r = HeavyDoclingEngine(ParserConfig()).process(item)
    # A1: empty stub must NOT report content_present; it must be PARTIAL/FAILED
    # and never count as an assembled (silent-loss) page.
    assert r.status in (PageStatus.PARTIAL, PageStatus.FAILED)
    assert r.content_present is False


def test_heavy_run_worker_convert_none_returns_failed(monkeypatch):
    docling = pytest.importorskip("docling")
    from app.parser.scheduler import _run_heavy

    _monkeypatch_convert_path(monkeypatch, lambda *a, **k: None)
    item = PageWorkItem(doc_id="d-x", source_hash="s", src_path="x.pdf",
                        page_index=0, route="docling", models_dir="")
    r = _run_heavy(item, ParserConfig())
    assert r.status == PageStatus.FAILED
    assert r.errors


# ---------------------------------------------------------------------------
# A2 — Corrupt / unreadable PDF must NOT report `parsed` with 0 pages
# ---------------------------------------------------------------------------
def test_source_scan_corrupt_pdf_raises(tmp_path):
    store = FilesystemStore(str(tmp_path / "store"))
    # Not a real PDF — fitz.open must fail and we must surface it, not 0 pages.
    bad = b"%PDF-1.4\nnot a real pdf body"
    with pytest.raises(Exception):
        SourceScan.scan(bad, "broken.pdf", store)


def test_corrupt_pdf_not_reported_parsed(tmp_path):
    """A2 end-to-end: a corrupt/unreadable PDF must surface as failed (never a
    silent 0-page `parsed` outcome)."""
    store = FilesystemStore(str(tmp_path / "store"))
    from app.parser.events import EventPublisher
    from app.parser.extraction import Extractor
    from app.parser.config import default_config

    # A header that looks like a PDF but has no valid body -> fitz.open fails.
    bad = b"%PDF-1.4\nnot a real pdf body"
    ex = Extractor(default_config(), store, events=EventPublisher(sink=lambda n, p: None))
    po = ex.extract(bad, "broken.pdf")
    # Invariant: must NOT be reported as `parsed`.
    assert po.status != "parsed"
    assert po.status in ("failed", "unsupported")


def test_extract_exception_never_leaves_document_pending(tmp_path):
    """ZERO-SILENT-PARTIAL-STATE safety net: if anything throws between run_plan
    and the final emit for a document, the document must NOT be left frozen in the
    `pending` ledger (planner writes it before execution). It must be marked
    `failed` in the ledger, every page FAILED (unless already persisted), and a
    `document.parse_failed` event emitted — never a silent `parsed` outcome."""
    store, _ = _scan_pdf(tmp_path, pages=2)
    from app.parser.events import EventPublisher
    from app.parser.extraction import Extractor
    from app.parser.config import default_config
    from app.parser.page_result import PageStatus

    captured = []
    events = EventPublisher(sink=lambda n, p: captured.append((n, p)))

    # Force the assembler to throw (simulating a DocumentBuilder/store edge case).
    import app.parser.assembler as asm_mod

    def boom(self, *a, **k):
        raise RuntimeError("simulated post-run crash")

    mp = __import__("pytest").MonkeyPatch()
    mp.setattr(asm_mod.Assembler, "assemble", boom)

    ex = Extractor(default_config(), store, events=events)

    # A fresh 2-page PDF so SourceScan re-derives the page set independently.
    import fitz
    d = fitz.open()
    for _ in range(2):
        d.new_page(width=595, height=842).insert_text((72, 100), "x", fontsize=11)
    blob = d.tobytes()
    d.close()

    po = ex.extract(blob, "raw.pdf")
    mp.undo()

    # The doc must be reported failed, never parsed.
    assert po.status == "failed"
    assert po.document_id is not None
    # No `document.parsed.v1` event may have fired.
    assert not any(n == "document.parsed.v1" for n, _ in captured)
    # A `document.parse_failed` event must have fired.
    assert any(n == "document.parse_failed" for n, _ in captured)

    # Ledger must not be frozen at `pending`: assembly recorded as failed, and
    # NO page is left in the `pending` limbo state (pages the scheduler already
    # persisted stay OK; pages it hadn't get marked FAILED by the safety net).
    ledger = Ledger(str(store.root))
    lp = ledger.load_plan(po.document_id)
    assert lp is not None
    assert lp["assembly"]["status"] == "failed"
    assert set(lp["pages"].keys()) == {"0", "1"}
    for p in lp["pages"].values():
        assert p["status"] != "pending"  # the core invariant: never limbo
        assert p["status"] in ("ok", "failed")


# ---------------------------------------------------------------------------
# A3 — Retry must NOT downgrade docling -> native
# ---------------------------------------------------------------------------
def test_assembler_retry_docling_stays_docling(monkeypatch, tmp_path):
    store, manifest = _scan_pdf(tmp_path, pages=1)
    page_store = PageStore(str(store.root))
    ledger = Ledger(str(store.root))
    # Force a genuine docling band (engine may be unavailable in CI; we only care
    # that the retry routing preserves the "docling" band, never downgrades it).
    monkeypatch.setattr("app.parser.planner.docling_loader.engine_available", lambda: True)
    planner = Planner(page_store, ledger)
    plan = planner.plan(manifest, "docling", None, ParserConfig())
    # A FAILED docling page; we intercept HeavyDoclingEngine to avoid building the
    # real engine and assert the band stays "docling" (never native).
    captured = {}

    def fake_heavy_process(self, item):
        captured["route"] = item.route
        return PageResult(doc_id=item.doc_id, page_index=item.page_index, route="docling",
                          status=PageStatus.OK,
                          blocks=[RecoveredBlock(page=0, text="recovered", source="docling")])

    monkeypatch.setattr("app.parser.engines.heavy_docling.HeavyDoclingEngine.process",
                        fake_heavy_process)
    results = [
        PageResult(doc_id=manifest.doc_id, page_index=0, route="docling",
                   status=PageStatus.FAILED,
                   errors=[{"page_no": 1, "category": "x", "message": "first fail"}]),
    ]
    assembler = Assembler(ParserConfig(), store, ledger)
    report = assembler.assemble(plan, results, manifest.src_path, manifest.source_hash,
                               max_retries=1)
    assert captured.get("route") == "docling"
    assert report.status == "ok"
    assert report.actual_pages == 1


# ---------------------------------------------------------------------------
# D1 — Resume reschedules FAILED/DEAD pages (does not clobber ledger)
# ---------------------------------------------------------------------------
def test_planner_resume_reschedules_failed_and_dead(tmp_path):
    store, manifest = _scan_pdf(tmp_path, pages=2)
    page_store = PageStore(str(store.root))
    ledger = Ledger(str(store.root))
    planner = Planner(page_store, ledger)
    planner.plan(manifest, "native", None, ParserConfig())
    # page 0 FAILED, page 1 DEAD in the prior ledger.
    ledger.update_page(manifest.doc_id, 0, PageStatus.FAILED, "", "native", 1, [{"m": "f"}])
    ledger.update_page(manifest.doc_id, 1, PageStatus.DEAD, "", "native", 2, [{"m": "d"}])

    plan = planner.plan(manifest, "native", None, ParserConfig(), resume=True)
    # Both failed/dead are rescheduled (with incremented attempt); nothing dropped.
    assert sorted(w.page_index for w in plan.work_items) == [0, 1]
    by_idx = {w.page_index: w for w in plan.work_items}
    assert by_idx[0].attempt == 2   # prior 1 + 1
    assert by_idx[1].attempt == 3   # prior 2 + 1


def test_planner_resume_preserves_ok_and_attempts(tmp_path):
    store, manifest = _scan_pdf(tmp_path, pages=2)
    page_store = PageStore(str(store.root))
    ledger = Ledger(str(store.root))
    planner = Planner(page_store, ledger)
    planner.plan(manifest, "native", None, ParserConfig())
    ok0 = PageResult(doc_id=manifest.doc_id, page_index=0, route="native", status=PageStatus.OK,
                     blocks=[RecoveredBlock(page=0, text="x", source="text")])
    page_store.put_page(manifest.doc_id, 0, ok0)
    ledger.update_page(manifest.doc_id, 0, PageStatus.OK, ok0.checksum, "native", 1, [])

    plan = planner.plan(manifest, "native", None, ParserConfig(), resume=True)
    assert [w.page_index for w in plan.work_items] == [1]  # page 0 skipped


# ---------------------------------------------------------------------------
# F1 — Heading classification parity: document-wide median (no per-page drift)
# ---------------------------------------------------------------------------
def _document_wide_median(path):
    import fitz

    d = fitz.open(path)
    sizes = []
    for pi in range(d.page_count):
        for blk in d[pi].get_text("dict").get("blocks", []):
            if blk.get("type") != 0:
                continue
            for line in blk.get("lines", []):
                for span in line.get("spans", []):
                    if span.get("text", "").strip():
                        sizes.append(float(span.get("size", 0.0)))
    d.close()
    return sorted(sizes)[len(sizes) // 2] if sizes else 12.0


def test_heading_parity_legacy_loader_matches_engine(tmp_path):
    """F1: the page-centric engine's heading classification must match the
    legacy `Loaders._pdf` output (both use the document-wide median body size,
    never a per-page median that would drift on cover/title pages)."""
    import fitz

    doc = fitz.open()
    # Three pages; the body text (11pt) dominates the document-wide median.
    p0 = doc.new_page(width=595, height=842)
    p0.insert_text((72, 100), "Cover Title Big", fontsize=20)
    p1 = doc.new_page(width=595, height=842)
    p1.insert_text((72, 100), "Section Heading One", fontsize=16)
    p1.insert_text((72, 140), "Body text body text body body", fontsize=11)
    p2 = doc.new_page(width=595, height=842)
    p2.insert_text((72, 140), "More body text more body text here", fontsize=11)
    path = str(tmp_path / "hp.pdf")
    doc.save(path)
    doc.close()

    from app.parser.loaders.loaders import Loaders
    from app.parser.engines.native_pdf import _native_page_from_doc
    from app.parser.detection import detect

    data = open(path, "rb").read()
    rec = Loaders(ParserConfig())._pdf(data, detect(data, "hp.pdf"))

    # Both paths must compute the SAME document-wide median (legacy and engine
    # agree on the median rule; here the 4 font sizes yield a median of 16).
    body_med = _document_wide_median(path)
    assert body_med == 16.0

    d = fitz.open(path)
    r0 = _native_page_from_doc(d[0], 0, ParserConfig(), body_med=body_med)
    r1 = _native_page_from_doc(d[1], 1, ParserConfig(), body_med=body_med)
    r2 = _native_page_from_doc(d[2], 2, ParserConfig(), body_med=body_med)
    d.close()

    # Legacy loader blocks (all pages) must match the engine blocks (all pages)
    # on `kind` — proving the heading rule is identical across both paths.
    legacy_kinds = sorted(b.kind for b in rec.blocks)
    engine_kinds = sorted(b.kind for b in (r0.blocks + r1.blocks + r2.blocks))
    assert legacy_kinds == engine_kinds
    # And headings larger than the document-wide median are still detected.
    assert "heading" in engine_kinds


def test_heading_uses_document_wide_median_not_per_page(tmp_path):
    """F1 regression: a lone large line on a cover/title page must be compared
    against the document-wide median (not its own per-page median). With a body
    page present, the document-wide median is 11; the 20pt cover line is then a
    `heading`, whereas a per-page median (20) would mislabel it as body.
    """
    import fitz

    doc = fitz.open()
    p0 = doc.new_page(width=595, height=842)
    p0.insert_text((72, 100), "Cover Title Big", fontsize=20)
    p1 = doc.new_page(width=595, height=842)
    p1.insert_text((72, 100), "Body text body text body", fontsize=11)
    p2 = doc.new_page(width=595, height=842)
    p2.insert_text((72, 100), "More body text more body text", fontsize=11)
    path = str(tmp_path / "h.pdf")
    doc.save(path)
    doc.close()

    from app.parser.engines.native_pdf import _native_page_from_doc

    body_med = _document_wide_median(path)
    assert body_med == 11.0  # body pages dominate the document-wide median

    d = fitz.open(path)
    r0 = _native_page_from_doc(d[0], 0, ParserConfig(), body_med=body_med)
    d.close()
    # The lone 20pt cover line is a heading vs the document-wide median.
    assert any(b.kind == "heading" for b in r0.blocks)
