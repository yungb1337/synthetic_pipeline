"""DOM object model. One canonical representation for every file format.

This model is the single source of truth downstream modules (normalization,
chunking, KG) consume. It must be parser-independent, so keep it lean and
faithful: unknown values become `None`, never fabricated text.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

# Leaf import (app.routing.schema imports only pydantic) so the canonical DOM
# can carry the routing decision with no parser->router coupling beyond a plain
# type reference (architecture §2; ADR-011 §9). Optional => old DOMs keep valid
# (routing=None).
from app.routing.schema import RoutingDecision


class BBox(BaseModel):
    """Coordinates in source space (PDF points by default). Nullable per node."""
    x0: float
    y0: float
    x1: float
    y1: float


class Block(BaseModel):
    """A text-bearing region (spatial or logical) of the document."""
    id: str
    kind: str = "paragraph"
    text: str = ""
    bbox: Optional[BBox] = None
    page: int = 0
    confidence: float = 1.0
    font_size: Optional[float] = None
    bold: Optional[bool] = None
    # provenance of this block
    source: str = "text"           # "text" | "ocr" | "markup" | "spreadsheet" | ...
    ocr_engine: Optional[str] = None


class Cell(BaseModel):
    text: str = ""
    bbox: Optional[BBox] = None


class Row(BaseModel):
    cells: list[Cell] = Field(default_factory=list)


class Table(BaseModel):
    id: str = ""
    page: int = 0
    bbox: Optional[BBox] = None
    header: list[str] = Field(default_factory=list)
    rows: list[Row] = Field(default_factory=list)
    source: str = "native"   # "native" | "ocr" | "heuristic"
    confidence: float = 1.0


class ImageObject(BaseModel):
    id: str = ""
    page: int = 0
    bbox: Optional[BBox] = None
    storage_ref: str = ""       # path/key into the Store
    mime: str = ""
    checksum: str = ""
    caption: str = ""


class Annotation(BaseModel):
    kind: str = ""   # "note" | "highlight" | "stamp" | "link" | ...
    text: str = ""
    page: int = 0


class Page(BaseModel):
    index: int
    width: Optional[float] = None
    height: Optional[float] = None
    blocks: list[Block] = Field(default_factory=list)
    tables: list[Table] = Field(default_factory=list)
    images: list[ImageObject] = Field(default_factory=list)
    annotations: list[Annotation] = Field(default_factory=list)


class Reference(BaseModel):
    """A reference/link or citation target captured from source."""
    kind: str = "link"          # "link" | "citation" | "ident"
    target: str = ""


class Metadata(BaseModel):
    mime: str = ""
    detected_type: str = ""        # our canonical type slug, e.g. "pdf", "csv"
    declared_extension: str = ""   # the misleading/derived extension
    probe: str = ""                # which detect signal won ("magic","container",...)
    title: str = ""
    author: str = ""
    creator: str = ""
    producer: str = ""
    subject: str = ""
    created: str = ""
    modified: str = ""
    language: str = ""
    page_count: int = 0


class Provenance(BaseModel):
    parser_version: str
    dom_schema_version: str
    ocr_engine: Optional[str] = None
    oct_level: bool = False
    # ADR-007: Docling backend identity (present only when the Docling path ran).
    docling_version: Optional[str] = None
    layout_model: Optional[str] = None
    config: dict = Field(default_factory=dict)
    # ADR-011: the intelligent router's decision (present only when the auto
    # route ran); None for manual-native/docling and pre-router DOMs (additive).
    routing: Optional[RoutingDecision] = None
    # --- normalization stage (Module #2) ---
    normalizer_version: Optional[str] = None
    normalization_report: Optional[dict] = None


class Document(BaseModel):
    """The canonical output of the Parser module."""
    version: str
    document_id: str
    source_hash: str
    metadata: Metadata = Field(default_factory=Metadata)
    provenance: Optional[Provenance] = None
    reading_order: list[str] = Field(default_factory=list)  # chain of block ids
    pages: list[Page] = Field(default_factory=list)
    references: list[Reference] = Field(default_factory=list)

    # aggregate counts for monitoring/validate
    def num_blocks(self) -> int:
        return sum(len(p.blocks) for p in self.pages)

    def num_tables(self) -> int:
        return sum(len(p.tables) for p in self.pages)

    def num_images(self) -> int:
        return sum(len(p.images) for p in self.pages)