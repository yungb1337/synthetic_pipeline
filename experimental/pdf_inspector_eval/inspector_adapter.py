"""Adapter wrapping all firecrawl/pdf-inspector APIs for experimentation."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pdf_inspector


@dataclass
class PageData:
    page_num: int
    markdown: str
    route: str = "rust_native"
    needs_ocr: bool = False
    ocr_reason: str = ""
    has_tables: bool = False
    has_columns: bool = False
    positioned_items: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class InspectorExtractionResult:
    pdf_path: str
    doc_id: str
    pdf_type: str
    page_count: int
    confidence: float
    has_encoding_issues: bool
    is_complex_layout: bool
    processing_time_ms: float
    pages_needing_ocr: list[int] = field(default_factory=list)
    pages_with_columns: list[int] = field(default_factory=list)
    pages_with_tables: list[int] = field(default_factory=list)
    full_markdown: str = ""
    pages: list[PageData] = field(default_factory=list)
    route_breakdown: dict[str, int] = field(default_factory=dict)
    error: str | None = None


class PDFInspectorAdapter:
    """Provides high-level methods to extract features, layout, and markdown using pdf-inspector."""

    def __init__(self, extract_positions: bool = True):
        self.extract_positions = extract_positions

    def inspect_and_extract(
        self, pdf_path: str | Path, doc_id: str = ""
    ) -> InspectorExtractionResult:
        p_path = str(Path(pdf_path).resolve())
        t0 = time.perf_counter()

        try:
            # 1. Process PDF for overall classification and metadata
            proc_res = pdf_inspector.process_pdf(p_path)

            # 2. Extract per-page markdown and page-level flags
            pages_res = pdf_inspector.extract_pages_markdown(p_path)

            # 3. Optionally extract positioned text items
            positions_by_page: dict[int, list[dict[str, Any]]] = {}
            if self.extract_positions:
                try:
                    raw_pos = pdf_inspector.extract_text_with_positions(p_path)
                    for item in raw_pos:
                        pno = getattr(item, "page", 1)
                        if pno not in positions_by_page:
                            positions_by_page[pno] = []
                        positions_by_page[pno].append(
                            {
                                "text": getattr(item, "text", ""),
                                "x": getattr(item, "x", 0.0),
                                "y": getattr(item, "y", 0.0),
                            }
                        )
                except Exception:
                    pass

            # 4. Construct PageData objects with per-page route tags
            pages: list[PageData] = []
            tables_set = set(getattr(pages_res, "pages_with_tables", []) or [])
            cols_set = set(getattr(pages_res, "pages_with_columns", []) or [])
            ocr_set = set(getattr(pages_res, "pages_needing_ocr", []) or [])

            pdf_type = getattr(proc_res, "pdf_type", "unknown")
            conf = float(getattr(proc_res, "confidence", 1.0))
            has_encoding = bool(getattr(proc_res, "has_encoding_issues", False))
            is_complex = bool(getattr(proc_res, "is_complex_layout", False))
            route_counts = {"rust_native": 0, "cuda_ocr": 0, "docling_heavy": 0}

            for p_obj in getattr(pages_res, "pages", []):
                p_num = getattr(p_obj, "page", 1)
                md = getattr(p_obj, "markdown", "") or ""
                needs_ocr = (
                    getattr(p_obj, "needs_ocr", False)
                    or (p_num in ocr_set)
                    or has_encoding
                    or (pdf_type in ("scanned", "image_based"))
                )
                ocr_reason = getattr(p_obj, "ocr_reason", "") or ""
                has_tables = p_num in tables_set
                has_columns = p_num in cols_set

                if needs_ocr:
                    p_route = "cuda_ocr"
                elif has_tables:
                    p_route = "docling_heavy"
                else:
                    p_route = "rust_native"

                route_counts[p_route] += 1

                pages.append(
                    PageData(
                        page_num=p_num,
                        markdown=md,
                        route=p_route,
                        needs_ocr=needs_ocr,
                        ocr_reason=ocr_reason,
                        has_tables=has_tables,
                        has_columns=has_columns,
                        positioned_items=positions_by_page.get(p_num, []),
                    )
                )

            wall_ms = (time.perf_counter() - t0) * 1000.0

            return InspectorExtractionResult(
                pdf_path=p_path,
                doc_id=doc_id or Path(p_path).stem,
                pdf_type=pdf_type,
                page_count=getattr(proc_res, "page_count", len(pages)),
                confidence=conf,
                has_encoding_issues=has_encoding,
                is_complex_layout=is_complex,
                processing_time_ms=wall_ms,
                pages_needing_ocr=list(
                    getattr(proc_res, "pages_needing_ocr", []) or []
                ),
                pages_with_columns=list(
                    getattr(proc_res, "pages_with_columns", []) or []
                ),
                pages_with_tables=list(
                    getattr(proc_res, "pages_with_tables", []) or []
                ),
                full_markdown=getattr(proc_res, "markdown", "") or "",
                pages=pages,
                route_breakdown=route_counts,
            )

        except Exception as exc:
            wall_ms = (time.perf_counter() - t0) * 1000.0
            return InspectorExtractionResult(
                pdf_path=p_path,
                doc_id=doc_id or Path(p_path).stem,
                pdf_type="error",
                page_count=0,
                confidence=0.0,
                has_encoding_issues=False,
                is_complex_layout=False,
                processing_time_ms=wall_ms,
                error=str(exc),
            )
