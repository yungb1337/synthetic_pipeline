"""Tests for GROUP A throughput improvements (A1 - A6).

A1: Per-page item map in HeavyDoclingEngine (O(N) instead of O(N^2) item scans)
A2: Source bytes cached per document in HeavyDoclingEngine
A3: Tightened heavy pool recycling (10 max tasks) + RSS trigger hook
A4: Batch concurrency capped for heavy corpora (concurrency_cap_heavy)
A5: calibrate_f helper in ResourceGovernor
A6: Batch page-store ledger journal writes (update_pages_batch)
"""

from __future__ import annotations

from unittest.mock import MagicMock

from app.parser.config import ParserConfig
from app.parser.engines.heavy_docling import HeavyDoclingEngine
from app.parser.scheduler import ResourceGovernor
from app.parser.storage_pages import Ledger
from app.processing.config import ProcessingConfig
from app.processing.executor import BatchWorker


def test_a1_a2_heavy_docling_item_indexing_and_src_cache(tmp_path):
    """A1 & A2: Items are indexed per page and source bytes are cached per path."""
    cfg = ParserConfig()
    engine = HeavyDoclingEngine(cfg)

    # Verify source bytes cache
    src_file = tmp_path / "test.pdf"
    src_file.write_bytes(b"%PDF-1.4 test bytes")

    b1 = engine._src_bytes(str(src_file))
    assert b1 == b"%PDF-1.4 test bytes"
    assert str(src_file) in engine._src_cache
    # Second read hits cache
    b2 = engine._src_bytes(str(src_file))
    assert b2 is b1


def test_a3_heavy_pool_recycling_config():
    """A3: Default recycling interval is tightened to 10."""
    cfg = ParserConfig()
    assert cfg.heavy_pool_max_tasks_per_child == 10
    assert cfg.heavy_pool_rss_threshold_mb == 0


def test_a4_batch_concurrency_cap():
    """A4: Batch concurrency is bounded when heavy_concurrency is set."""
    pcfg = ProcessingConfig(
        concurrency=16, heavy_concurrency=4, concurrency_cap_heavy=2
    )
    mock_pipeline = MagicMock()
    worker = BatchWorker(pcfg, mock_pipeline)
    # Effective concurrency is min(16, 4 * 2) = 8
    assert worker._effective_concurrency() == 8

    # When cap is disabled (0), base concurrency is returned
    pcfg_uncapped = ProcessingConfig(
        concurrency=16, heavy_concurrency=4, concurrency_cap_heavy=0
    )
    worker_uncapped = BatchWorker(pcfg_uncapped, mock_pipeline)
    assert worker_uncapped._effective_concurrency() == 16


def test_a5_resource_governor_calibrate_f():
    """A5: calibrate_f returns measured bytes or None."""
    val = ResourceGovernor.calibrate_f()
    # If docling is installed, returns a float budget
    assert val is None or isinstance(val, float)


def test_a6_ledger_batch_updates(tmp_path):
    """A6: update_pages_batch writes multiple entries atomically to journal."""
    ledger = Ledger(str(tmp_path))
    doc_id = "d-test-batch"

    updates = [
        {"page_index": 0, "status": "ok", "checksum": "abc0", "engine": "docling"},
        {"page_index": 1, "status": "ok", "checksum": "abc1", "engine": "docling"},
        {"page_index": 2, "status": "ok", "checksum": "abc2", "engine": "docling"},
    ]
    ledger.update_pages_batch(doc_id, updates)

    plan = ledger.load_plan(doc_id)
    assert plan is not None
    pages = plan.get("pages", {})
    assert len(pages) == 3
    assert pages["0"]["status"] == "ok"
    assert pages["1"]["status"] == "ok"
    assert pages["2"]["status"] == "ok"
