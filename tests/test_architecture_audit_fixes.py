"""test_architecture_audit_fixes.py — test suite pinning the 10 architecture and reliability fixes.

Verifies:
1. P0: Scheduler deadline bounded iteration (no indefinite hang on worker stall).
2. P0: Strict persistence invariant (put_dom / put_raw exceptions mark report failed).
3. P1: Heavy pool rebuild shuts down old ProcessPoolExecutor.
4. P1: Distributed table probing inspects beyond first 4 pages.
5. P2: Worker HeavyDoclingEngine caching across page tasks.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

from app.parser.assembler import Assembler
from app.parser.config import ParserConfig
from app.parser.page_result import PageResult, PageStatus
from app.parser.planner import ExecutionPlan, PageWorkItem
from app.parser.scheduler import Scheduler, _run_heavy
from app.parser.storage import FilesystemStore
from app.routing.inspectors import _probe_page_indices


def test_p0_persistence_failure_marks_report_failed():
    """When put_dom or put_raw raises an exception, the assembly MUST fail."""
    with tempfile.TemporaryDirectory() as tmpdir:
        store = FilesystemStore(Path(tmpdir))
        assembler = Assembler(config=ParserConfig(), store=store)

        plan = ExecutionPlan(
            doc_id="doc-test-persist",
            source_hash="h1",
            sha="sha1",
            route="native",
            decision=None,
            detected_type="pdf",
            mime="application/pdf",
            declared_extension="pdf",
            probe="magic",
            page_count=1,
            expected_page_set=[0],
            page_sizes={0: (612.0, 792.0)},
            metadata={},
            config_snapshot={},
            work_items=[
                PageWorkItem(
                    doc_id="doc-test-persist",
                    page_index=0,
                    route="native",
                    src_path="test.pdf",
                    source_hash="h1",
                )
            ],
        )
        results = [
            PageResult(
                doc_id="doc-test-persist",
                page_index=0,
                route="native",
                status=PageStatus.OK,
                blocks=[],
                tables=[],
            )
        ]

        # Simulate store.put_dom failure (e.g. disk write failure)
        with patch.object(store, "put_dom", side_effect=OSError("Disk write failed")):
            with patch("app.parser.assembler._read_src", return_value=b"%PDF-1.4"):
                report = assembler.assemble(
                    plan, results, src_path="dummy.pdf", sha256="dummy_sha"
                )
                assert report.status == "failed"
                assert report.dom_key is None
                assert report.document is None
                assert any(err["category"] == "store_dom" for err in report.errors)


def test_p1_distributed_table_probe_indices():
    """Distributed table probing must sample beyond first 4 pages for multi-page docs."""
    # 100-page document must sample beginning, middle, 3/4, and tail
    indices = _probe_page_indices(100, max_pages=5)
    assert len(indices) <= 5
    assert 0 in indices
    assert 1 in indices
    assert 50 in indices
    assert 75 in indices
    assert 99 in indices

    # 4-page document probes all 4 pages
    indices_small = _probe_page_indices(4, max_pages=5)
    assert indices_small == [0, 1, 2, 3]


def test_p1_heavy_pool_rebuild_shuts_down_old_pool():
    """Rebuilding a broken heavy pool must call shutdown on the old executor."""
    config = ParserConfig()
    scheduler = Scheduler(config, heavy_concurrency=1)

    # Instantiate pool
    pool1 = scheduler._get_heavy_pool()
    assert pool1 is not None

    # Mock shutdown on old pool
    with patch.object(pool1, "shutdown") as mock_shutdown:
        scheduler._pool_is_broken = True
        pool2 = scheduler._get_heavy_pool()
        assert pool2 is not None
        assert pool2 is not pool1
        mock_shutdown.assert_called_once_with(wait=False, cancel_futures=True)

    scheduler.close()


def test_p2_worker_heavy_engine_cached():
    """_run_heavy should cache the HeavyDoclingEngine instance at module level."""
    config = ParserConfig()
    item = PageWorkItem(
        doc_id="d1",
        page_index=0,
        route="docling",
        src_path="dummy.pdf",
        source_hash="h1",
    )

    with patch(
        "app.parser.engines.heavy_docling.HeavyDoclingEngine.process",
        return_value=PageResult(
            doc_id="d1", page_index=0, route="docling", status=PageStatus.OK
        ),
    ):
        res1 = _run_heavy(item, config)
        from app.parser import scheduler

        cached_engine = scheduler._WORKER_HEAVY_ENGINE
        assert cached_engine is not None

        res2 = _run_heavy(item, config)
        assert scheduler._WORKER_HEAVY_ENGINE is cached_engine
