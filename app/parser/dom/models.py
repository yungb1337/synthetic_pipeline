"""DOM object model. One canonical representation for every file format.

This model is the single source of truth downstream modules (normalization,
chunking, KG) consume. It must be parser-independent, so keep it lean and
faithful: unknown values become `None`, never fabricated text.
"""

from __future__ import annotations

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
    bbox: BBox | None = None
    page: int = 0
    confidence: float = 1.0
    font_size: float | None = None
    bold: bool | None = None
    # provenance of this block
    source: str = "text"  # "text" | "ocr" | "markup" | "spreadsheet" | ...
    ocr_engine: str | None = None


class Cell(BaseModel):
    text: str = ""
    bbox: BBox | None = None


class Row(BaseModel):
    cells: list[Cell] = Field(default_factory=list)
    bbox: BBox | None = None  # row-level geometry, when available (D5)


class Table(BaseModel):
    id: str = ""
    page: int = 0
    bbox: BBox | None = None
    header: list[str] = Field(default_factory=list)
    rows: list[Row] = Field(default_factory=list)
    source: str = "native"  # "native" | "ocr" | "heuristic"
    confidence: float = 1.0
    # Caption/title text associated with the table (from source structure, e.g.
    # Docling caption refs or a full-width title row). Kept separate from the
    # header so caption text never becomes column names. Additive: absent in
    # older DOMs.
    caption: str = ""


class ImageObject(BaseModel):
    id: str = ""
    page: int = 0
    bbox: BBox | None = None
    storage_ref: str = ""  # path/key into the Store
    mime: str = ""
    checksum: str = ""
    caption: str = ""


class Annotation(BaseModel):
    kind: str = ""  # "note" | "highlight" | "stamp" | "link" | ...
    text: str = ""
    page: int = 0


class Page(BaseModel):
    index: int
    width: float | None = None
    height: float | None = None
    blocks: list[Block] = Field(default_factory=list)
    tables: list[Table] = Field(default_factory=list)
    images: list[ImageObject] = Field(default_factory=list)
    annotations: list[Annotation] = Field(default_factory=list)


class Reference(BaseModel):
    """A reference/link or citation target captured from source."""

    kind: str = "link"  # "link" | "citation" | "ident"
    target: str = ""
    # Additive structured fields (extraction-quality run): a reference has a
    # stable id, a human label (e.g. "[33]"), and full text. Empty by default so
    # pre-existing DOMs stay valid.
    id: str = ""
    label: str = ""
    text: str = ""


class ReadingOrderEntry(BaseModel):
    """One semantic unit in the canonical reading sequence.

    Extends (does NOT replace) `Document.reading_order` (which keeps the
    block-id chain for the chunker). `reading_order_full` carries the COMPLETE
    sequence — blocks, tables, and images in canonical order — so every content
    unit is represented exactly once (investigation D4). Reuses existing ids.
    """

    type: str = "block"  # "block" | "table" | "image"
    id: str = ""


class Metadata(BaseModel):
    mime: str = ""
    detected_type: str = ""  # our canonical type slug, e.g. "pdf", "csv"
    declared_extension: str = ""  # the misleading/derived extension
    probe: str = ""  # which detect signal won ("magic","container",...)
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
    ocr_engine: str | None = None
    oct_level: bool = False
    # ADR-007: Docling backend identity (present only when the Docling path ran).
    docling_version: str | None = None
    layout_model: str | None = None
    config: dict = Field(default_factory=dict)
    # ADR-011: the intelligent router's decision (present only when the auto
    # route ran); None for manual-native/docling and pre-router DOMs (additive).
    routing: RoutingDecision | None = None
    # --- normalization stage (Module #2) ---
    normalizer_version: str | None = None
    normalization_report: dict | None = None


class Region(BaseModel):
    """Geometric partition of a page (column / sidebar / footnote / header).

    Additive field — absent in older DOMs, consumers that don't need
    regions ignore this field entirely.
    """

    id: str = ""
    page: int = 0
    bbox: BBox | None = None
    kind: str = "column"  # "column" | "sidebar" | "footnote" | "header"
    block_ids: list[str] = Field(default_factory=list)


class Document(BaseModel):
    """The canonical output of the Parser module."""

    version: str
    document_id: str
    source_hash: str
    metadata: Metadata = Field(default_factory=Metadata)
    provenance: Provenance | None = None
    reading_order: list[str] = Field(default_factory=list)  # chain of block ids
    # Complete typed reading sequence (blocks + tables + images); additive for
    # D4. Every semantic content unit appears exactly once. The chunker still
    # reads `reading_order` (block ids) — this field is the full superset.
    reading_order_full: list[ReadingOrderEntry] = Field(default_factory=list)
    pages: list[Page] = Field(default_factory=list)
    references: list[Reference] = Field(default_factory=list)
    # n -> reference id, for body `[n]` markers (D3). Additive.
    citation_index: dict[str, str] = Field(default_factory=dict)
    # Region partition per page (column / sidebar / footnote / header).
    # Additive — absent in older DOMs, consumers that don't need regions
    # ignore this field.
    regions: list[Region] = Field(default_factory=list)

    # aggregate counts for monitoring/validate
    def num_blocks(self) -> int:
        return sum(len(p.blocks) for p in self.pages)

    def num_tables(self) -> int:
        return sum(len(p.tables) for p in self.pages)

    def num_images(self) -> int:
        return sum(len(p.images) for p in self.pages)
