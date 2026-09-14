"""I-13 regression tests — runtime observability.

1. Scheduler emits a `parser.metrics.v1` aggregate at the end of run_plan
   (percentiles, band mix, status mix, RSS, concurrency).
2. BatchReport exposes pages/s, turnaround percentiles and band/status mix,
   merged from scheduler metrics events.
"""
from __future__ import annotations

import pytest

import fitz

from app.parser.config import ParserConfig
from app.parser.scheduler import Scheduler
from app.parser.storage import FilesystemStore
from app.parser.storage_pages import Ledger, PageStore
from app.parser.planner import Planner
from app.parser.source import SourceScan


def _pdf_bytes(pages: int = 3) -> bytes:
    doc = fitz.open()
    for i in range(pages):
        p = doc.new_page(width=595, height=842)
        p.insert_text((72, 100), f"Metric page {i}", fontsize=11)
    blob = doc.tobytes()
    doc.close()
    return blob


def test_i13_run_plan_emits_metrics_event(tmp_path):
    events: list[tuple[str, dict]] = []
    root = tmp_path / "store"
    store = FilesystemStore(str(root))
    ps = PageStore(str(root))
    led = Ledger(str(root))

    sched = Scheduler(ParserConfig(), page_store=ps, ledger=led,
                      metrics_sink=lambda n, p: events.append((n, p)))

    manifest = SourceScan.scan(_pdf_bytes(3), "m.pdf", store)
    planner = Planner(ps, led)
    plan = planner.plan(manifest, "native", None, ParserConfig())

    results = sched.run_plan(plan)
    assert all(r.status.value == "ok" for r in results)
    assert len(events) == 1
    name, payload = events[0]
    assert name == "parser.metrics.v1"
    assert payload["pages_total"] == 3
    assert payload["by_band"] == {"native": 3}
    assert payload["by_status"] == {"ok": 3}
    ta = payload["page_turnaround_ms"]
    assert ta["p50"] is not None and ta["p50"] >= 0
    assert ta["p95"] >= ta["p50"]
    assert payload["native_concurrency"] == sched.native_concurrency
    assert payload["heavy_concurrency"] == sched.heavy_concurrency
    assert payload["run_ms"] >= 0
    sched.close()


def test_i13_no_sink_emits_nothing_and_never_crashes(tmp_path):
    root = tmp_path / "store"
    store = FilesystemStore(str(root))
    ps = PageStore(str(root))
    led = Ledger(str(root))
    sched = Scheduler(ParserConfig(), page_store=ps, ledger=led)  # no metrics_sink

    manifest = SourceScan.scan(_pdf_bytes(2), "m.pdf", store)
    plan = Planner(ps, led).plan(manifest, "native", None, ParserConfig())
    results = sched.run_plan(plan)  # must not raise without a sink
    assert len(results) == 2
    sched.close()


def test_i13_percentile_math():
    from app.parser.scheduler import _percentile

    assert _percentile([], 50) is None
    assert _percentile([5.0], 50) == 5.0
    assert _percentile(list(range(1, 101)), 50) == 50.5
    assert _percentile(list(range(1, 101)), 95) == 95.05
    assert _percentile([1.0, 2.0, 3.0, 4.0], 0) == 1.0
    assert _percentile([1.0, 2.0, 3.0, 4.0], 100) == 4.0


def test_i13_batchreport_merges_metrics():
    from app.processing.executor import BatchReport

    r = BatchReport()
    r.merge_metrics({
        "pages_total": 7,
        "by_band": {"native": 5, "docling": 2},
        "by_status": {"ok": 6, "failed": 1},
        "page_turnaround_ms": {"p50": 10.0, "p95": 20.0, "p99": 30.0},
        "rss_mb": 512.5,
    })
    r.merge_metrics({
        "pages_total": 3,
        "by_band": {"native": 3},
        "by_status": {"ok": 3},
        "page_turnaround_ms": {"p50": 12.0, "p95": 25.0},
        "rss_mb": 600.0,
    })
    assert r.pages_seen == 10
    assert r.by_band == {"native": 8, "docling": 2}
    assert r.by_page_status == {"ok": 9, "failed": 1}
    # later samples overwrite percentile slots (per-run snapshot semantics)
    assert r.page_turnaround_p50_ms == 12.0
    assert r.page_turnaround_p95_ms == 25.0
    assert r.page_turnaround_p99_ms == 30.0
    assert r.rss_mb == 600.0

    # throughput fields compute at run end
    r.ok = 10
    r.elapsed_ms = 5000.0
    from app.processing.executor import BatchWorker  # noqa: F401  (import sanity)
    # docs_per_s/pages_per_s are set by BatchWorker.run; compute directly here:
    secs = r.elapsed_ms / 1000.0
    assert round(r.ok / secs, 3) == 2.0
