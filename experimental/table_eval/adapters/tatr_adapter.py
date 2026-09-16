"""Microsoft Table Transformer (TATR) adapter for Detection and Structure Recognition.
"""
from __future__ import annotations

import io
from typing import Any, Optional
from PIL import Image
import torch
from transformers import AutoImageProcessor, TableTransformerForObjectDetection

from .base import BaseTableExtractor, BoundingBox, RawCell, RawTable


class TATRTableExtractor(BaseTableExtractor):
    """Runs Microsoft Table Transformer models on full pages or cropped table regions."""

    def __init__(
        self,
        device: str = "cuda",
        crop_only: bool = True,
        det_threshold: float = 0.6,
        struct_threshold: float = 0.5,
    ):
        self.device = device if (torch.cuda.is_available() and device == "cuda") else "cpu"
        self.crop_only = crop_only
        self.det_threshold = det_threshold
        self.struct_threshold = struct_threshold

        self._det_model_name = "microsoft/table-transformer-detection"
        self._tsr_model_name = "microsoft/table-transformer-structure-recognition"

        self._det_processor = None
        self._det_model = None
        self._tsr_processor = None
        self._tsr_model = None

    def _load_models(self) -> None:
        if self._tsr_model is None:
            self._tsr_processor = AutoImageProcessor.from_pretrained(self._tsr_model_name)
            self._tsr_model = TableTransformerForObjectDetection.from_pretrained(self._tsr_model_name).to(self.device)
            self._tsr_model.eval()

        if not self.crop_only and self._det_model is None:
            self._det_processor = AutoImageProcessor.from_pretrained(self._det_model_name)
            self._det_model = TableTransformerForObjectDetection.from_pretrained(self._det_model_name).to(self.device)
            self._det_model.eval()

    def detect_table_bboxes(self, page_image: Image.Image) -> list[BoundingBox]:
        """Detect table bounding boxes on a full-page image using TATR-detection."""
        self._load_models()
        if self._det_model is None:
            return []

        inputs = self._det_processor(images=page_image, return_tensors="pt").to(self.device)
        with torch.no_grad():
            outputs = self._det_model(**inputs)

        target_sizes = torch.tensor([page_image.size[::-1]]).to(self.device)
        results = self._det_processor.post_process_object_detection(
            outputs, threshold=self.det_threshold, target_sizes=target_sizes
        )[0]

        bboxes: list[BoundingBox] = []
        for score, label, box in zip(results["scores"], results["labels"], results["boxes"]):
            lbl_name = self._det_model.config.id2label[label.item()]
            if lbl_name in ("table", "table rotated"):
                b = box.tolist()
                bboxes.append(BoundingBox(x0=b[0], y0=b[1], x1=b[2], y1=b[3]))
        return bboxes

    def recognize_structure(
        self,
        table_crop: Image.Image,
        table_bbox: BoundingBox,
        page_index: int,
        table_idx: int,
    ) -> RawTable:
        """Runs TSR on a cropped table image to extract rows, columns, and spanning cells."""
        self._load_models()
        inputs = self._tsr_processor(images=table_crop, return_tensors="pt").to(self.device)
        with torch.no_grad():
            outputs = self._tsr_model(**inputs)

        target_sizes = torch.tensor([table_crop.size[::-1]]).to(self.device)
        results = self._tsr_processor.post_process_object_detection(
            outputs, threshold=self.struct_threshold, target_sizes=target_sizes
        )[0]

        # Labels:
        # 0: table, 1: table column, 2: table row, 3: table column header,
        # 4: table projected row header, 5: table spanning cell
        rows_boxes = []
        cols_boxes = []
        headers_boxes = []
        spanning_boxes = []

        for score, label, box in zip(results["scores"], results["labels"], results["boxes"]):
            lbl = self._tsr_model.config.id2label[label.item()]
            b = box.tolist()
            cbox = BoundingBox(x0=b[0], y0=b[1], x1=b[2], y1=b[3])
            if lbl == "table row":
                rows_boxes.append((score.item(), cbox))
            elif lbl == "table column":
                cols_boxes.append((score.item(), cbox))
            elif lbl == "table column header":
                headers_boxes.append((score.item(), cbox))
            elif lbl == "table spanning cell":
                spanning_boxes.append((score.item(), cbox))

        # Sort rows top-to-bottom, columns left-to-right
        rows_boxes.sort(key=lambda item: item[1].y0)
        cols_boxes.sort(key=lambda item: item[1].x0)

        # Build grid cells from row/col intersections
        raw_cells: list[RawCell] = []
        for r_idx, (_, r_box) in enumerate(rows_boxes):
            for c_idx, (_, c_box) in enumerate(cols_boxes):
                # Intersection inside crop
                cx0 = max(c_box.x0, 0.0)
                cx1 = min(c_box.x1, table_crop.width)
                cy0 = max(r_box.y0, 0.0)
                cy1 = min(r_box.y1, table_crop.height)

                if cx1 > cx0 and cy1 > cy0:
                    # Map to full page coordinate space
                    page_cx0 = table_bbox.x0 + cx0
                    page_cy0 = table_bbox.y0 + cy0
                    page_cx1 = table_bbox.x0 + cx1
                    page_cy1 = table_bbox.y0 + cy1

                    raw_cells.append(
                        RawCell(
                            row_idx=r_idx,
                            col_idx=c_idx,
                            row_span=1,
                            col_span=1,
                            bbox=BoundingBox(page_cx0, page_cy0, page_cx1, page_cy1),
                        )
                    )

        # Fallback if no rows/columns detected
        if not rows_boxes or not cols_boxes:
            raw_cells.append(
                RawCell(
                    row_idx=0,
                    col_idx=0,
                    bbox=table_bbox,
                )
            )

        return RawTable(
            table_id=f"t-p{page_index}-{table_idx:02d}",
            page_index=page_index,
            bbox=table_bbox,
            header=[],
            rows=[],
            cells=raw_cells,
            source="tatr_tsr",
            confidence=0.90,
        )

    def extract_tables_from_page(
        self,
        fitz_page: Any,
        page_index: int,
        page_image: Optional[Image.Image] = None,
        layout_regions: Optional[list[Any]] = None,
        candidate_bboxes: Optional[list[BoundingBox]] = None,
    ) -> list[RawTable]:
        if page_image is None:
            return []

        # 1. Determine table bounding boxes
        bboxes: list[BoundingBox] = []
        if candidate_bboxes:
            bboxes = candidate_bboxes
        elif not self.crop_only:
            bboxes = self.detect_table_bboxes(page_image)
        elif layout_regions:
            for reg in layout_regions:
                if getattr(reg, "kind", "") == "table":
                    bboxes.append(reg.bbox)

        tables: list[RawTable] = []
        img_w, img_h = page_image.size
        pdf_w, pdf_h = float(fitz_page.rect.width), float(fitz_page.rect.height)
        scale_x = img_w / pdf_w if pdf_w > 0 else 1.0
        scale_y = img_h / pdf_h if pdf_h > 0 else 1.0

        for t_idx, bbox in enumerate(bboxes):
            # Crop image (with 5px padding)
            px0 = max(0, int(bbox.x0 * scale_x) - 5)
            py0 = max(0, int(bbox.y0 * scale_y) - 5)
            px1 = min(img_w, int(bbox.x1 * scale_x) + 5)
            py1 = min(img_h, int(bbox.y1 * scale_y) + 5)

            if px1 <= px0 or py1 <= py0:
                continue

            crop = page_image.crop((px0, py0, px1, py1))
            raw_tbl = self.recognize_structure(crop, bbox, page_index, t_idx)
            tables.append(raw_tbl)

        return tables
