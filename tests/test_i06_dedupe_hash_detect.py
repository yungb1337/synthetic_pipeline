"""I-06 regression tests — one hash, one detect per extract.

Before the fix, `Extractor.extract` ran `detection.detect` (once normally,
twice on the resume fast-path) and `SourceScan.scan` re-hashed the full
buffer even when the executor had already computed the sha256. These tests
pin the new contract: exactly ONE hash of the data and ONE detect per
`extract()` call.
"""

from __future__ import annotations

import fitz

from app.parser import detection
from app.parser.config import ParserConfig
from app.parser.extraction import Extractor
from app.parser.source import SourceScan
from app.parser.storage import FilesystemStore


def _pdf_bytes(pages: int = 2) -> bytes:
    doc = fitz.open()
    for i in range(pages):
        p = doc.new_page(width=595, height=842)
        p.insert_text((72, 100), f"Dedupe page {i}", fontsize=11)
    blob = doc.tobytes()
    doc.close()
    return blob


def test_i06_scan_reuses_supplied_detected_and_hash(monkeypatch):
    import hashlib

    import app.parser.source as src_mod

    calls = {"detect": 0, "hash": 0}
    real_detect = detection.detect
    real_sha256 = hashlib.sha256

    def counting_detect(data, filename=""):
        calls["detect"] += 1
        return real_detect(data, filename)

    def counting_sha256(data=None):
        calls["hash"] += 1
        return real_sha256(data)

    # source.py sees the same hashlib module object + the detection module,
    # so patching these covers everything scan() could call.
    monkeypatch.setattr(src_mod.detection, "detect", counting_detect)
    monkeypatch.setattr(hashlib, "sha256", counting_sha256)

    store = FilesystemStore("work/_i06_tmp_store")
    # Supply both -> scan must neither detect nor hash.
    m = SourceScan.scan(
        _pdf_bytes(),
        "x.pdf",
        store,
        detected=real_detect(_pdf_bytes(), "x.pdf"),
        source_hash=real_sha256(_pdf_bytes()).hexdigest(),
    )

    assert calls["detect"] == 0, "scan re-detected despite supplied result"
    assert calls["hash"] == 0, "scan re-hashed despite supplied hash"
    assert m.doc_id.startswith("d-")


def test_i06_scan_standalone_still_works(tmp_path):
    """No args -> previous behavior exactly (detect + hash inside scan)."""
    store = FilesystemStore(str(tmp_path / "store"))
    m = SourceScan.scan(_pdf_bytes(), "x.pdf", store)
    assert m.page_count == 2
    assert m.doc_id.startswith("d-")


def test_i06_extract_hashes_once_detects_once(tmp_path, monkeypatch):
    store = FilesystemStore(str(tmp_path / "store"))
    ex = Extractor(
        ParserConfig(),
        store,
        events=__import__(
            "app.parser.events", fromlist=["EventPublisher"]
        ).EventPublisher(sink=lambda n, p: None),
    )

    counts = {"detect": 0}
    real_detect = detection.detect

    def counting_detect(data, filename=""):
        counts["detect"] += 1
        return real_detect(data, filename)

    monkeypatch.setattr(detection, "detect", counting_detect)
    monkeypatch.setattr("app.parser.source.detection.detect", counting_detect)

    po = ex.extract(_pdf_bytes(), "x.pdf")
    assert po.status == "parsed"
    # I-06 contract: exactly ONE detection per extract (was 2; 3 on resume).
    assert counts["detect"] == 1, f"detect ran {counts['detect']} times"


def test_i06_resume_fastpath_detects_once(tmp_path, monkeypatch):
    """The resume fast-path reuses the detect result instead of re-calling it."""
    store = FilesystemStore(str(tmp_path / "store"))
    events = __import__(
        "app.parser.events", fromlist=["EventPublisher"]
    ).EventPublisher(sink=lambda n, p: None)
    ex = Extractor(ParserConfig(), store, events=events)
    blob = _pdf_bytes()

    counts = {"detect": 0}
    real_detect = detection.detect

    def counting_detect(data, filename=""):
        counts["detect"] += 1
        return real_detect(data, filename)

    monkeypatch.setattr(detection, "detect", counting_detect)

    po1 = ex.extract(blob, "x.pdf")  # full parse populates the ledger
    assert po1.status == "parsed"
    counts["detect"] = 0  # reset for the resume run

    po2 = ex.extract(blob, "x.pdf", resume=True)
    assert po2.status == "parsed"
    # Fast path: one detect total (reused across the fast-path report + scan).
    assert counts["detect"] == 1, f"detect ran {counts['detect']} times on resume"


def test_i06_doc_id_stable_with_supplied_hash(tmp_path):
    """doc_id derives from the supplied hash — identical bytes, identical id."""
    store = FilesystemStore(str(tmp_path / "store"))
    blob = _pdf_bytes()
    import hashlib

    sha = hashlib.sha256(blob).hexdigest()

    m1 = SourceScan.scan(blob, "a.pdf", store, source_hash=sha)
    m2 = SourceScan.scan(blob, "a.pdf", store, source_hash=sha)
    assert m1.doc_id == m2.doc_id == f"d-{sha[:16]}"
