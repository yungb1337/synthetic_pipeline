"""Simple engine — inherently single-page formats (ADR-013 T9).

Covers plaintext, csv, tsv, json, xml, html, markdown, docx, xlsx. Reuses the
exact existing `Loaders._*` helpers (now returning only page-0 parts) and
detects the format from the source bytes (cheap, accurate). Returns one
`PageResult(page_index=0)`.
"""
from __future__ import annotations

from ..config import ParserConfig
from ..detection import detect
from ..loaders.loaders import Loaders
from ..page_result import PageResult, PageStatus
from .base import SIMPLE, PageWorkItem


class SimpleEngine:
    route_band = SIMPLE

    def __init__(self, config: ParserConfig):
        self.config = config

    def process(self, item: PageWorkItem) -> PageResult:
        from ..parts import RecoveredDocument

        try:
            data = open(item.src_path, "rb").read()
        except Exception as e:
            return PageResult(
                doc_id=item.doc_id, page_index=0, route=SIMPLE, status=PageStatus.FAILED,
                errors=[{"page_no": 1, "category": "simple_read", "message": str(e)}],
                source_hash=item.source_hash,
            )

        detected = detect(data, item.src_path)
        loaders = Loaders(self.config)
        try:
            rec = loaders.load(detected, data, route=None)
        except Exception as e:
            return PageResult(
                doc_id=item.doc_id, page_index=0, route=SIMPLE, status=PageStatus.FAILED,
                errors=[{"page_no": 1, "category": "simple_load", "message": str(e)}],
                source_hash=item.source_hash,
            )

        if not isinstance(rec, RecoveredDocument):
            rec = RecoveredDocument()

        content_present = bool(rec.blocks) or any(t.rows for t in rec.tables)
        return PageResult(
            doc_id=item.doc_id, page_index=0, route=SIMPLE, status=PageStatus.OK,
            blocks=rec.blocks, tables=rec.tables, images=rec.images,
            annotations=rec.annotations, content_present=content_present,
            source_hash=item.source_hash,
        )
