"""Recovered parts: the format-agnostic result of a loader.

`RecoveredDocument` is the contract between format loaders (which know
about PDFs/Word/CSV...) and the DOM builder (which knows about nothing but
this generic representation). This is the seam that keeps format parsers
interchangeable (SYN4: "every parser produces the same Document object").
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:  # leaf import only for type hints; no runtime coupling
    from app.routing.schema import RoutingDecision


@dataclass
class RecoveredBlock:
    page: int = 0
    kind: str = "paragraph"
    text: str = ""
    bbox: Optional[tuple[float, float, float, float]] = None   # x0,y0,x1,y1
    seq: int = 0
    confidence: float = 1.0
    font_size: Optional[float] = None
    bold: Optional[bool] = None
    source: str = "text"          # "text" | "ocr" | "markup" | "spreadsheet" | ...
    ocr_engine: Optional[str] = None


@dataclass
class RecoveredTable:
    page: int = 0
    bbox: Optional[tuple[float, float, float, float]] = None
    header: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)   # values only (strings)
    source: str = "native"
    confidence: float = 1.0
    # Caption/title text associated with the table by the upstream extractor
    # (e.g. Docling's caption refs), or the table's own full-width title row when
    # no explicit caption is available. Preserved (never fused into the header).
    caption: str = ""
    # Column x-positions (left edges) from the upstream header cells, plus the
    # header/body y-extent — used by the evidence-graph row reconstruction when
    # the upstream table structure collapsed. Internal intermediate detail;
    # not serialized to the DOM.
    column_starts: list[float] = field(default_factory=list)
    header_bottom: float = 0.0
    body_bottom: float = 0.0
    # Cell + row geometry (D5). `cell_bboxes` is aligned to `rows`:
    # cell_bboxes[r][c] holds the cell's bbox tuple or None. `row_bboxes[r]` is
    # the row's bbox or None. Sourced from Docling (TOPLEFT, no origin flip);
    # never fabricated — positions absent upstream stay None (faithful).
    cell_bboxes: list[list[Optional[tuple[float, float, float, float]]]] = field(
        default_factory=list)
    row_bboxes: list[Optional[tuple[float, float, float, float]]] = field(
        default_factory=list)


@dataclass
class RecoveredImage:
    page: int = 0
    bbox: Optional[tuple[float, float, float, float]] = None
    storage_ref: str = ""   # filled by orchestrator after persisting `blob`
    mime: str = ""
    checksum: str = ""
    caption: str = ""
    blob: bytes = b""       # raw image bytes for the Store to persist


@dataclass
class RecoveredAnnotation:
    page: int = 0
    kind: str = "note"
    text: str = ""


@dataclass
class RecoveredDocument:
    """What a loader returns; mapped to DOM by the builder."""
    detected_type: str = ""
    mime: str = ""
    declared_extension: str = ""
    probe: str = ""
    title: str = ""
    author: str = ""
    creator: str = ""
    producer: str = ""
    subject: str = ""
    created: str = ""
    modified: str = ""
    language: str = ""
    page_count: int = 0
    page_sizes: dict = field(default_factory=dict)   # page -> (w,h)
    blocks: list[RecoveredBlock] = field(default_factory=list)
    tables: list[RecoveredTable] = field(default_factory=list)
    images: list[RecoveredImage] = field(default_factory=list)
    annotations: list[RecoveredAnnotation] = field(default_factory=list)
    references: list[tuple[str, str]] = field(default_factory=list)  # (kind,target)
    # ADR-007: when True, `blocks` are already in final reading order (e.g. Docling's
    # iterate_items order); the DOM builder must NOT re-run the heuristic ROG on them.
    reading_order_authoritative: bool = False
    # ADR-007: engine/model identity for provenance + idempotent re-parse stability.
    docling_version: Optional[str] = None
    layout_model: Optional[str] = None
    # ADR-011: the router's decision for this document (additive; None when
    # routing didn't run, e.g. manual native/docling or non-PDF).
    routing: Optional["RoutingDecision"] = None
    # Performance metrics accumulated by the loaders (wall-clock ms): e.g.
    # "docling_ms", "docling_map_ms", "table_reconstruct_ms", "ocr_ms",
    # "ocr_pages". Internal observability; NOT serialized to the DOM.
    timings: dict = field(default_factory=dict)