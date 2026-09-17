"""Spatial cell text assignment and table block occlusion handling.
"""
from __future__ import annotations

from typing import Any, Optional
import fitz

from .adapters.base import BoundingBox, RawCell, RawTable


class CellMatcher:
    """Matches OCR text lines and digital PDF text to detected table cells and reconstructs 2D grid text."""

    @staticmethod
    def assign_ocr_text_to_cells(
        raw_table: RawTable,
        ocr_boxes: list[list[list[float]]],
        ocr_texts: list[str],
        ocr_scores: list[float],
        scale_x: float = 1.0,
        scale_y: float = 1.0,
        fitz_page: Optional[fitz.Page] = None,
    ) -> RawTable:
        """Assigns OCR text items or digital text to the best matching cells in the table."""
        if not raw_table.cells:
            return raw_table

        # Group cells by row_idx
        row_map: dict[int, list[RawCell]] = {}
        for c in raw_table.cells:
            row_map.setdefault(c.row_idx, []).append(c)

        for r_idx in row_map:
            row_map[r_idx].sort(key=lambda cell: cell.bbox.x0 if cell.bbox else 0.0)

        cell_text_buckets: dict[int, list[str]] = {i: [] for i in range(len(raw_table.cells))}

        # Strategy A: If fitz_page is available with native text, extract text per cell bbox
        used_digital = False
        if fitz_page is not None:
            try:
                for c_idx, cell in enumerate(raw_table.cells):
                    if not cell.bbox:
                        continue
                    rect = fitz.Rect(cell.bbox.x0, cell.bbox.y0, cell.bbox.x1, cell.bbox.y1)
                    cell_text = fitz_page.get_text("text", clip=rect).strip()
                    if cell_text:
                        cell_text_buckets[c_idx].append(cell_text)
                        used_digital = True
            except Exception:
                used_digital = False

        # Strategy B: If digital text was empty or not available, use OCR boxes
        if not used_digital or not any(cell_text_buckets.values()):
            ocr_items = []
            for box, txt, score in zip(ocr_boxes, ocr_texts, ocr_scores):
                if not box or len(box) < 4:
                    continue
                xs = [pt[0] / scale_x for pt in box]
                ys = [pt[1] / scale_y for pt in box]
                cbox = BoundingBox(min(xs), min(ys), max(xs), max(ys))
                ocr_items.append((cbox, txt.strip(), score))

            table_ocr_items = [
                (box, txt, s) for (box, txt, s) in ocr_items
                if raw_table.bbox.intersects(box)
            ]

            for box, txt, _ in table_ocr_items:
                center_x = (box.x0 + box.x1) / 2.0
                center_y = (box.y0 + box.y1) / 2.0

                best_cell_idx = -1
                max_ioa = 0.0

                for c_idx, cell in enumerate(raw_table.cells):
                    if not cell.bbox:
                        continue
                    if cell.bbox.contains_point(center_x, center_y):
                        best_cell_idx = c_idx
                        break
                    ia = cell.bbox.intersection_area(box)
                    box_a = max(1.0, box.width * box.height)
                    ioa = ia / box_a
                    if ioa > max_ioa and ioa > 0.3:
                        max_ioa = ioa
                        best_cell_idx = c_idx

                if best_cell_idx >= 0:
                    cell_text_buckets[best_cell_idx].append(txt)

        # Update cell texts
        for c_idx, cell in enumerate(raw_table.cells):
            bucket_txts = cell_text_buckets[c_idx]
            if bucket_txts:
                cell.text = " ".join(bucket_txts).replace("\n", " ").strip()

        # Reconstruct rows and header
        reconstructed_rows: list[list[str]] = []
        for r_idx in sorted(row_map.keys()):
            r_cells = row_map[r_idx]
            row_str = [c.text for c in r_cells]
            reconstructed_rows.append(row_str)

        if reconstructed_rows:
            raw_table.header = reconstructed_rows[0]
            raw_table.rows = reconstructed_rows[1:] if len(reconstructed_rows) > 1 else []

        return raw_table

    @staticmethod
    def filter_occluded_ocr_blocks(
        ocr_boxes: list[list[list[float]]],
        ocr_texts: list[str],
        ocr_scores: list[float],
        tables: list[RawTable],
        scale_x: float = 1.0,
        scale_y: float = 1.0,
    ) -> tuple[list[list[list[float]]], list[str], list[float]]:
        """Suppresses OCR text lines that fall inside any detected table bbox to prevent duplicate paragraph blocks."""
        if not tables:
            return ocr_boxes, ocr_texts, ocr_scores

        keep_boxes = []
        keep_texts = []
        keep_scores = []

        for box, txt, score in zip(ocr_boxes, ocr_texts, ocr_scores):
            if not box or len(box) < 4:
                continue
            xs = [pt[0] / scale_x for pt in box]
            ys = [pt[1] / scale_y for pt in box]
            center_x = (min(xs) + max(xs)) / 2.0
            center_y = (min(ys) + max(ys)) / 2.0

            # Check if inside any table bbox (with 5pt tolerance inside border)
            in_table = False
            for tbl in tables:
                if tbl.bbox.contains_point(center_x, center_y):
                    in_table = True
                    break

            if not in_table:
                keep_boxes.append(box)
                keep_texts.append(txt)
                keep_scores.append(score)

        return keep_boxes, keep_texts, keep_scores
