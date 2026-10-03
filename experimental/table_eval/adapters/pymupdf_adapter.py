"""PyMuPDF table extraction adapter supporting multiple strategies (lines, text, hybrid)."""

from __future__ import annotations

import re
from typing import Any

import fitz

from .base import BaseTableExtractor, BoundingBox, RawCell, RawTable

_TABLE_CAPTION_RE = re.compile(r"^(?:table|tab\.)\s+[0-9]+[:.]?", re.IGNORECASE)


class PyMuPDFTableExtractor(BaseTableExtractor):
    """Extracts tables from a PyMuPDF page using configurable strategies and heuristics."""

    def __init__(self, strategy: str = "lines", snap_tolerance: float = 3.0):
        self.strategy = strategy
        self.snap_tolerance = snap_tolerance

    def _is_oversplit_table(self, rows: list[list[Any]]) -> bool:
        if not rows:
            return True
        cols = len(rows[0])
        if cols > 25:
            return True
        # Check if >70% of cells are empty
        total = sum(len(r) for r in rows)
        if total == 0:
            return True
        empty = sum(sum(1 for c in r if not str(c or "").strip()) for r in rows)
        return (empty / total) > 0.85

    def extract_tables_from_page(
        self,
        fitz_page: fitz.Page,
        page_index: int,
        page_image: Any | None = None,
        layout_regions: list[LayoutRegion] | None = None,
    ) -> list[RawTable]:
        tables: list[RawTable] = []
        found_mupdf_tables = []

        try:
            if self.strategy == "lines":
                finder = fitz_page.find_tables(
                    strategy="lines", snap_tolerance=self.snap_tolerance
                )
                if finder and finder.tables:
                    found_mupdf_tables = list(finder.tables)

            elif self.strategy == "hybrid":
                # First try explicit lines
                finder = fitz_page.find_tables(
                    strategy="lines", snap_tolerance=self.snap_tolerance
                )
                if finder and finder.tables:
                    found_mupdf_tables = list(finder.tables)

                # Fallback: if lines found nothing, check if page has table captions or whitespace columns
                if not found_mupdf_tables:
                    page_text = fitz_page.get_text("text")
                    if _TABLE_CAPTION_RE.search(page_text) or "Table " in page_text:
                        finder2 = fitz_page.find_tables(
                            horizontal_strategy="lines",
                            vertical_strategy="text",
                            snap_tolerance=self.snap_tolerance,
                        )
                        if finder2 and finder2.tables:
                            found_mupdf_tables = list(finder2.tables)

            elif self.strategy == "text":
                finder = fitz_page.find_tables(
                    horizontal_strategy="text",
                    vertical_strategy="text",
                    snap_tolerance=self.snap_tolerance,
                )
                if finder and finder.tables:
                    found_mupdf_tables = list(finder.tables)

        except Exception:
            found_mupdf_tables = []

        for idx, t in enumerate(found_mupdf_tables):
            try:
                extracted = t.extract()
            except Exception:
                extracted = []

            if (
                not extracted
                or len(extracted) < 2
                or self._is_oversplit_table(extracted)
            ):
                continue

            # Check if table has any non-empty cell
            has_content = any(
                any(str(c or "").strip() for c in row) for row in extracted
            )
            if not has_content:
                continue

            t_bbox = getattr(t, "bbox", None)
            bbox = (
                BoundingBox(
                    x0=float(t_bbox[0]),
                    y0=float(t_bbox[1]),
                    x1=float(t_bbox[2]),
                    y1=float(t_bbox[3]),
                )
                if t_bbox
                else BoundingBox(
                    0, 0, float(fitz_page.rect.width), float(fitz_page.rect.height)
                )
            )

            header = [str(c or "").strip() for c in extracted[0]]
            data_rows = [[str(c or "").strip() for c in r] for r in extracted[1:]]

            # Build raw cells
            raw_cells: list[RawCell] = []
            if hasattr(t, "cells") and t.cells:
                for c in t.cells:
                    try:
                        c_bbox = BoundingBox(
                            float(c[0]), float(c[1]), float(c[2]), float(c[3])
                        )
                        # Fitz cells format may vary or be tuples
                        raw_cells.append(
                            RawCell(
                                row_idx=0,
                                col_idx=0,
                                bbox=c_bbox,
                            )
                        )
                    except Exception:
                        pass

            tables.append(
                RawTable(
                    table_id=f"t-p{page_index}-{idx:02d}",
                    page_index=page_index,
                    bbox=bbox,
                    header=header,
                    rows=data_rows,
                    cells=raw_cells,
                    source=f"pymupdf_{self.strategy}",
                    confidence=0.95 if self.strategy == "lines" else 0.85,
                )
            )

        return tables
