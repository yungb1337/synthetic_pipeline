"""Build a canonical Document DOM from recovered parts.

Responsibilities:
  * Map RecoveredDocument -> Document (pydantic) deterministically.
  * Recover global reading order over the loader's blocks (which carry .bbox
    and .seq), then materialize it as an ordered chain of block ids.
  * Assign stable block ids.
  * Compute source-hash for idempotency + lineage.

Stage boundary (§1.3 fix #1):
  Stage 1 (physical/layout): objects, bboxes, reading order, cells, images.
  Stage 2 (logical/semantic): references, reading_order_full, entities, footnotes.
  Orchestrated via SemanticContext — no implicit global state.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .._pdfmeta import clean_meta_string
from ..config import ParserConfig
from ..parts import RecoveredDocument
from . import reading_order
from .models import (
    Annotation,
    BBox,
    Block,
    Cell,
    Document,
    ImageObject,
    Metadata,
    Page,
    Provenance,
    Reference,
    Region,
    Row,
    Table,
)


@dataclass
class SemanticContext:
    """Stage 1 output passed into Stage 2.

    Immutable after construction — Stage 2 methods mutate this in place
    (populate Document fields) so the builder retains full control.
    """

    document_id: str = ""
    pages: dict[int, Page] = field(default_factory=dict)
    reading_order: list[str] = field(default_factory=list)
    regions: list[Region] = field(default_factory=list)  # Stage 2
    references: list[Reference] = field(default_factory=list)  # Stage 2 output
    citation_index: dict[str, str] = field(default_factory=dict)  # Stage 2 output


def _bbox(t: tuple | None) -> BBox | None:
    if t is None:
        return None
    x0, y0, x1, y1 = t
    return BBox(x0=x0, y0=y0, x1=x1, y1=y1)


class DocumentBuilder:
    def __init__(self, config: ParserConfig) -> None:
        self.config = config

    def build(
        self, recovered: RecoveredDocument, document_id: str, sha256: str
    ) -> Document:
        """Stage 1 + Stage 2 assembly.

        Stage 1 (physical/layout): objects, bboxes, reading order, cells, images.
        Stage 2 (logical/semantic): references, reading_order_full, regions, entities.
        """
        # ---- Stage 1: physical/layout ----
        pages: dict[int, Page] = {}
        ordered = reading_order.recover_reading_order(recovered.blocks)
        # Assign stable block ids: doc-level block counter per page,
        # same convention as the original builder (no id on RecoveredBlock).
        block_seq: dict[int, int] = {}
        for b in ordered:
            block_seq.setdefault(b.page, 0)
            b.id = f"{document_id}/b{b.page:02d}_{block_seq[b.page]:04d}"
            block_seq[b.page] += 1
        chain = [b.id for b in ordered]

        for b in ordered:
            p = pages.setdefault(b.page, Page(index=b.page, blocks=[]))
            p.blocks.append(
                Block(
                    id=b.id,
                    kind=b.kind,
                    text=b.text,
                    bbox=_bbox(b.bbox),
                    page=b.page,
                    confidence=b.confidence,
                    font_size=b.font_size,
                    bold=b.bold,
                    source=b.source,
                    ocr_engine=b.ocr_engine,
                )
            )

        for t in recovered.tables:
            p = pages.setdefault(t.page, Page(index=t.page, blocks=[]))
            _rows = [
                Row(
                    cells=[
                        Cell(text=c) for c in (r.cells if hasattr(r, "cells") else r)
                    ],
                    bbox=_bbox(r.bbox) if hasattr(r, "bbox") and r.bbox else None,
                )
                for r in t.rows
            ]
            p.tables.append(
                Table(
                    id=f"{document_id}/t{len(p.tables)}_{t.page}",
                    page=t.page,
                    bbox=_bbox(t.bbox),
                    header=t.header,
                    rows=_rows,
                    source=t.source,
                    confidence=t.confidence,
                    caption=t.caption,
                )
            )
        for img in recovered.images:
            p = pages.setdefault(img.page, Page(index=img.page, blocks=[]))
            p.images.append(
                ImageObject(
                    id=f"{document_id}/i{len(p.images)}_{img.page}",
                    page=img.page,
                    bbox=_bbox(img.bbox),
                    storage_ref=img.storage_ref,
                    mime=img.mime,
                    checksum=img.checksum,
                    caption=img.caption,
                )
            )
        for ann in recovered.annotations:
            p = pages.setdefault(ann.page, Page(index=ann.page, blocks=[]))
            p.annotations.append(
                Annotation(kind=ann.kind, text=ann.text, page=ann.page)
            )

        # D9: guarantee a Page object for EVERY page in the expected set, even
        # when the page carries no content in the folded RecoveredDocument. A
        # continuation page whose only content was a table fragment gets that
        # fragment's rows merged into the parent table (normalize_tables), leaving
        # it content-less here; without an explicit Page it would be SILENTLY
        # dropped from the canonical DOM even though the assembler counted it as
        # assembled — exactly the "page 8 missing" defect. Emitting an empty Page
        # preserves page order/density and makes zero-silent-loss true end-to-end.
        # Generic: keyed off the source page count and the page-index convention
        # actually used by the content (native is 0-based, docling 1-based). Never
        # a specific page number.
        if recovered.page_count:
            observed = set(pages.keys())
            base = 0 if observed and min(observed) == 0 else 1
            for idx in range(base, recovered.page_count + base):
                pages.setdefault(idx, Page(index=idx, blocks=[]))

        # page sizes: per-producer keys land here exactly (native 0-based,
        # docling 1-based). For any page still missing dims (e.g. a docling page
        # absent from `doc.pages`), fall back to the document-wide median size so
        # no page is emitted with null geometry (D6). No fabricated per-page values.
        known = [v for v in recovered.page_sizes.values() if v and v[0] and v[1]]
        med = sorted(known)[len(known) // 2] if known else None
        for idx, pg in pages.items():
            if (pg.width is None or pg.height is None) and idx in recovered.page_sizes:
                w, h = recovered.page_sizes[idx]
                pg.width = pg.width or w
                pg.height = pg.height or h
            if (pg.width is None or pg.height is None) and med is not None:
                pg.width, pg.height = med
            # A page with absolutely no known geometry keeps None by design; this
            # only happens for a structurally-empty page, which is not a parse loss.

        # ---- Stage 2: logical/semantic ----
        semantic_ctx = SemanticContext(
            document_id=document_id,
            pages=pages,
            reading_order=chain,
        )
        self._extract_references(semantic_ctx, recovered)
        self._build_reading_order_full(semantic_ctx)
        self._build_regions(semantic_ctx)

        metadata = Metadata(
            mime=recovered.mime,
            detected_type=recovered.detected_type,
            declared_extension=recovered.declared_extension,
            probe=recovered.probe,
            title=clean_meta_string(recovered.title or ""),
            author=clean_meta_string(recovered.author or ""),
            creator=clean_meta_string(recovered.creator or ""),
            producer=clean_meta_string(recovered.producer or ""),
            subject=clean_meta_string(recovered.subject or ""),
            created=clean_meta_string(recovered.created or ""),
            modified=clean_meta_string(recovered.modified or ""),
            language=clean_meta_string(recovered.language or ""),
            page_count=len(pages) or recovered.page_count or 0,
        )

        ocr_engines = {b.ocr_engine for b in ordered if b.ocr_engine}
        provenance = Provenance(
            parser_version=self.config.parser_version,
            dom_schema_version=self.config.dom_schema_version,
            ocr_engine=sorted(ocr_engines)[0] if ocr_engines else None,
            oct_level=bool(ocr_engines),
            docling_version=recovered.docling_version,
            layout_model=recovered.layout_model,
            config=self.config.snapshot(),
            routing=recovered.routing,  # ADR-011: forwarded when the auto route ran
        )

        return Document(
            version=self.config.dom_schema_version,
            document_id=document_id,
            source_hash=sha256,
            metadata=metadata,
            provenance=provenance,
            reading_order=chain,
            reading_order_full=semantic_ctx.reading_order_full,
            regions=semantic_ctx.regions,
            # D1: deterministic page order. `pages` is an insertion-ordered dict
            # keyed by page index; a page first touched out of order (e.g. an
            # annotations-only page mapped before a text page) would otherwise be
            # serialized in insertion order. Sort by index so the canonical DOM
            # page sequence is always 1..N regardless of mapping order.
            pages=sorted(pages.values(), key=lambda p: p.index),
            references=semantic_ctx.references,
            citation_index=semantic_ctx.citation_index,
        )

    def _extract_references(
        self, ctx: SemanticContext, recovered: RecoveredDocument
    ) -> None:
        """Stage 2: D3 — extract references/bibliography from source.

        Delegates to the reference_extractor; results are stored on
        SemanticContext so the caller can wire them into the Document.
        """
        from .reference_extractor import extract_references

        src_bytes = getattr(recovered, "src_bytes", None)
        refs, citation_index = extract_references(
            pages=list(ctx.pages.values()),
            doc_id=ctx.document_id,
            src_bytes=src_bytes,
        )
        ctx.references = refs
        ctx.citation_index = citation_index

    def _build_reading_order_full(self, ctx: SemanticContext) -> None:
        """Stage 2: D4 — complete typed reading order (blocks + tables + images)."""
        ctx.reading_order_full = reading_order.build_reading_order_full(
            sorted(ctx.pages.values(), key=lambda p: p.index)
        )

    def _build_regions(self, ctx: SemanticContext) -> None:
        """Stage 2: region-based reading order (§1.3 fix #5).

        Partition each page into regions (column / sidebar / footnote / header)
        so downstream consumers can linearize per-region instead of walking
        a single flat chain.
        """
        regions: list = []
        for page in sorted(ctx.pages.values(), key=lambda p: p.index):
            if not page.blocks:
                continue
            page_regions = reading_order.build_regions(page)
            for kind, block_ids, bbox in page_regions:
                regions.append(
                    Region(
                        id=f"{ctx.document_id}/r{len(regions)}_{page.index}",
                        page=page.index,
                        bbox=bbox,
                        kind=kind,
                        block_ids=block_ids,
                    )
                )
        ctx.regions = regions
