"""Base interfaces and shared data classes for Table and Layout adapters."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class BoundingBox:
    """Standard bounding box (x0, y0, x1, y1) in points or pixels."""

    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def width(self) -> float:
        return max(0.0, self.x1 - self.x0)

    @property
    def height(self) -> float:
        return max(0.0, self.y1 - self.y0)

    def intersects(self, other: BoundingBox) -> bool:
        return not (
            self.x1 <= other.x0
            or self.x0 >= other.x1
            or self.y1 <= other.y0
            or self.y0 >= other.y1
        )

    def intersection_area(self, other: BoundingBox) -> float:
        ix0 = max(self.x0, other.x0)
        iy0 = max(self.y0, other.y0)
        ix1 = min(self.x1, other.x1)
        iy1 = min(self.y1, other.y1)
        if ix1 <= ix0 or iy1 <= iy0:
            return 0.0
        return (ix1 - ix0) * (iy1 - iy0)

    def contains_point(self, x: float, y: float) -> bool:
        return self.x0 <= x <= self.x1 and self.y0 <= y <= self.y1


@dataclass
class RawCell:
    """A single cell inside a detected table grid."""

    row_idx: int
    col_idx: int
    row_span: int = 1
    col_span: int = 1
    bbox: BoundingBox | None = None
    text: str = ""
    confidence: float = 1.0


@dataclass
class RawTable:
    """A detected table with bounding box, cells, and reconstructed rows/columns."""

    table_id: str
    page_index: int
    bbox: BoundingBox
    header: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)
    cells: list[RawCell] = field(default_factory=list)
    caption: str = ""
    source: str = "native"
    confidence: float = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class LayoutRegion:
    """A region detected on a page by a layout model."""

    region_id: str
    page_index: int
    kind: str  # "table", "text", "heading", "figure", "header", "footer"
    bbox: BoundingBox
    confidence: float = 1.0
    text: str = ""


class BaseTableExtractor:
    """Base interface for all table extraction adapters."""

    def is_available(self) -> bool:
        return True

    def extract_tables_from_page(
        self,
        fitz_page: Any,
        page_index: int,
        page_image: Any | None = None,
        layout_regions: list[LayoutRegion] | None = None,
    ) -> list[RawTable]:
        raise NotImplementedError
