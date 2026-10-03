"""pdfplumber table extraction adapter supporting line-based and text-based heuristics."""

from __future__ import annotations

from typing import Any

import pdfplumber

from .base import BaseTableExtractor, BoundingBox, RawCell, RawTable


class PDFPlumberTableExtractor(BaseTableExtractor):
    """Extracts tables from PDF pages using pdfplumber line/text settings."""

    def __init__(
        self,
        strategy: str = "lines",  # "lines" or "text"
        snap_tolerance: float = 3.0,
        join_tolerance: float = 3.0,
    ):
        self.strategy = strategy
        self.snap_tolerance = snap_tolerance
        self.join_tolerance = join_tolerance

    def extract_tables_from_page(
        self,
        fitz_page: Any,
        page_index: int,
        page_image: Any | None = None,
        layout_regions: list[Any] | None = None,
        pdf_path: str | None = None,
    ) -> list[RawTable]:
        tables: list[RawTable] = []
        if not pdf_path:
            return tables

        try:
            with pdfplumber.open(pdf_path) as pdf:
                if page_index >= len(pdf.pages):
                    return tables
                page = pdf.pages[page_index]

                table_settings = {
                    "vertical_strategy": "lines"
                    if self.strategy == "lines"
                    else "text",
                    "horizontal_strategy": "lines"
                    if self.strategy == "lines"
                    else "text",
                    "snap_tolerance": self.snap_tolerance,
                    "join_tolerance": self.join_tolerance,
                }

                found_tables = page.find_tables(table_settings=table_settings)

                for idx, t in enumerate(found_tables):
                    extracted = t.extract()
                    if not extracted or len(extracted) < 2:
                        continue

                    # Filter out ghost / empty tables
                    has_content = any(
                        any(str(c or "").strip() for c in r) for r in extracted
                    )
                    if not has_content:
                        continue

                    t_bbox = t.bbox  # (x0, top, x1, bottom)
                    bbox = BoundingBox(
                        x0=float(t_bbox[0]),
                        y0=float(t_bbox[1]),
                        x1=float(t_bbox[2]),
                        y1=float(t_bbox[3]),
                    )

                    header = [str(c or "").strip() for c in extracted[0]]
                    rows = [[str(c or "").strip() for c in r] for r in extracted[1:]]

                    # Extract cells if available
                    raw_cells: list[RawCell] = []
                    for c_idx, cell_box in enumerate(t.cells):
                        raw_cells.append(
                            RawCell(
                                row_idx=0,
                                col_idx=0,
                                bbox=BoundingBox(
                                    x0=float(cell_box[0]),
                                    y0=float(cell_box[1]),
                                    x1=float(cell_box[2]),
                                    y1=float(cell_box[3]),
                                ),
                            )
                        )

                    tables.append(
                        RawTable(
                            table_id=f"t-p{page_index}-{idx:02d}",
                            page_index=page_index,
                            bbox=bbox,
                            header=header,
                            rows=rows,
                            cells=raw_cells,
                            source=f"pdfplumber_{self.strategy}",
                            confidence=0.90 if self.strategy == "lines" else 0.80,
                        )
                    )
        except Exception:
            pass

        return tables
