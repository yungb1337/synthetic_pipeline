"""I-03 regression tests — assembler retries are pool-contained.

The old `_retry_pages` executed engines synchronously in the CALLING thread.
Under batch concurrency (many document threads), docling retries could run
CONCURRENT IN-PROCESS Docling conversions — exactly the RAM-spike /
std::bad_alloc hazard the scheduler's single-worker in-process pool exists to
prevent. These tests pin the new contract:

1. With a scheduler wired, docling retries run through `run_plan_for_pages`
   (pool dispatch), never in the caller's thread.
2. The B2 skip (engine_unavailable pages are futile to retry) is preserved.
3. When the scheduler returns fresh results for retried pages, they replace
   the stale failed results; untouched pages keep their original results.
4. Backward compat: no scheduler → legacy in-process path still works.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from app.parser.config import ParserConfig
from app.parser.page_result import PageResult, PageStatus
from app.parser.planner import ExecutionPlan
from app.parser.assembler import Assembler, AssemblyReport


def _plan(page_indexes: list[int], band: str = "docling") -> ExecutionPlan:
    from app.parser.engines.base import PageWorkItem

    items = [
        PageWorkItem(
            doc_id="d-x", source_hash="sha", src_path=f"/tmp/x.pdf",
            page_index=i, route=band,
        )
        for i in page_indexes
    ]
    return ExecutionPlan(
        doc_id="d-x", source_hash="sha", sha="sha", route=band,
        decision=None, detected_type="pdf", mime="application/pdf",
        declared_extension="pdf", probe="magic",
        expected_page_set=page_indexes, page_count=len(page_indexes),
        page_sizes={}, metadata={}, config_snapshot={},
        work_items=items,
    )


def _res(idx: int, status: PageStatus, band: str = "docling") -> PageResult:
    return PageResult(doc_id="d-x", page_index=idx, route=band, status=status)


def _report(failed: list[int], missing: list[int]) -> AssemblyReport:
    return AssemblyReport(doc_id="d-x", status="partial",
                          expected_pages=3, actual_pages=0,
                          failed_pages=failed, missing_pages=missing)


class _RecordingScheduler:
    """Stands in for the real Scheduler: records dispatch, fakes results."""

    def __init__(self):
        self.dispatched_pages: list[int] | None = None
        self.call_count = 0

    def run_plan_for_pages(self, plan, page_indexes):
        self.call_count += 1
        self.dispatched_pages = sorted(page_indexes)
        # Simulate the pool producing fresh results for every requested page.
        return [_res(i, PageStatus.OK) for i in self.dispatched_pages]


def test_i03_retry_dispatches_through_scheduler_pools():
    sched = _RecordingScheduler()
    a = Assembler(ParserConfig(), store=None, ledger=None, scheduler=sched)
    plan = _plan([0, 1, 2])
    results = [_res(0, PageStatus.FAILED), _res(1, PageStatus.OK), _res(2, PageStatus.MISSING if hasattr(PageStatus, "MISSING") else PageStatus.FAILED)]

    out = a._retry_pages(plan, results, _report(failed=[0, 2], missing=[]))

    # Dispatch happened exactly once, through the scheduler (pool path)
    assert sched.call_count == 1
    assert sched.dispatched_pages == [0, 2]
    # Fresh OK results replaced the stale failed ones; the OK page kept.
    by_idx = {r.page_index: r for r in out}
    assert by_idx[0].status == PageStatus.OK
    assert by_idx[1].status == PageStatus.OK
    assert by_idx[2].status == PageStatus.OK


def test_i03_engine_unavailable_pages_are_not_retried():
    """B2 skip preserved: an engine_unavailable docling page is never re-run."""
    sched = _RecordingScheduler()
    a = Assembler(ParserConfig(), store=None, ledger=None, scheduler=sched)
    plan = _plan([0, 1])
    failed_plain = _res(0, PageStatus.FAILED)
    failed_plain.errors = [{"page_no": 1, "category": "docling_convert", "message": "boom"}]
    failed_unavail = _res(1, PageStatus.FAILED)
    failed_unavail.errors = [{"page_no": 2, "category": "engine_unavailable", "message": "no engine"}]

    out = a._retry_pages(plan, [failed_plain, failed_unavail], _report(failed=[0, 1], missing=[]))

    # Only the genuinely-failed page went to the pool
    assert sched.dispatched_pages == [0]
    by_idx = {r.page_index: r for r in out}
    # engine_unavailable page keeps its original FAILED result
    assert by_idx[1].errors[0]["category"] == "engine_unavailable"
    assert by_idx[1].status == PageStatus.FAILED


def test_i03_scheduler_ownership_documented():
    """The production wiring must hand the extractor's scheduler to the assembler."""
    import inspect

    from app.parser.extraction import Extractor

    src = inspect.getsource(Extractor.__init__)
    assert "scheduler=self.scheduler" in src, (
        "Extractor must wire its scheduler into the Assembler (I-03)"
    )


def test_i03_no_scheduler_falls_back_to_inprocess(monkeypatch, tmp_path):
    """Backward compat: without a scheduler the legacy in-process path runs."""
    from app.parser.engines.base import PageWorkItem

    calls: list[str] = []

    class FakeNative:
        def __init__(self, config):
            pass

        def process(self, item: PageWorkItem):
            calls.append(f"native:{item.page_index}")
            return _res(item.page_index, PageStatus.OK, band="native")

    monkeypatch.setattr(
        "app.parser.engines.native_pdf.NativePdfEngine", FakeNative
    )
    a = Assembler(ParserConfig(), store=None, ledger=None, scheduler=None)
    plan = _plan([0], band="native")
    out = a._retry_pages(plan, [_res(0, PageStatus.FAILED, band="native")],
                         _report(failed=[0], missing=[]))
    assert calls == ["native:0"]
    assert out[0].status == PageStatus.OK
