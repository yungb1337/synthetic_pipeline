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
            p.tables.append(
                Table(
                    id=f"{document_id}/t{len(p.tables)}_{t.page}",
                    page=t.page,
                    bbox=_bbox(t.bbox),
                    header=t.header,
                    rows=[Row(cells=[Cell(text=c) for c in r]) for r in t.rows],
                    source=t.source,
                    confidence=t.confidence,
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

        # page sizes ensure all pages have dims
        for idx, pg in pages.items():
            if idx in recovered.page_sizes:
                pg.width, pg.height = recovered.page_sizes[idx]

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
        )

        return Document(
            version=self.config.dom_schema_version,
            document_id=document_id,
            source_hash=sha256,
            metadata=metadata,
            provenance=provenance,
            reading_order=chain,
            pages=list(pages.values()),
            references=[Reference(kind=k, target=t) for (k, t) in recovered.references],
        )