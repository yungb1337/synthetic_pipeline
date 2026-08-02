"""Tests for the batch processing layer (worker pool + incremental manifest)."""
from __future__ import annotations

from dataclasses import replace

from app.parser.storage import FilesystemStore
from app.processing.config import ProcessingConfig
from app.processing.corpus import hash_paths, scan_dir
from app.processing.executor import BatchWorker, ParseNormalizePipeline


def _cfg(**kw) -> ProcessingConfig:
    base = ProcessingConfig(ocr_warm=False, concurrency=4)
    return replace(base, **kw)


def _write_corpus(root, n: int) -> None:
    (root / "inputs").mkdir(parents=True, exist_ok=True)
    for i in range(n):
        (root / "inputs" / f"doc_{i}.md").write_text(
            f"# Report {i}\n\nPatient {i} observed with  stable signs.\n\n- item a\n- item b\n",
            encoding="utf-8",
        )


def _refs(inputs_dir, cfg):
    return hash_paths(scan_dir(inputs_dir, cfg.exts), cfg.concurrency)


def test_scan_and_hash(tmp_path):
    _write_corpus(tmp_path, n=3)
    cfg = _cfg()
    refs = _refs(str(tmp_path / "inputs"), cfg)
    assert len(refs) == 3
    assert all(r.sha256 for r in refs)


def test_incremental_manifest_skips_done(tmp_path):
    _write_corpus(tmp_path, n=3)
    cfg = _cfg(manifest_path=str(tmp_path / "manifest.json"))
    store = FilesystemStore(str(tmp_path / "store"))
    worker = BatchWorker(cfg, ParseNormalizePipeline(store))
    refs = _refs(str(tmp_path / "inputs"), cfg)

    r1 = worker.run(refs)
    assert r1.ok == 3 and r1.skipped == 0

    # re-run against the same manifest: everything already done -> all skipped
    r2 = worker.run(refs)
    assert r2.ok == 0 and r2.skipped == 3

    # a new file appears -> only it is processed (manifest persisted across runs)
    (tmp_path / "inputs" / "extra.md").write_text("# Extra\nmore  text\n", encoding="utf-8")
    refs2 = _refs(str(tmp_path / "inputs"), cfg)
    r3 = worker.run(refs2)
    assert r3.ok == 1 and r3.skipped == 3


def test_failed_docs_do_not_crash_batch(tmp_path):
    (tmp_path / "inputs").mkdir(parents=True, exist_ok=True)
    (tmp_path / "inputs" / "good.md").write_text("# Good\nfine text\n", encoding="utf-8")
    (tmp_path / "inputs" / "bad.pdf").write_bytes(b"\x00\x01\x02 corrupt-not-a-pdf")
    cfg = _cfg(manifest_path=str(tmp_path / "manifest.json"))
    store = FilesystemStore(str(tmp_path / "store"))
    worker = BatchWorker(cfg, ParseNormalizePipeline(store))
    refs = _refs(str(tmp_path / "inputs"), cfg)
    rep = worker.run(refs)
    assert rep.ok == 1 and rep.failed == 1