"""Strategy factory & pipeline orchestrator for P000-P016 permutations and hybrid cascades.
"""
from __future__ import annotations

import io
import time
import warnings
from typing import Any, Optional
import numpy as np
from PIL import Image
import fitz

warnings.filterwarnings("ignore")

from .adapters.base import BaseTableExtractor, BoundingBox, LayoutRegion, RawTable
from .adapters.docling_adapter import DoclingHeronLayoutDetector, DoclingTableFormerExtractor
from .adapters.pdfplumber_adapter import PDFPlumberTableExtractor
from .adapters.pymupdf_adapter import PyMuPDFTableExtractor
from .adapters.slanet_adapter import SLANetTableExtractor
from .adapters.tatr_adapter import TATRTableExtractor
from .cell_matcher import CellMatcher
from .config import PERMUTATIONS, PermutationSpec


class ExecutionStrategy:
    """Encapsulates layout detection, table extraction, OCR, cell matching, and hybrid routing."""

    def __init__(self, spec: PermutationSpec):
        self.spec = spec
        self.layout_detector: Optional[Any] = None
        self.table_extractor: Optional[BaseTableExtractor] = None
        self.tsr_extractor: Optional[BaseTableExtractor] = None
        self.ocr_engine: Optional[Any] = None

        self._setup_components()

    def _setup_components(self) -> None:
        p_id = self.spec.id

        # Setup Layout Detector
        if self.spec.layout_detector == "docling_heron":
            self.layout_detector = DoclingHeronLayoutDetector(device="cuda" if self.spec.requires_gpu else "cpu")

        # Setup Table Extractor / TSR based on permutation
        if p_id in ("P002", "P003"):
            strat = self.spec.options.get("strategy", "lines")
            self.table_extractor = PyMuPDFTableExtractor(strategy=strat)

        elif p_id == "P004":
            self.table_extractor = PDFPlumberTableExtractor(strategy="lines")

        elif p_id == "P005":
            self.table_extractor = PDFPlumberTableExtractor(strategy="text")

        elif p_id == "P007":
            self.table_extractor = TATRTableExtractor(
                device="cuda" if self.spec.requires_gpu else "cpu", crop_only=False
            )

        elif p_id == "P008":
            self.table_extractor = PyMuPDFTableExtractor(strategy="hybrid")
            self.tsr_extractor = TATRTableExtractor(
                device="cuda" if self.spec.requires_gpu else "cpu", crop_only=True
            )

        elif p_id == "P009":
            self.tsr_extractor = TATRTableExtractor(
                device="cuda" if self.spec.requires_gpu else "cpu", crop_only=True
            )

        elif p_id == "P010":
            self.table_extractor = PyMuPDFTableExtractor(strategy="hybrid")
            self.tsr_extractor = SLANetTableExtractor()

        elif p_id == "P011":
            self.tsr_extractor = SLANetTableExtractor()

        elif p_id == "P012":
            self.tsr_extractor = DoclingTableFormerExtractor(
                mode="fast", device="cuda" if self.spec.requires_gpu else "cpu"
            )

        elif p_id == "P013":
            self.tsr_extractor = DoclingTableFormerExtractor(
                mode="accurate", device="cuda" if self.spec.requires_gpu else "cpu"
            )

        elif p_id == "P014":  # H1: Vector + Neural Cascade
            self.table_extractor = PyMuPDFTableExtractor(strategy="lines")
            self.tsr_extractor = TATRTableExtractor(
                device="cuda" if self.spec.requires_gpu else "cpu", crop_only=True
            )

        elif p_id == "P015":  # H2: Confidence-Gated Geometry Cascade
            self.table_extractor = PyMuPDFTableExtractor(strategy="hybrid")
            self.tsr_extractor = TATRTableExtractor(
                device="cuda" if self.spec.requires_gpu else "cpu", crop_only=True
            )

        elif p_id == "P016":  # H3: Tri-Band Router
            self.table_extractor = PyMuPDFTableExtractor(strategy="hybrid")
            self.tsr_extractor = TATRTableExtractor(
                device="cuda" if self.spec.requires_gpu else "cpu", crop_only=True
            )

    def _ensure_ocr_engine(self) -> None:
        if self.ocr_engine is not None:
            return
        from rapidocr import RapidOCR
        if self.spec.requires_gpu:
            from rapidocr.utils.typings import EngineType
            params = {
                "Det.engine_type": EngineType.TORCH,
                "Cls.engine_type": EngineType.TORCH,
                "Rec.engine_type": EngineType.TORCH,
                "EngineConfig.torch.use_cuda": True,
            }
            self.ocr_engine = RapidOCR(params=params)
        else:
            self.ocr_engine = RapidOCR()

    def process_page(
        self,
        fitz_page: fitz.Page,
        page_index: int,
        pdf_path: str,
        render_dpi: int = 150,
    ) -> dict[str, Any]:
        """Runs the complete page extraction pipeline for this strategy."""
        t_start = time.perf_counter()
        p_id = self.spec.id

        p_w = float(fitz_page.rect.width)
        p_h = float(fitz_page.rect.height)

        # 1. Rasterize page image
        zoom = render_dpi / 72.0
        mat = fitz.Matrix(zoom, zoom)
        pix = fitz_page.get_pixmap(matrix=mat, alpha=False)
        img = Image.open(io.BytesIO(pix.tobytes("png")))
        img_w, img_h = img.size
        scale_x = img_w / p_w if p_w > 0 else 1.0
        scale_y = img_h / p_h if p_h > 0 else 1.0

        # 2. Layout Detection (if enabled)
        layout_regions: list[LayoutRegion] = []
        if self.layout_detector is not None:
            try:
                layout_regions = self.layout_detector.detect_layout(img, page_index)
            except Exception:
                layout_regions = []

        # 3. Table Extraction / TSR
        extracted_tables: list[RawTable] = []

        if p_id == "P001":
            # No table extraction
            extracted_tables = []

        elif p_id in ("P002", "P003"):
            if self.table_extractor:
                extracted_tables = self.table_extractor.extract_tables_from_page(
                    fitz_page, page_index, img, layout_regions
                )

        elif p_id in ("P004", "P005"):
            if self.table_extractor:
                extracted_tables = self.table_extractor.extract_tables_from_page(
                    fitz_page, page_index, img, layout_regions, pdf_path=pdf_path
                )

        elif p_id == "P007":
            if self.table_extractor:
                extracted_tables = self.table_extractor.extract_tables_from_page(
                    fitz_page, page_index, img, layout_regions
                )

        elif p_id in ("P008", "P010"):
            # PyMuPDF detects candidate table bounding boxes -> Neural TSR processes crop
            candidate_tables = self.table_extractor.extract_tables_from_page(
                fitz_page, page_index, img, layout_regions
            ) if self.table_extractor else []
            bboxes = [t.bbox for t in candidate_tables]
            if bboxes and self.tsr_extractor:
                extracted_tables = self.tsr_extractor.extract_tables_from_page(
                    fitz_page, page_index, img, candidate_bboxes=bboxes
                )
            else:
                extracted_tables = candidate_tables

        elif p_id in ("P009", "P011", "P012", "P013"):
            # Heron Layout detects bboxes -> TSR processes crop
            table_bboxes = [r.bbox for r in layout_regions if r.kind == "table"]
            if table_bboxes and self.tsr_extractor:
                extracted_tables = self.tsr_extractor.extract_tables_from_page(
                    fitz_page, page_index, img, candidate_bboxes=table_bboxes
                )

        elif p_id == "P014":
            # H1: Vector lines -> PyMuPDF; if no lines found, try TATR on full page or suspected region
            candidate_tables = self.table_extractor.extract_tables_from_page(
                fitz_page, page_index, img, layout_regions
            ) if self.table_extractor else []
            if candidate_tables:
                extracted_tables = candidate_tables
            elif self.tsr_extractor:
                # Suspected borderless table fallback
                page_text = fitz_page.get_text("text")
                if "Table " in page_text or "TABLE " in page_text:
                    tatr_full = TATRTableExtractor(
                        device="cuda" if self.spec.requires_gpu else "cpu", crop_only=False
                    )
                    extracted_tables = tatr_full.extract_tables_from_page(fitz_page, page_index, img)

        elif p_id == "P015":
            # H2: Confidence-gated cascade
            candidate_tables = self.table_extractor.extract_tables_from_page(
                fitz_page, page_index, img, layout_regions
            ) if self.table_extractor else []
            high_conf = [t for t in candidate_tables if t.confidence >= 0.9]
            low_conf = [t for t in candidate_tables if t.confidence < 0.9]

            extracted_tables.extend(high_conf)
            if low_conf and self.tsr_extractor:
                bboxes = [t.bbox for t in low_conf]
                neural_tables = self.tsr_extractor.extract_tables_from_page(
                    fitz_page, page_index, img, candidate_bboxes=bboxes
                )
                extracted_tables.extend(neural_tables)

        elif p_id == "P016":
            # H3: Tri-Band Router
            candidate_tables = self.table_extractor.extract_tables_from_page(
                fitz_page, page_index, img, layout_regions
            ) if self.table_extractor else []
            extracted_tables = candidate_tables

        # 4. OCR Extraction
        self._ensure_ocr_engine()
        ocr_boxes: list[list[list[float]]] = []
        ocr_texts: list[str] = []
        ocr_scores: list[float] = []

        try:
            img_np = np.array(img)
            if img_np.ndim == 2:
                img_np = np.stack([img_np] * 3, axis=-1)
            elif img_np.shape[2] == 4:
                img_np = img_np[:, :, :3]

            ocr_res = self.ocr_engine(img_np)
            if ocr_res is not None:
                if hasattr(ocr_res, "boxes") and ocr_res.boxes is not None:
                    ocr_boxes = [b.tolist() if hasattr(b, "tolist") else list(b) for b in ocr_res.boxes]
                    ocr_texts = [str(t) for t in (ocr_res.txts or [])]
                    ocr_scores = [float(s) for s in (ocr_res.scores or [])]
                elif isinstance(ocr_res, tuple) and len(ocr_res) >= 3 and ocr_res[0] is not None:
                    ocr_boxes = [b.tolist() if hasattr(b, "tolist") else list(b) for b in ocr_res[0]]
                    ocr_texts = [str(t) for t in (ocr_res[1] or [])]
                    ocr_scores = [float(s) for s in (ocr_res[2] or [])]
        except Exception:
            pass

        # 5. Cell Text Assignment for Neural / BBox tables lacking text
        for tbl in extracted_tables:
            # If table cells have no text or headers are empty, assign from OCR / digital page
            needs_assignment = not tbl.rows or not any(any(c for c in r) for r in tbl.rows)
            if needs_assignment and tbl.cells:
                CellMatcher.assign_ocr_text_to_cells(
                    tbl, ocr_boxes, ocr_texts, ocr_scores, scale_x, scale_y, fitz_page=fitz_page
                )

        # 6. Suppress OCR text inside table bboxes from becoming paragraph blocks
        filtered_boxes, filtered_texts, filtered_scores = CellMatcher.filter_occluded_ocr_blocks(
            ocr_boxes, ocr_texts, ocr_scores, extracted_tables, scale_x, scale_y
        )

        page_duration_ms = (time.perf_counter() - t_start) * 1000.0

        return {
            "page_index": page_index,
            "width": p_w,
            "height": p_h,
            "scale_x": scale_x,
            "scale_y": scale_y,
            "tables": extracted_tables,
            "ocr_boxes": filtered_boxes,
            "ocr_texts": filtered_texts,
            "ocr_scores": filtered_scores,
            "duration_ms": page_duration_ms,
        }
