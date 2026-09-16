"""Converts extracted tables and OCR blocks into canonical Document DOM and runs Normalization.
"""
from __future__ import annotations

import re
from typing import Any

from app.normalizer.normalizer import Normalizer
from app.parser.dom.models import (
    BBox,
    Block,
    Cell,
    Document,
    ImageObject,
    Metadata,
    Page,
    Provenance,
    ReadingOrderEntry,
    Reference,
    Row,
    Table,
)

from .adapters.base import RawTable


class TableBenchmarkDOMConverter:
    """Constructs canonical Document DOM from OCR text blocks and 2D RawTable objects."""

    def __init__(self, normalizer: Normalizer | None = None):
        self.normalizer = normalizer or Normalizer()

    def _quad_to_bbox(self, quad: list[list[float]], scale_x: float = 1.0, scale_y: float = 1.0) -> BBox | None:
        if not quad or len(quad) < 4:
            return None
        xs = [pt[0] / scale_x for pt in quad]
        ys = [pt[1] / scale_y for pt in quad]
        return BBox(x0=min(xs), y0=min(ys), x1=max(xs), y1=max(ys))

    def _classify_block_kind(self, text: str) -> str:
        t = text.strip()
        if not t:
            return "paragraph"
        if re.match(r"^(?:[0-9]+(?:\.[0-9]+)*\s+[A-Z]|Abstract\b|Introduction\b|Methods\b|Results\b|Discussion\b|Conclusion\b|References\b)", t, re.IGNORECASE):
            return "heading"
        if len(t) < 60 and (t.isupper() or t.istitle()) and not t.endswith("."):
            return "heading"
        if re.match(r"^\[[0-9]+\]|^[0-9]+\.\s+[A-Z]", t):
            return "reference"
        return "paragraph"

    def build_canonical_dom(
        self,
        document_id: str,
        source_sha256: str,
        page_results: list[dict[str, Any]],
        strategy_id: str = "P001",
    ) -> Document:
        pages: list[Page] = []
        reading_order: list[str] = []
        reading_order_full: list[ReadingOrderEntry] = []
        references: list[Reference] = []
        citation_index: dict[str, str] = {}

        block_counter = 0
        ref_counter = 0
        in_ref_section = False

        for p_data in page_results:
            p_idx = p_data["page_index"]
            p_w = p_data["width"]
            p_h = p_data["height"]
            scale_x = p_data.get("scale_x", 1.0)
            scale_y = p_data.get("scale_y", 1.0)

            page_blocks: list[Block] = []
            page_tables: list[Table] = []
            page_images: list[ImageObject] = []

            # 1. Convert RawTables into canonical Table objects
            tables_data: list[RawTable] = p_data.get("tables", [])
            for t_idx, raw_tbl in enumerate(tables_data):
                dom_rows: list[Row] = []
                for r in raw_tbl.rows:
                    cells = [Cell(text=str(c)) for c in r]
                    dom_rows.append(Row(cells=cells))

                t_bbox = BBox(
                    x0=raw_tbl.bbox.x0,
                    y0=raw_tbl.bbox.y0,
                    x1=raw_tbl.bbox.x1,
                    y1=raw_tbl.bbox.y1,
                ) if raw_tbl.bbox else None

                t_id = f"t-p{p_idx}-{t_idx:02d}"
                dom_table = Table(
                    id=t_id,
                    page=p_idx,
                    bbox=t_bbox,
                    header=raw_tbl.header,
                    rows=dom_rows,
                    source=raw_tbl.source,
                    confidence=raw_tbl.confidence,
                    caption=raw_tbl.caption,
                )
                page_tables.append(dom_table)

            # 2. Convert OCR lines into Blocks
            ocr_boxes = p_data.get("ocr_boxes", [])
            ocr_texts = p_data.get("ocr_texts", [])
            ocr_scores = p_data.get("ocr_scores", [])

            indexed_lines = []
            for i, (txt, score, box) in enumerate(zip(ocr_texts, ocr_scores, ocr_boxes)):
                bbox = self._quad_to_bbox(box, scale_x, scale_y)
                indexed_lines.append((i, txt, score, box, bbox))

            # Sort reading order (two column heuristic)
            def _sort_key(item):
                b = item[4]
                if b is None:
                    return (0, 0)
                col = 1 if (p_w > 300 and b.x0 > p_w * 0.48) else 0
                return (col, round(b.y0 / 12.0), b.x0)

            indexed_lines.sort(key=_sort_key)

            for _, txt, score, _, bbox in indexed_lines:
                block_counter += 1
                b_id = f"b-p{p_idx}-{block_counter:04d}"
                kind = self._classify_block_kind(txt)

                if "reference" in txt.lower() and len(txt) < 30:
                    in_ref_section = True

                # Check citation
                ref_match = re.match(r"^\[([0-9]+)\]\s*(.*)", txt)
                if ref_match:
                    ref_label = f"[{ref_match.group(1)}]"
                    ref_counter += 1
                    r_id = f"ref-{ref_counter:03d}"
                    references.append(
                        Reference(
                            id=r_id,
                            label=ref_label,
                            text=ref_match.group(2) or txt,
                            kind="citation",
                            target="",
                        )
                    )
                    citation_index[ref_match.group(1)] = r_id

                block = Block(
                    id=b_id,
                    kind=kind,
                    text=txt,
                    bbox=bbox,
                    page=p_idx,
                    confidence=score,
                    source="ocr",
                    ocr_engine="rapidocr_ppocrv6",
                )
                page_blocks.append(block)
                reading_order.append(b_id)
                reading_order_full.append(ReadingOrderEntry(type="block", id=b_id))

            # Add table entries to full reading order
            for tbl in page_tables:
                reading_order_full.append(ReadingOrderEntry(type="table", id=tbl.id))

            pages.append(
                Page(
                    index=p_idx,
                    width=p_w,
                    height=p_h,
                    blocks=page_blocks,
                    tables=page_tables,
                    images=page_images,
                    annotations=[],
                )
            )

        metadata = Metadata(
            mime="application/pdf",
            detected_type="pdf",
            declared_extension="pdf",
            probe="experimental-table-eval",
            page_count=len(pages),
        )

        provenance = Provenance(
            parser_version="experimental-table-benchmark-1.0",
            dom_schema_version="1.0",
            ocr_engine="rapidocr_ppocrv6",
            layout_model=strategy_id,
            config={"strategy_id": strategy_id},
        )

        raw_doc = Document(
            version="1.0",
            document_id=document_id,
            source_hash=source_sha256,
            metadata=metadata,
            provenance=provenance,
            reading_order=reading_order,
            reading_order_full=reading_order_full,
            pages=pages,
            references=references,
            citation_index=citation_index,
        )

        # Run normalizer
        normalized_doc = self.normalizer.normalize(raw_doc)
        return normalized_doc
