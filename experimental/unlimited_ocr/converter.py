"""Converts raw Unlimited-OCR outputs into the canonical Document DOM and applies Normalization."""

from __future__ import annotations

import re

from app.normalizer.normalizer import Normalizer
from app.parser.dom.models import (
    BBox,
    Block,
    Document,
    ImageObject,
    Metadata,
    Page,
    Provenance,
    ReadingOrderEntry,
    Reference,
    Table,
)

from .adapter import DocumentRawOCR


class UnlimitedOCRConverter:
    """Transforms raw OCR boxes and texts into the canonical Document DOM with strict traceability."""

    def __init__(self, normalizer: Normalizer | None = None):
        self.normalizer = normalizer or Normalizer()

    def _quad_to_bbox(self, quad: list[list[float]]) -> BBox | None:
        if not quad or len(quad) < 4:
            return None
        xs = [pt[0] for pt in quad]
        ys = [pt[1] for pt in quad]
        return BBox(x0=min(xs), y0=min(ys), x1=max(xs), y1=max(ys))

    def _classify_block_kind(self, text: str, font_height: float = 0.0) -> str:
        t = text.strip()
        if not t:
            return "paragraph"
        # Heading checks
        if re.match(
            r"^(?:[0-9]+(?:\.[0-9]+)*\s+[A-Z]|Abstract\b|Introduction\b|Methods\b|Results\b|Discussion\b|Conclusion\b|References\b)",
            t,
            re.IGNORECASE,
        ):
            return "heading"
        if len(t) < 60 and (t.isupper() or t.istitle()) and not t.endswith("."):
            return "heading"
        if re.match(r"^\[[0-9]+\]|^[0-9]+\.\s+[A-Z]", t):
            return "reference"
        return "paragraph"

    def convert_raw_to_dom(self, raw_doc: DocumentRawOCR) -> Document:
        pages: list[Page] = []
        reading_order: list[str] = []
        reading_order_full: list[ReadingOrderEntry] = []
        references: list[Reference] = []
        citation_index: dict[str, str] = {}

        block_counter = 0
        ref_counter = 0

        in_ref_section = False

        for raw_page in raw_doc.pages:
            page_blocks: list[Block] = []
            page_tables: list[Table] = []
            page_images: list[ImageObject] = []

            # Sort lines geometrically: primary y (with ~10px tolerance for columns), secondary x
            indexed_lines = []
            for i, (txt, score, box) in enumerate(
                zip(raw_page.texts, raw_page.scores, raw_page.boxes)
            ):
                bbox = self._quad_to_bbox(box)
                indexed_lines.append((i, txt, score, box, bbox))

            # Sort top-to-bottom, left-to-right
            def _sort_key(item):
                b = item[4]
                if b is None:
                    return (0, 0)
                # Two-column layout heuristic: if page width > 400 and x0 > width/2, sort column 2 after column 1
                col = (
                    1 if (raw_page.width > 300 and b.x0 > raw_page.width * 0.48) else 0
                )
                return (col, round(b.y0 / 12.0), b.x0)

            indexed_lines.sort(key=_sort_key)

            for _, txt, score, _, bbox in indexed_lines:
                block_counter += 1
                b_id = f"b-p{raw_page.page_index}-{block_counter:04d}"
                kind = self._classify_block_kind(txt)

                if "reference" in txt.lower() and len(txt) < 30:
                    in_ref_section = True

                # Extract reference items
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
                elif in_ref_section and re.match(r"^[0-9]+\.\s+(.*)", txt):
                    ref_match2 = re.match(r"^([0-9]+)\.\s*(.*)", txt)
                    if ref_match2:
                        ref_label = f"[{ref_match2.group(1)}]"
                        ref_counter += 1
                        r_id = f"ref-{ref_counter:03d}"
                        references.append(
                            Reference(
                                id=r_id,
                                label=ref_label,
                                text=ref_match2.group(2) or txt,
                                kind="citation",
                                target="",
                            )
                        )
                        citation_index[ref_match2.group(1)] = r_id

                block = Block(
                    id=b_id,
                    kind=kind,
                    text=txt,
                    bbox=bbox,
                    page=raw_page.page_index,
                    confidence=score,
                    source="ocr",
                    ocr_engine="unlimited-ocr-ppocrv6",
                )
                page_blocks.append(block)
                reading_order.append(b_id)
                reading_order_full.append(ReadingOrderEntry(type="block", id=b_id))

            pages.append(
                Page(
                    index=raw_page.page_index,
                    width=raw_page.width,
                    height=raw_page.height,
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
            probe="experimental-unlimited-ocr",
            title=raw_doc.metadata.get("title", ""),
            page_count=len(pages),
        )

        provenance = Provenance(
            parser_version="experimental-unlimited-ocr-v1.0",
            dom_schema_version="0.1.0",
            ocr_engine="unlimited-ocr-ppocrv6",
            layout_model="ppocrv6-det-rec",
            config=raw_doc.metadata,
        )

        doc = Document(
            version="0.1.0",
            document_id=raw_doc.document_id,
            source_hash=raw_doc.source_sha256,
            metadata=metadata,
            provenance=provenance,
            reading_order=reading_order,
            reading_order_full=reading_order_full,
            pages=pages,
            references=references,
            citation_index=citation_index,
            regions=[],
        )

        # Apply existing canonical Normalizer (Module #2)
        normalized_doc = self.normalizer.normalize(doc)
        return normalized_doc
