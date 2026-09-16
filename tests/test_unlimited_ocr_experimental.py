"""Unit and integration tests for experimental Unlimited-OCR module.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import fitz
import pytest

from experimental.unlimited_ocr.adapter import DocumentRawOCR, PageRawOCR, UnlimitedOCRAdapter
from experimental.unlimited_ocr.artifacts import ArtifactManager
from experimental.unlimited_ocr.config import UnlimitedOCRConfig
from experimental.unlimited_ocr.converter import UnlimitedOCRConverter
from experimental.unlimited_ocr.metrics import (
    DocumentMetrics,
    calculate_repetition_score,
    compute_text_hash,
)
from experimental.unlimited_ocr.runner import UnlimitedOCREvaluationRunner
from tests.routing_fixtures import image_only_pdf, text_pdf


def test_repetition_score_detection():
    # Normal text
    normal = "This is a normal paragraph with diverse words and clinical terms for evaluating patient response."
    score, is_rep = calculate_repetition_score(normal)
    assert score < 0.2
    assert not is_rep

    # Infinite repetition loop text
    repeated = " ".join(["patient blood pressure systolic diastolic"] * 30)
    score, is_rep = calculate_repetition_score(repeated)
    assert is_rep
    assert score > 0.35


def test_compute_text_hash():
    t1 = "  Hello   world \n this is a test. "
    t2 = "Hello world this is a test."
    assert compute_text_hash(t1) == compute_text_hash(t2)


def test_unlimited_ocr_adapter_process(tmp_path: Path):
    pdf_bytes = text_pdf(pages=2, lines=4)
    pdf_file = tmp_path / "sample.pdf"
    pdf_file.write_bytes(pdf_bytes)

    cfg = UnlimitedOCRConfig(artifacts_dir=tmp_path / "artifacts", evaluation_dir=tmp_path / "eval")
    adapter = UnlimitedOCRAdapter(cfg)

    raw_doc = adapter.process_pdf(pdf_file, document_id="doc-test-01")

    assert raw_doc.document_id == "doc-test-01"
    assert raw_doc.page_count == 2
    assert raw_doc.status == "success"
    assert len(raw_doc.pages) == 2
    assert "Clinical Report" in raw_doc.full_text
    assert raw_doc.metrics is not None
    assert raw_doc.metrics.pages.processed_pages == 2


def test_unlimited_ocr_converter_to_dom(tmp_path: Path):
    pdf_bytes = text_pdf(pages=1, lines=3)
    pdf_file = tmp_path / "sample_conv.pdf"
    pdf_file.write_bytes(pdf_bytes)

    cfg = UnlimitedOCRConfig(artifacts_dir=tmp_path / "artifacts", evaluation_dir=tmp_path / "eval")
    adapter = UnlimitedOCRAdapter(cfg)
    raw_doc = adapter.process_pdf(pdf_file, document_id="doc-conv-01")

    converter = UnlimitedOCRConverter()
    dom = converter.convert_raw_to_dom(raw_doc)

    assert dom.document_id == "doc-conv-01"
    assert len(dom.pages) == 1
    assert dom.num_blocks() > 0
    assert dom.provenance is not None
    assert dom.provenance.ocr_engine == "unlimited-ocr-ppocrv6"
    assert dom.provenance.normalization_report is not None


def test_artifacts_manager_layout(tmp_path: Path):
    cfg = UnlimitedOCRConfig(artifacts_dir=tmp_path / "artifacts", evaluation_dir=tmp_path / "eval")
    mgr = ArtifactManager(cfg)

    doc_id = "test-doc-123"
    meta_path = mgr.save_source_metadata(doc_id, {"key": "val"})
    assert meta_path.exists()
    assert (tmp_path / "artifacts" / doc_id / "source_metadata.json").exists()

    raw_json, raw_md = mgr.save_raw_output(doc_id, {"raw": 1}, "# Title")
    assert raw_json.exists()
    assert raw_md.exists()

    norm_path = mgr.save_normalized_dom(doc_id, {"doc_id": doc_id})
    assert norm_path.exists()

    logs_out, logs_err = mgr.save_logs(doc_id, "out log", "err log")
    assert logs_out.exists()
    assert logs_err.exists()


def test_runner_on_custom_corpus(tmp_path: Path):
    corpus_dir = tmp_path / "corpus"
    corpus_dir.mkdir()

    (corpus_dir / "doc1.pdf").write_bytes(text_pdf(pages=1, lines=2))
    (corpus_dir / "doc2.pdf").write_bytes(text_pdf(pages=1, lines=3))

    cfg = UnlimitedOCRConfig(
        artifacts_dir=tmp_path / "artifacts",
        evaluation_dir=tmp_path / "eval",
        report_path=tmp_path / "reports" / "eval.md",
    )
    runner = UnlimitedOCREvaluationRunner(cfg)

    report = runner.run_evaluation(
        corpus_name=str(corpus_dir),
        limit=2,
        repeat_count=2,
        run_judge=False,  # Skip API call in unit tests
    )

    assert report["total_documents"] == 2
    assert report["total_pages"] == 2
    assert report["success_rate"] == 1.0
    assert len(report["repeatability"]) == 2
    assert report["repeatability"][0]["is_deterministic"] is True
    assert (tmp_path / "reports" / "eval.md").exists()
