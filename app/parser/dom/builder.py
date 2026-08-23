"""Build a canonical Document DOM from recovered parts.

Responsibilities:
  * Map RecoveredDocument -> Document (pydantic) deterministically.
  * Recover global reading order over the loader's blocks (which carry .bbox
    and .seq), then materialize it as an ordered chain of block ids.
  * Assign stable block ids.
  * Compute source-hash for idempotency + lineage.
"""
from __future__ import annotations

from ..parts import RecoveredDocument
from ..config import ParserConfig
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
    Row,
    Table,
)


def _bbox(t: tuple | None) -> BBox | None:
    if t is None:
        return None
    x0, y0, x1, y1 = t
    return BBox(x0=x0, y0=y0, x1=x1, y1=y1)


def _idf(doc_id: str, page: int, seq: int) -> str:
    return f"{doc_id}/b{page:02d}_{seq:04d}"


class DocumentBuilder:
    def __init__(self, config: ParserConfig):
        self.config = config

    def build(self, recovered: RecoveredDocument, document_id: str, sha256: str) -> Document:
        # 1) read order over the *loader* blocks (bbox + seq present here).
        #    ADR-007: a loader that provides authoritative reading order (e.g.
        #    Docling's iterate_items) opts out of the heuristic ROG.
        if recovered.reading_order_authoritative:
            ordered = list(recovered.blocks)
        else:
            ordered = reading_order.recover_reading_order(recovered.blocks)

        pages: dict[int, Page] = {}
        chain: list[str] = []

        for b in ordered:
            page = pages.setdefault(b.page, Page(index=b.page, blocks=[]))
            if b.page in recovered.page_sizes:
                w, h = recovered.page_sizes[b.page]
                page.width, page.height = w, h
            bid = _idf(document_id, b.page, len(page.blocks))
            page.blocks.append(
                Block(
                    id=bid,
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
            chain.append(bid)

        # tables / images / annotations
        for t in recovered.tables:
            p = pages.setdefault(t.page, Page(index=t.page, blocks=[]))
            # Forward cell geometry (D5). Docling supplies per-cell TOPLEFT bboxes
            # aligned to `rows`; native tables leave them empty and we forward
            # None (never fabricate coordinates). `row_bboxes` carries the row's
            # union bbox. Both are additive and preserved for downstream use.
            _rows: list[Row] = []
            for ri, r in enumerate(t.rows):
                cb = t.cell_bboxes[ri] if ri < len(t.cell_bboxes) else [None] * len(r)
                _rows.append(Row(
                    cells=[Cell(
                        text=c,
                        bbox=_bbox(cb[ci] if ci < len(cb) else None),
                    ) for ci, c in enumerate(r)],
                    bbox=_bbox(t.row_bboxes[ri] if ri < len(t.row_bboxes) else None),
                ))
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
            p.annotations.append(Annotation(kind=ann.kind, text=ann.text, page=ann.page))

        # D9: guarantee a Page object for EVERY page in the expected set, even
        # when the page carries no content in the folded RecoveredDocument. A
        # continuation page whose only content was a table fragment gets that
        # fragment's rows merged into the parent table (normalize_tables), leaving
        # it content-less here; without an explicit Page it would be SILENTLY
        # dropped from the canonical DOM even though the assembler counted it as
        # assembled — exactly the "page 8 missing" defect. Emitting an empty Page
        # preserves page order/density and makes zero-silent-loss true end-to-end.
        # Generic: keyed off the source page count and the page-index convention
        # actually used by the content (native is 0-based, docling 1-based); never
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

        metadata = Metadata(
            mime=recovered.mime,
            detected_type=recovered.detected_type,
            declared_extension=recovered.declared_extension,
            probe=recovered.probe,
            title=recovered.title or "",
            author=recovered.author or "",
            creator=recovered.creator or "",
            producer=recovered.producer or "",
            subject=recovered.subject or "",
            created=recovered.created or "",
            modified=recovered.modified or "",
            language=recovered.language or "",
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
            # D1: deterministic page order. `pages` is an insertion-ordered dict
            # keyed by page index; a page first touched out of order (e.g. an
            # annotations-only page mapped before a text page) would otherwise be
            # serialized in insertion order. Sort by index so the canonical DOM
            # page sequence is always 1..N regardless of mapping order.
            pages=sorted(pages.values(), key=lambda p: p.index),
            references=[Reference(kind=k, target=t) for (k, t) in recovered.references],
        )