"""Enrichment engine — per-page ADR-012 variant (native + OCR fallback).

For each page: run the native extractor. If the page produced ZERO text blocks
(a scanned page), render exactly ONE Pixmap and OCR it via the existing
`on-prem` RapidOCR wrapper (`ocr.ocr_bytes`). One render per scanned page —
matching ADR-012's "exactly one Pixmap per empty page". OCR is gated by
`config.ocr_enabled`.
"""
from __future__ import annotations

import time

from .. import ocr
from ..config import ParserConfig
from ..page_result import PageResult, PageStatus
from ..parts import RecoveredBlock
from .base import ENRICHMENT, PageWorkItem
from .native_pdf import NativePdfEngine


class EnrichmentEngine:
    route_band = ENRICHMENT

    def __init__(self, config: ParserConfig):
        self.config = config

    def process(self, item: PageWorkItem) -> PageResult:
        native = NativePdfEngine(self.config)
        res = native.process(item)
        # Count text-bearing blocks only (a page with no text is a candidate for
        # OCR; tables/images alone do not count as "readable text").
        has_text = any(b.source == "text" or b.kind != "heading" for b in res.blocks) and any(
            b.text.strip() for b in res.blocks
        )
        if has_text or not self.config.ocr_enabled:
            return res

        # Zero text -> render this one page and OCR it.
        import fitz

        engine_name = ocr.engine_name()
        t_ocr = time.time()
        try:
            doc = fitz.open(item.src_path)
            try:
                page = doc[item.page_index]
                pix = page.get_pixmap()
                png = pix.tobytes("png")
            finally:
                doc.close()
        except Exception as e:
            res.errors.append({"page_no": item.page_index + 1, "category": "ocr_render", "message": str(e)})
            return res

        ocr_blocks: list[RecoveredBlock] = []
        for i, (text, bbox, conf) in enumerate(ocr.ocr_bytes(png)):
            clean = text.strip()
            if not clean:
                continue
            ocr_blocks.append(
                RecoveredBlock(
                    page=item.page_index, kind="paragraph", text=clean, bbox=bbox,
                    seq=len(res.blocks) + i, confidence=conf if conf <= 1.0 else conf / 100.0,
                    source="ocr", ocr_engine=engine_name,
                )
            )
        res.blocks.extend(ocr_blocks)
        res.timings.setdefault("ocr_ms", 0.0)
        res.timings["ocr_ms"] = round((time.time() - t_ocr) * 1000, 1)
        res.timings["ocr_pages"] = 1
        return res