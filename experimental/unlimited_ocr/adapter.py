"""Isolated adapter for Baidu Unlimited-OCR (PP-OCRv6 local on-prem inference).
"""
from __future__ import annotations

import contextlib
import io
import sys
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import fitz  # PyMuPDF
from PIL import Image

from .config import UnlimitedOCRConfig
from .metrics import (
    DocumentMetrics,
    PageMetrics,
    PerformanceMetrics,
    StructuralMetrics,
    TextMetrics,
    calculate_repetition_score,
)

try:
    import psutil
except ImportError:
    psutil = None

try:
    import torch
except ImportError:
    torch = None


@dataclass
class PageRawOCR:
    page_index: int
    width: float
    height: float
    boxes: list[list[list[float]]] = field(default_factory=list)
    texts: list[str] = field(default_factory=list)
    scores: list[float] = field(default_factory=list)
    markdown: str = ""
    inference_time_ms: float = 0.0
    status: str = "success"
    error: str | None = None


@dataclass
class DocumentRawOCR:
    document_id: str
    source_path: str
    source_sha256: str
    page_count: int
    pages: list[PageRawOCR] = field(default_factory=list)
    full_text: str = ""
    full_markdown: str = ""
    status: str = "success"  # "success" | "partial" | "failed"
    errors: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    stdout: str = ""
    stderr: str = ""
    metrics: DocumentMetrics | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "document_id": self.document_id,
            "source_path": self.source_path,
            "source_sha256": self.source_sha256,
            "page_count": self.page_count,
            "status": self.status,
            "errors": self.errors,
            "metadata": self.metadata,
            "pages": [
                {
                    "page_index": p.page_index,
                    "width": p.width,
                    "height": p.height,
                    "line_count": len(p.texts),
                    "texts": p.texts,
                    "scores": p.scores,
                    "boxes": p.boxes,
                    "markdown": p.markdown,
                    "inference_time_ms": p.inference_time_ms,
                    "status": p.status,
                    "error": p.error,
                }
                for p in self.pages
            ],
            "metrics": self.metrics.to_dict() if self.metrics else None,
        }


class UnlimitedOCRAdapter:
    """Runs local Baidu PP-OCR inference across document pages and gathers rigorous telemetry."""

    def __init__(self, config: UnlimitedOCRConfig | None = None):
        self.config = config or UnlimitedOCRConfig()
        self._engine = None
        self._init_duration_ms = 0.0
        self._ensure_engine_loaded()

    def _ensure_engine_loaded(self) -> None:
        if self._engine is not None:
            return
        t0 = time.perf_counter()
        try:
            from rapidocr import RapidOCR

            if self.config.use_gpu:
                from rapidocr.utils.typings import EngineType
                params = {
                    "Det.engine_type": EngineType.TORCH,
                    "Cls.engine_type": EngineType.TORCH,
                    "Rec.engine_type": EngineType.TORCH,
                    "EngineConfig.torch.use_cuda": True,
                }
                self._engine = RapidOCR(params=params)
            else:
                self._engine = RapidOCR()
            self._init_duration_ms = (time.perf_counter() - t0) * 1000.0
        except Exception as exc:
            self._engine = None
            self._init_duration_ms = (time.perf_counter() - t0) * 1000.0
            raise RuntimeError(f"Failed to initialize Unlimited-OCR / RapidOCR engine: {exc}") from exc

    def _get_process_rss_mb(self) -> float:
        if psutil is None:
            return 0.0
        try:
            p = psutil.Process()
            return p.memory_info().rss / (1024.0 * 1024.0)
        except Exception:
            return 0.0

    def _get_vram_mb(self) -> float:
        if torch is not None and torch.cuda.is_available():
            try:
                return torch.cuda.max_memory_allocated() / (1024.0 * 1024.0)
            except Exception:
                return 0.0
        return 0.0

    def process_pdf(self, pdf_path: str | Path, document_id: str | None = None) -> DocumentRawOCR:
        pdf_path = Path(pdf_path).resolve()
        if not pdf_path.is_file():
            raise FileNotFoundError(f"Source PDF not found: {pdf_path}")

        source_bytes = pdf_path.read_bytes()
        import hashlib

        source_sha = hashlib.sha256(source_bytes).hexdigest()
        doc_id = document_id or f"unlimited-{source_sha[:16]}"

        stdout_buf = io.StringIO()
        stderr_buf = io.StringIO()

        start_wall = time.perf_counter()
        start_rss = self._get_process_rss_mb()
        peak_rss = start_rss

        raw_pages: list[PageRawOCR] = []
        doc_errors: list[dict[str, Any]] = []
        overall_status = "success"

        preprocess_ms = 0.0
        inference_ms = 0.0
        postprocess_ms = 0.0

        total_expected_pages = 0
        try:
            with fitz.open(pdf_path) as doc:
                total_expected_pages = doc.page_count
                total_pages = doc.page_count
                if total_pages == 0:
                    doc_errors.append({"category": "empty_document", "message": "PDF contains 0 pages"})
                    overall_status = "failed"

                for p_idx in range(total_pages):
                    page_t0 = time.perf_counter()
                    fitz_page = doc[p_idx]
                    p_width = float(fitz_page.rect.width)
                    p_height = float(fitz_page.rect.height)

                    # 1. Preprocessing / Rendering
                    prep_t0 = time.perf_counter()
                    try:
                        # Render to pixmap at configured DPI
                        zoom = self.config.render_dpi / 72.0
                        mat = fitz.Matrix(zoom, zoom)
                        pix = fitz_page.get_pixmap(matrix=mat, alpha=False)
                        img = Image.open(io.BytesIO(pix.tobytes("png")))

                        # Check bounding limits / memory safety guard
                        w, h = img.size
                        longest = max(w, h)
                        if longest > self.config.max_image_edge:
                            scale = self.config.max_image_edge / float(longest)
                            nw = max(1, int(round(w * scale)))
                            nh = max(1, int(round(h * scale)))
                            img = img.resize((nw, nh), Image.LANCZOS)

                        import numpy as np

                        img_array = np.array(img)
                        preprocess_ms += (time.perf_counter() - prep_t0) * 1000.0
                    except Exception as exc:
                        preprocess_ms += (time.perf_counter() - prep_t0) * 1000.0
                        err_msg = f"Page {p_idx+1} rendering failed: {exc}"
                        stderr_buf.write(err_msg + "\n")
                        doc_errors.append({"page_no": p_idx + 1, "category": "render_error", "message": str(exc)})
                        raw_pages.append(
                            PageRawOCR(
                                page_index=p_idx,
                                width=p_width,
                                height=p_height,
                                status="failed",
                                error=str(exc),
                            )
                        )
                        overall_status = "partial"
                        continue

                    # 2. OCR Inference
                    inf_t0 = time.perf_counter()
                    try:
                        with contextlib.redirect_stdout(stdout_buf), contextlib.redirect_stderr(stderr_buf):
                            res = self._engine(img_array)
                        page_inf_ms = (time.perf_counter() - inf_t0) * 1000.0
                        inference_ms += page_inf_ms
                    except Exception as exc:
                        page_inf_ms = (time.perf_counter() - inf_t0) * 1000.0
                        inference_ms += page_inf_ms
                        err_msg = f"Page {p_idx+1} OCR inference failed: {exc}"
                        stderr_buf.write(err_msg + "\n" + traceback.format_exc() + "\n")
                        doc_errors.append({"page_no": p_idx + 1, "category": "ocr_inference_error", "message": str(exc)})
                        raw_pages.append(
                            PageRawOCR(
                                page_index=p_idx,
                                width=p_width,
                                height=p_height,
                                status="failed",
                                error=str(exc),
                            )
                        )
                        overall_status = "partial"
                        continue

                    # 3. Postprocessing & Output Structuring
                    post_t0 = time.perf_counter()
                    boxes: list[list[list[float]]] = []
                    texts: list[str] = []
                    scores: list[float] = []
                    md_lines: list[str] = []

                    if res is not None:
                        # RapidOCROutput structure
                        raw_boxes = getattr(res, "boxes", None)
                        raw_txts = getattr(res, "txts", None)
                        raw_scores = getattr(res, "scores", None)

                        if raw_txts is not None and len(raw_txts) > 0:
                            for idx_line in range(len(raw_txts)):
                                t = str(raw_txts[idx_line] or "").strip()
                                s = float(raw_scores[idx_line]) if raw_scores is not None and idx_line < len(raw_scores) else 1.0
                                b = raw_boxes[idx_line].tolist() if raw_boxes is not None and idx_line < len(raw_boxes) else []
                                if t:
                                    texts.append(t)
                                    scores.append(s)
                                    boxes.append(b)
                                    md_lines.append(t)

                    page_md = "\n\n".join(md_lines)
                    raw_pages.append(
                        PageRawOCR(
                            page_index=p_idx,
                            width=p_width,
                            height=p_height,
                            boxes=boxes,
                            texts=texts,
                            scores=scores,
                            markdown=page_md,
                            inference_time_ms=page_inf_ms,
                            status="success",
                            error=None,
                        )
                    )
                    postprocess_ms += (time.perf_counter() - post_t0) * 1000.0

                    current_rss = self._get_process_rss_mb()
                    if current_rss > peak_rss:
                        peak_rss = current_rss

        except Exception as doc_exc:
            overall_status = "failed"
            doc_errors.append({"category": "document_fatal", "message": str(doc_exc), "traceback": traceback.format_exc()})
            stderr_buf.write(f"Fatal error processing document {doc_id}: {doc_exc}\n")

        end_wall = time.perf_counter()
        wall_ms = (end_wall - start_wall) * 1000.0

        full_texts = []
        for p in raw_pages:
            if p.texts:
                full_texts.extend(p.texts)
        full_text_str = "\n".join(full_texts)
        full_markdown_str = "\n\n".join(f"## Page {p.page_index + 1}\n\n{p.markdown}" for p in raw_pages if p.markdown)

        # Repetition loop detection
        rep_score, has_rep = calculate_repetition_score(full_text_str, n=self.config.repetition_ngram_size)
        if has_rep:
            doc_errors.append({
                "category": "repetition_loop",
                "message": f"Suspicious repetition loop detected (score={rep_score:.3f})",
            })
            if overall_status == "success":
                overall_status = "partial"

        # Incomplete / Empty check
        if not full_text_str.strip():
            doc_errors.append({"category": "empty_output", "message": "No text extracted from document"})
            overall_status = "failed"

        if len(raw_pages) < max(1, total_expected_pages):
            doc_errors.append({
                "category": "missing_pages",
                "message": f"Extracted {len(raw_pages)} pages, expected {total_expected_pages}",
            })
            if overall_status == "success":
                overall_status = "partial"

        # Build Metrics
        total_chars = len(full_text_str)
        total_words = len(full_text_str.split())
        total_lines = len(full_texts)
        pages_with_content = sum(1 for p in raw_pages if p.texts)
        avg_conf = sum(sum(p.scores) for p in raw_pages) / max(1, sum(len(p.scores) for p in raw_pages))

        pages_count = len(raw_pages)
        pages_per_sec = (pages_count / (wall_ms / 1000.0)) if wall_ms > 0 else 0.0
        chars_per_sec = (total_chars / (wall_ms / 1000.0)) if wall_ms > 0 else 0.0

        metrics = DocumentMetrics(
            document_id=doc_id,
            source_hash=source_sha,
            text=TextMetrics(
                total_chars=total_chars,
                total_words=total_words,
                total_lines=total_lines,
                empty_output=len(full_text_str.strip()) == 0,
                repetition_score=rep_score,
                has_suspicious_repetition=has_rep,
                average_confidence=round(avg_conf, 4),
            ),
            pages=PageMetrics(
                expected_pages=pages_count,
                processed_pages=pages_count,
                missing_pages=0,
                pages_with_content=pages_with_content,
            ),
            structure=StructuralMetrics(
                total_blocks=total_lines,
                total_headings=0,
                total_paragraphs=total_lines,
                total_tables=0,
                total_table_rows=0,
                total_table_cells=0,
                total_references=0,
                reading_order_entries=total_lines,
            ),
            performance=PerformanceMetrics(
                wall_duration_ms=round(wall_ms, 2),
                init_duration_ms=round(self._init_duration_ms, 2),
                inference_duration_ms=round(inference_ms, 2),
                preprocess_duration_ms=round(preprocess_ms, 2),
                postprocess_duration_ms=round(postprocess_ms, 2),
                pages_per_second=round(pages_per_sec, 3),
                chars_per_second=round(chars_per_sec, 2),
                peak_ram_mb=round(peak_rss, 2),
                peak_vram_mb=round(self._get_vram_mb(), 2),
            ),
        )

        metadata = {
            "engine": self.config.engine_name,
            "models": {
                "det": self.config.det_model,
                "cls": self.config.cls_model,
                "rec": self.config.rec_model,
            },
            "render_dpi": self.config.render_dpi,
            "max_image_edge": self.config.max_image_edge,
            "source_path": str(pdf_path),
            "file_size": pdf_path.stat().st_size,
        }

        return DocumentRawOCR(
            document_id=doc_id,
            source_path=str(pdf_path),
            source_sha256=source_sha,
            page_count=pages_count,
            pages=raw_pages,
            full_text=full_text_str,
            full_markdown=full_markdown_str,
            status=overall_status,
            errors=doc_errors,
            metadata=metadata,
            stdout=stdout_buf.getvalue(),
            stderr=stderr_buf.getvalue(),
            metrics=metrics,
        )
