"""Smart Router using pdf-inspector signals to determine optimal execution path (per-page hybrid).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import pdf_inspector

RouteType = Literal["rust_native", "cuda_ocr", "docling_heavy"]


@dataclass
class PageRoutingResult:
    page_num: int  # 0-based
    route: RouteType
    reason: str
    confidence: float
    needs_ocr: bool
    has_tables: bool
    has_columns: bool


@dataclass
class RoutingDecisionResult:
    route: RouteType
    confidence: float
    reason: str
    pages_needing_ocr: list[int]
    has_encoding_issues: bool
    is_complex_layout: bool
    pdf_type: str
    page_routes: dict[int, PageRoutingResult] = field(default_factory=dict)
    route_breakdown: dict[str, int] = field(default_factory=dict)


class PDFInspectorSmartRouter:
    """Classifies PDF and assigns optimal extraction route per document and per page in < 30ms."""

    def __init__(
        self,
        min_native_confidence: float = 0.70,
        docling_escalation_confidence: float = 0.50,
    ):
        self.min_native_confidence = min_native_confidence
        self.docling_escalation_confidence = docling_escalation_confidence

    def route_document(self, pdf_path: str | Path) -> RoutingDecisionResult:
        """Computes both document-level summary and granular per-page routing decisions."""
        p_path = str(Path(pdf_path).resolve())

        try:
            res = pdf_inspector.process_pdf(p_path)
            pdf_type = getattr(res, "pdf_type", "text_based")
            conf = float(getattr(res, "confidence", 1.0))
            has_encoding_issues = bool(getattr(res, "has_encoding_issues", False))
            is_complex = bool(getattr(res, "is_complex_layout", False))
            pages_ocr = list(getattr(res, "pages_needing_ocr", []) or [])
            page_count = int(getattr(res, "page_count", 1))

            # Fetch page-level signals
            try:
                pages_res = pdf_inspector.extract_pages_markdown(p_path)
                pages_list = getattr(pages_res, "pages", [])
                tables_set = set(getattr(pages_res, "pages_with_tables", []) or [])
                cols_set = set(getattr(pages_res, "pages_with_columns", []) or [])
                ocr_set = set(getattr(pages_res, "pages_needing_ocr", []) or [])
            except Exception:
                pages_list = []
                tables_set = set()
                cols_set = set()
                ocr_set = set(pages_ocr)

            # Route each page individually
            page_routes: dict[int, PageRoutingResult] = {}
            breakdown = {"rust_native": 0, "cuda_ocr": 0, "docling_heavy": 0}

            n_pages = max(page_count, len(pages_list))
            for p_idx in range(n_pages):
                # 1-based page number from inspector vs 0-based index
                p_num_1based = p_idx + 1
                needs_ocr = (p_num_1based in ocr_set) or has_encoding_issues or (pdf_type in ("scanned", "image_based"))
                has_tables = (p_num_1based in tables_set)
                has_columns = (p_num_1based in cols_set)

                # Rule A: Page needs OCR or has broken font CMap -> cuda_ocr
                if needs_ocr:
                    p_route = "cuda_ocr"
                    p_reason = "Page contains scanned images or corrupt font CMap."
                # Rule B: Page has detected table -> docling_heavy TableFormer
                elif has_tables:
                    p_route = "docling_heavy"
                    p_reason = "Page contains tabular layout: escalated to Docling TableFormer."
                # Rule C: Standard clean digital page -> rust_native
                else:
                    p_route = "rust_native"
                    p_reason = "Clean digital text layer with intact CMap."

                page_routes[p_idx] = PageRoutingResult(
                    page_num=p_idx,
                    route=p_route,
                    reason=p_reason,
                    confidence=conf,
                    needs_ocr=needs_ocr,
                    has_tables=has_tables,
                    has_columns=has_columns,
                )
                breakdown[p_route] += 1

            # Overall dominant route classification
            if breakdown["cuda_ocr"] > (n_pages * 0.5):
                primary_route = "cuda_ocr"
                primary_reason = f"Majority of pages ({breakdown['cuda_ocr']}/{n_pages}) require OCR."
            elif breakdown["docling_heavy"] > 0:
                primary_route = "rust_native" if breakdown["rust_native"] >= breakdown["docling_heavy"] else "docling_heavy"
                primary_reason = f"Hybrid per-page execution: {breakdown['rust_native']} native, {breakdown['docling_heavy']} docling pages."
            else:
                primary_route = "rust_native"
                primary_reason = "All pages qualify for high-speed Rust native path."

            return RoutingDecisionResult(
                route=primary_route,
                confidence=conf,
                reason=primary_reason,
                pages_needing_ocr=pages_ocr,
                has_encoding_issues=has_encoding_issues,
                is_complex_layout=is_complex,
                pdf_type=pdf_type,
                page_routes=page_routes,
                route_breakdown=breakdown,
            )

        except Exception as exc:
            return RoutingDecisionResult(
                route="docling_heavy",
                confidence=0.0,
                reason=f"Inspection exception, fallback to heavy engine: {exc}",
                pages_needing_ocr=[],
                has_encoding_issues=False,
                is_complex_layout=True,
                pdf_type="error",
                page_routes={},
                route_breakdown={"rust_native": 0, "cuda_ocr": 0, "docling_heavy": 1},
            )
