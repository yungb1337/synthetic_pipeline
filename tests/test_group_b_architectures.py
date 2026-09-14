"""Tests for GROUP B architectural features (B1, B2, B3).

B1: Single-box multi-process sharding (_shard_files in CLI)
B2: Remote Docling microservice & engine (docling_service + RemoteDoclingEngine)
B3: Multi-box cluster sharding (shard_doc_refs in corpus/executor)
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.parser.cli import _shard_files
from app.parser.config import ParserConfig
from app.parser.docling_service import create_server
from app.parser.engines.base import DOCLING, PageWorkItem
from app.parser.engines.remote_docling import RemoteDoclingEngine
from app.parser.page_result import PageResult, PageStatus
from app.parser.parts import RecoveredBlock
from app.parser.scheduler import Scheduler
from app.processing.config import ProcessingConfig
from app.processing.corpus import DocRef, shard_doc_refs
from app.processing.executor import BatchWorker


def test_b1_file_sharding():
    """B1: _shard_files partitions files deterministically without loss or overlap."""
    files = [Path(f"doc_{i:03d}.pdf") for i in range(30)]
    shards = 3

    partitioned = []
    for s_idx in range(shards):
        shard = _shard_files(files, shard_index=s_idx, shard_total=shards)
        partitioned.append(shard)

    # All files covered
    all_sharded = [f for s in partitioned for f in s]
    assert len(all_sharded) == len(files)
    assert set(all_sharded) == set(files)

    # Shards are mutually exclusive
    for i in range(shards):
        for j in range(i + 1, shards):
            assert set(partitioned[i]).isdisjoint(set(partitioned[j]))

    # Out of bounds check
    with pytest.raises(ValueError):
        _shard_files(files, shard_index=3, shard_total=3)


def test_b2_remote_docling_service_and_engine(tmp_path):
    """B2: Docling HTTP microservice handles health check and page processing."""
    # Create test PDF file
    test_pdf = tmp_path / "test_doc.pdf"
    test_pdf.write_bytes(b"%PDF-1.4 mock content")

    # Mock HeavyDoclingEngine inside docling_service
    mock_page_result = PageResult(
        doc_id="d-remote-test",
        page_index=0,
        route=DOCLING,
        status=PageStatus.OK,
        blocks=[RecoveredBlock(page=1, kind="paragraph", text="Remote extracted text")],
    )

    port = 18991
    server = create_server(host="127.0.0.1", port=port)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    time.sleep(0.1)

    try:
        cfg = ParserConfig(docling_service_url=f"http://127.0.0.1:{port}")
        engine = RemoteDoclingEngine(cfg)

        # 1. Health check
        assert engine.health_check() is True

        # 2. Page processing
        item = PageWorkItem(
            doc_id="d-remote-test",
            src_path=str(test_pdf),
            page_index=0,
            route=DOCLING,
        )

        with patch("app.parser.docling_service.HeavyDoclingEngine.process", return_value=mock_page_result):
            res = engine.process(item)

        assert res.status == PageStatus.OK
        assert len(res.blocks) == 1
        assert res.blocks[0].text == "Remote extracted text"

        # 3. Test Scheduler routing when docling_service_url is configured
        sched = Scheduler(cfg)
        assert getattr(sched.config, "docling_service_url", "") == f"http://127.0.0.1:{port}"
        sched.close()

    finally:
        server.shutdown()
        server.server_close()


def test_b2_remote_engine_fallback_when_offline():
    """B2: When remote Docling service is unreachable, returns contained FAILED result."""
    cfg = ParserConfig(docling_service_url="http://127.0.0.1:59999")
    engine = RemoteDoclingEngine(cfg, timeout=1.0)
    assert engine.health_check() is False

    item = PageWorkItem(doc_id="d-offline", src_path="nonexistent.pdf", page_index=0)
    res = engine.process(item)
    assert res.status == PageStatus.FAILED
    assert len(res.errors) > 0


def test_b3_cluster_shard_doc_refs():
    """B3: shard_doc_refs deterministically partitions DocRefs across nodes."""
    refs = [
        DocRef(path=f"/path/{i}.pdf", name=f"{i}.pdf", size=100, sha256=f"{i:04x}" * 16)
        for i in range(40)
    ]
    total_nodes = 4
    shards = []
    for node_idx in range(total_nodes):
        node_refs = shard_doc_refs(refs, shard_index=node_idx, shard_total=total_nodes)
        shards.append(node_refs)

    all_refs = [r for s in shards for r in s]
    assert len(all_refs) == len(refs)
    assert set(all_refs) == set(refs)

    for i in range(total_nodes):
        for j in range(i + 1, total_nodes):
            assert set(shards[i]).isdisjoint(set(shards[j]))


def test_b3_batch_worker_with_sharding():
    """B3: BatchWorker respects shard_index and shard_total configuration."""
    pcfg = ProcessingConfig(shard_index=1, shard_total=2, concurrency=4)
    mock_pipeline = MagicMock()
    worker = BatchWorker(pcfg, mock_pipeline)

    refs = [
        DocRef(path=f"/path/{i}.pdf", name=f"{i}.pdf", size=100, sha256=f"{i:02x}" * 32)
        for i in range(10)
    ]

    with patch.object(worker, "_run_with_retries", return_value=MagicMock(status="ok", docref=refs[0], document_id="d0", result_type="pdf")) as mock_run:
        with patch("app.processing.executor.load_manifest", return_value=set()):
            with patch("app.processing.executor.save_manifest"):
                worker.run(refs)
                # Exactly shard subset was processed (5 out of 10)
                assert mock_run.call_count == 5
