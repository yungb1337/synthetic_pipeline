"""Docling Heron layout and TableFormer FAST / ACCURATE isolated crop adapters.
"""
from __future__ import annotations

from typing import Any, Optional
from PIL import Image
import torch

from .base import BaseTableExtractor, BoundingBox, LayoutRegion, RawCell, RawTable


class DoclingHeronLayoutDetector:
    """Runs Docling Heron layout detection model to identify tables and layout regions."""

    def __init__(self, device: str = "cuda"):
        self.device = device if (torch.cuda.is_available() and device == "cuda") else "cpu"
        self._model = None

    def _ensure_loaded(self) -> None:
        if self._model is None:
            from docling.models.stages.layout.layout_model import LayoutModel
            from docling.datamodel.pipeline_options import LayoutOptions, AcceleratorOptions

            acc_opts = AcceleratorOptions(device=self.device)
            layout_opts = LayoutOptions()
            self._model = LayoutModel(artifacts_path=None, accelerator_options=acc_opts, options=layout_opts)

    def detect_layout(self, page_image: Image.Image, page_index: int) -> list[LayoutRegion]:
        self._ensure_loaded()
        if self._model is None:
            return []

        # Convert to Docling Page / Image structure if required or use model predictor
        regions: list[LayoutRegion] = []
        try:
            # LayoutModel produces predictions on image
            preds = self._model.predict(page_image)
            for idx, item in enumerate(preds):
                lbl = getattr(item, "label", "text").lower()
                bbox_raw = getattr(item, "bbox", None)
                if bbox_raw:
                    bbox = BoundingBox(
                        x0=float(bbox_raw.l),
                        y0=float(bbox_raw.t),
                        x1=float(bbox_raw.r),
                        y1=float(bbox_raw.b),
                    )
                    regions.append(
                        LayoutRegion(
                            region_id=f"reg-p{page_index}-{idx:02d}",
                            page_index=page_index,
                            kind="table" if "table" in lbl else lbl,
                            bbox=bbox,
                            confidence=float(getattr(item, "confidence", 1.0)),
                        )
                    )
        except Exception:
            pass

        return regions


class DoclingTableFormerExtractor(BaseTableExtractor):
    """Runs Docling TableFormer (FAST or ACCURATE) on cropped table regions."""

    def __init__(self, mode: str = "fast", device: str = "cuda"):
        self.mode_str = mode
        self.device = device if (torch.cuda.is_available() and device == "cuda") else "cpu"
        self._model = None

    def _ensure_loaded(self) -> None:
        if self._model is None:
            from docling.models.stages.table_structure.table_structure_model import TableStructureModel
            from docling.datamodel.pipeline_options import TableStructureOptions, TableFormerMode, AcceleratorOptions

            mode = TableFormerMode.FAST if self.mode_str == "fast" else TableFormerMode.ACCURATE
            opts = TableStructureOptions(mode=mode, do_cell_matching=False)
            acc_opts = AcceleratorOptions(device=self.device)
            self._model = TableStructureModel(
                enabled=True,
                artifacts_path=None,
                options=opts,
                accelerator_options=acc_opts,
            )

    def extract_tables_from_page(
        self,
        fitz_page: Any,
        page_index: int,
        page_image: Optional[Image.Image] = None,
        layout_regions: Optional[list[LayoutRegion]] = None,
        candidate_bboxes: Optional[list[BoundingBox]] = None,
    ) -> list[RawTable]:
        if page_image is None:
            return []

        self._ensure_loaded()
        if self._model is None:
            return []

        bboxes: list[BoundingBox] = []
        if candidate_bboxes:
            bboxes = candidate_bboxes
        elif layout_regions:
            for reg in layout_regions:
                if reg.kind == "table":
                    bboxes.append(reg.bbox)

        tables: list[RawTable] = []
        img_w, img_h = page_image.size
        pdf_w, pdf_h = float(fitz_page.rect.width), float(fitz_page.rect.height)
        scale_x = img_w / pdf_w if pdf_w > 0 else 1.0
        scale_y = img_h / pdf_h if pdf_h > 0 else 1.0

        for t_idx, bbox in enumerate(bboxes):
            px0 = max(0, int(bbox.x0 * scale_x) - 5)
            py0 = max(0, int(bbox.y0 * scale_y) - 5)
            px1 = min(img_w, int(bbox.x1 * scale_x) + 5)
            py1 = min(img_h, int(bbox.y1 * scale_y) + 5)

            if px1 <= px0 or py1 <= py0:
                continue

            crop = page_image.crop((px0, py0, px1, py1))
            try:
                # Predict on crop
                res = self._model.predict(crop)
                # Parse structure output
                header = []
                rows = []
                raw_cells: list[RawCell] = []
                if hasattr(res, "table_rows"):
                    for r_idx, r in enumerate(res.table_rows):
                        row_txts = []
                        for c_idx, c in enumerate(r.cells):
                            txt = getattr(c, "text", "")
                            row_txts.append(txt)
                            c_box = getattr(c, "bbox", None)
                            if c_box:
                                raw_cells.append(
                                    RawCell(
                                        row_idx=r_idx,
                                        col_idx=c_idx,
                                        bbox=BoundingBox(
                                            bbox.x0 + (c_box.l / scale_x),
                                            bbox.y0 + (c_box.t / scale_y),
                                            bbox.x0 + (c_box.r / scale_x),
                                            bbox.y0 + (c_box.b / scale_y),
                                        ),
                                        text=txt,
                                    )
                                )
                        if r_idx == 0:
                            header = row_txts
                        else:
                            rows.append(row_txts)

                tables.append(
                    RawTable(
                        table_id=f"t-p{page_index}-{t_idx:02d}",
                        page_index=page_index,
                        bbox=bbox,
                        header=header,
                        rows=rows,
                        cells=raw_cells,
                        source=f"docling_tableformer_{self.mode_str}",
                        confidence=0.95,
                    )
                )
            except Exception:
                pass

        return tables
