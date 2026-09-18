"""DOM Converter mapping pdf-inspector output into canonical dom-v0.1.0.docJSON.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from app.normalizer.normalizer import Normalizer
from app.parser.dom.models import (
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
from .inspector_adapter import InspectorExtractionResult


# Regex patterns for running headers, footers, and metadata lines
_JOURNAL_HEADER_PAT = re.compile(
    r"^(?:#+\s*)?(?:PLoS|PLOS)\s+(?:COMPUTATIONAL\s+BIOLOGY|CLINICAL\s+TRIALS|GENETICS|PATHOGENS|ONE|MEDICINE|BIOLOGY|NEGLECTED\s+TROPICAL\s+DISEASES|DIGITAL\s+HEALTH)\b",
    re.IGNORECASE,
)
_FOOTER_PAT = re.compile(
    r"^(?:PLOS|PLoS)\s.*\|\s*https?://|www\.plos[a-z]*\.org|\bdoi:\s*10\.\d{4,9}/|\b\d+\s*/\s*\d+\s*$|^Page\s+\d+\s+of\s+\d+$",
    re.IGNORECASE,
)
_REF_HEADER_PAT = re.compile(
    r"^(?:#+\s*)?(?:REFERENCES|BIBLIOGRAPHY|LITERATURE\s+CITED|WORKS\s+CITED)\s*$",
    re.IGNORECASE,
)
_NUMBERED_REF_PAT = re.compile(
    r"^(?:\*\*)?(?:\[?(\d+)\]?|(\d+)\.)(?:\*\*)?\s+(.*)",
    re.DOTALL,
)
_ORPHAN_REF_NUM_PAT = re.compile(
    r"^(?:\*\*)?(?:\[?(\d+)\]?|(\d+)\.)(?:\*\*)?\s*$"
)


class PDFInspectorDOMConverter:
    """Converts pdf-inspector extraction result into canonical Document DOM."""

    def __init__(self, normalizer: Normalizer | None = None):
        self.normalizer = normalizer or Normalizer()

    def _is_header_or_footer(self, line: str, doc_title: str = "") -> str | None:
        """Returns 'header' or 'footer' if line is margin metadata, else None."""
        text = line.strip()
        if not text:
            return None
        clean_text = text.lstrip("#").strip()

        # Check journal titles
        if _JOURNAL_HEADER_PAT.match(clean_text):
            return "header"
        if clean_text.upper() in ("RESEARCH ARTICLE", "EDITORIAL COMMENTARY", "REVIEW ARTICLE"):
            return "header"

        # Check footer patterns
        if _FOOTER_PAT.search(text):
            return "footer"

        # Running title match
        if doc_title and len(clean_text) > 10 and clean_text.lower() == doc_title.lower():
            return "header"

        return None

    def _parse_markdown_tables(self, md_lines: list[str]) -> tuple[list[dict[str, Any]], list[tuple[int, int]]]:
        """Detects and parses Markdown table blocks. Returns table dicts and line index ranges to exclude."""
        tables = []
        ranges = []
        i = 0
        n = len(md_lines)

        while i < n:
            line = md_lines[i].strip()
            # Look for delimiter row: |---|---|
            if i + 1 < n and ("|" in line or line.startswith("Table ") or line.startswith("Tab.")):
                next_line = md_lines[i + 1].strip()
                if "|" in next_line and re.search(r"\|[\s\-:|]+\|", next_line):
                    caption = ""
                    header_line = line
                    start_idx = i

                    # Check if line is actually a caption (e.g. Table 1. Description)
                    if line.startswith("Table") or line.startswith("Tab."):
                        caption = line.strip("|").strip()
                        # See if next_line is header and next_next is delimiter
                        if i + 2 < n:
                            candidate_delim = md_lines[i + 2].strip()
                            if "|" in candidate_delim and re.search(r"\|[\s\-:|]+\|", candidate_delim):
                                header_line = next_line
                                next_line = candidate_delim
                                i += 1

                    header_cells = [c.strip() for c in header_line.strip("|").split("|")]
                    row_lines = []
                    i += 2  # skip header and delimiter

                    while i < n:
                        cur = md_lines[i].strip()
                        if cur.startswith("|") or ("|" in cur and not cur.startswith("#")):
                            row_cells = [c.strip() for c in cur.strip("|").split("|")]
                            # Filter out single-cell DOI image links as rows
                            if any(row_cells):
                                row_lines.append(row_cells)
                            i += 1
                        else:
                            break

                    # Validate that this is a real table (at least 2 columns or multi-row)
                    has_data = len(row_lines) > 0 and any(len(r) > 1 for r in row_lines)
                    # Filter out pseudo-tables (e.g. 1-row p-value annotations or figure DOIs)
                    if has_data and not (len(row_lines) == 1 and any("doi.org" in "".join(r) for r in row_lines)):
                        tables.append({
                            "caption": caption,
                            "header": header_cells,
                            "rows": row_lines,
                        })
                        ranges.append((start_idx, i - 1))
                        continue
            i += 1

        return tables, ranges

    def build_canonical_dom(
        self,
        extraction: InspectorExtractionResult,
        source_sha256: str = "",
    ) -> Document:
        doc_id = extraction.doc_id
        if not source_sha256 and Path(extraction.pdf_path).exists():
            data = Path(extraction.pdf_path).read_bytes()
            source_sha256 = hashlib.sha256(data).hexdigest()

        # Pre-pass: Discover recurring running headers and footers across pages
        from collections import Counter
        top_line_counts = Counter()
        bottom_line_counts = Counter()
        for p_data in extraction.pages:
            p_lines = [l.strip() for l in (p_data.markdown or "").splitlines() if l.strip()]
            for l in p_lines[:3]:
                clean = l.lstrip("#").strip()
                if len(clean) > 4:
                    top_line_counts[clean] += 1
            for l in p_lines[-3:]:
                clean = l.lstrip("#").strip()
                if len(clean) > 4:
                    bottom_line_counts[clean] += 1

        recurring_headers = {line for line, count in top_line_counts.items() if count >= 2}
        recurring_footers = {line for line, count in bottom_line_counts.items() if count >= 2}

        pages: list[Page] = []
        reading_order: list[str] = []
        reading_order_full: list[ReadingOrderEntry] = []
        references: list[Reference] = []
        citation_index: dict[str, str] = {}

        block_counter = 0
        ref_counter = 0
        in_ref_section = False
        pending_orphan_ref_nums: list[str] = []

        for p_idx, p_data in enumerate(extraction.pages):
            page_blocks: list[Block] = []
            page_tables: list[Table] = []
            page_images: list[ImageObject] = []

            # Check if this specific page requires Docling heavy escalation
            if getattr(p_data, "route", "rust_native") == "docling_heavy":
                try:
                    from app.parser.loaders import docling_loader
                    if docling_loader.engine_available():
                        import fitz
                        with fitz.open(extraction.pdf_path) as mdoc:
                            if p_idx < len(mdoc):
                                single_doc = fitz.open()
                                single_doc.insert_pdf(mdoc, from_page=p_idx, to_page=p_idx)
                                page_bytes = single_doc.tobytes()
                                single_doc.close()
                                rec_doc = docling_loader.parse(page_bytes, filename=f"page_{p_idx}.pdf")
                            else:
                                rec_doc = None
                        if rec_doc and (rec_doc.blocks or rec_doc.tables):
                            for b in rec_doc.blocks:
                                b_id = f"b-p{p_idx}-{block_counter:04d}"
                                block_counter += 1
                                page_blocks.append(Block(
                                    id=b_id,
                                    page=p_idx,
                                    kind=b.kind,
                                    text=b.text,
                                    bbox=list(b.bbox) if b.bbox else None,
                                    source="docling",
                                    confidence=b.confidence,
                                ))
                                reading_order.append(b_id)
                                reading_order_full.append(ReadingOrderEntry(id=b_id, type="block"))

                            for t_idx, t in enumerate(rec_doc.tables):
                                t_id = f"t-p{p_idx}-{t_idx:02d}"
                                dom_rows = []
                                if t.header:
                                    dom_rows.append(Row(cells=[Cell(text=c) for c in t.header]))
                                for r in t.rows:
                                    dom_rows.append(Row(cells=[Cell(text=c) for c in r]))
                                page_tables.append(Table(
                                    id=t_id,
                                    page=p_idx,
                                    bbox=list(t.bbox) if t.bbox else None,
                                    header=t.header,
                                    rows=dom_rows,
                                    caption=t.caption or "",
                                    source="docling",
                                ))
                                reading_order.append(t_id)
                                reading_order_full.append(ReadingOrderEntry(id=t_id, type="table"))

                            pages.append(
                                Page(
                                    index=p_idx,
                                    width=612.0,
                                    height=792.0,
                                    blocks=page_blocks,
                                    tables=page_tables,
                                    images=page_images,
                                    annotations=[],
                                )
                            )
                            continue
                except Exception:
                    pass  # Fall through to native extraction on error

            p_md = p_data.markdown or ""
            md_lines = p_md.splitlines()

            # 1. Parse tables from markdown
            parsed_tables, table_ranges = self._parse_markdown_tables(md_lines)
            excluded_lines = set()
            for s, e in table_ranges:
                for idx in range(s, e + 1):
                    excluded_lines.add(idx)

            for t_idx, tbl in enumerate(parsed_tables):
                t_id = f"t-p{p_idx}-{t_idx:02d}"
                dom_rows: list[Row] = []

                # Header row
                if tbl["header"]:
                    header_cells = [Cell(text=c) for c in tbl["header"]]
                    dom_rows.append(Row(cells=header_cells))

                # Data rows
                for r in tbl["rows"]:
                    cells = [Cell(text=c) for c in r]
                    dom_rows.append(Row(cells=cells))

                dom_table = Table(
                    id=t_id,
                    page=p_idx,
                    bbox=None,
                    header=tbl["header"],
                    rows=dom_rows,
                    caption=tbl.get("caption", ""),
                    source="native",
                )
                page_tables.append(dom_table)
                reading_order.append(t_id)
                reading_order_full.append(
                    ReadingOrderEntry(
                        id=t_id,
                        type="table",
                    )
                )

            # 2. Parse text blocks from remaining markdown lines
            current_paragraph: list[str] = []

            def flush_paragraph():
                nonlocal block_counter, ref_counter, in_ref_section, pending_orphan_ref_nums
                if not current_paragraph:
                    return
                raw_text = " ".join(current_paragraph).strip()
                current_paragraph.clear()
                if not raw_text:
                    return

                # Check if this paragraph is a header or footer
                clean_text = raw_text.lstrip("#").strip()
                if clean_text in recurring_headers:
                    hf_kind = "header"
                elif clean_text in recurring_footers:
                    hf_kind = "footer"
                else:
                    hf_kind = self._is_header_or_footer(raw_text, doc_title=doc_id)

                if hf_kind is not None:
                    b_id = f"b-p{p_idx}-{block_counter:04d}"
                    block_counter += 1
                    block = Block(
                        id=b_id,
                        page=p_idx,
                        kind=hf_kind,
                        text=clean_text,
                        bbox=None,
                        source="text",
                    )
                    page_blocks.append(block)
                    return

                # Check if entering reference section
                if _REF_HEADER_PAT.match(raw_text):
                    in_ref_section = True
                    b_id = f"b-p{p_idx}-{block_counter:04d}"
                    block_counter += 1
                    block = Block(
                        id=b_id,
                        page=p_idx,
                        kind="heading",
                        text=raw_text.lstrip("#").strip(),
                        bbox=None,
                        source="text",
                    )
                    page_blocks.append(block)
                    reading_order.append(b_id)
                    reading_order_full.append(ReadingOrderEntry(id=b_id, type="block"))
                    return

                # Check for orphan reference numbers (e.g. **3.** on its own line)
                m_orphan = _ORPHAN_REF_NUM_PAT.match(raw_text)
                if m_orphan and in_ref_section:
                    num = m_orphan.group(1) or m_orphan.group(2)
                    pending_orphan_ref_nums.append(num)
                    return

                # Check for numbered reference entry (only inside reference section)
                if in_ref_section:
                    m_ref = _NUMBERED_REF_PAT.match(raw_text)
                    if m_ref:
                        ref_num = m_ref.group(1) or m_ref.group(2)
                        ref_body = m_ref.group(3).strip()
                        ref_id = f"ref-{ref_counter:04d}"
                        ref_counter += 1
                        references.append(
                            Reference(id=ref_id, text=ref_body, kind="citation", label=str(ref_num))
                        )
                        citation_index[ref_id] = ref_body[:80]

                        b_id = f"b-p{p_idx}-{block_counter:04d}"
                        block_counter += 1
                        block = Block(
                            id=b_id,
                            page=p_idx,
                            kind="reference",
                            text=f"[{ref_num}] {ref_body}",
                            bbox=None,
                            source="text",
                        )
                        page_blocks.append(block)
                        reading_order.append(b_id)
                        reading_order_full.append(ReadingOrderEntry(id=b_id, type="block"))
                        return

                    elif len(raw_text) > 30:
                        # Check if multiple citations are glued in this text block
                        splits = [0]
                        for m_split in re.finditer(r'(?:PMID:\s*\d+|\b\d{4}\s*[A-Z][a-z]+;\s*[\d\(]+[–\-0-9\)]+)\.?\s+(?=[A-Z][a-z]+ [A-Z]{1,2}(?:,\s+[A-Z][a-z]+ [A-Z]{1,2})*)', raw_text):
                            splits.append(m_split.end())
                        splits.append(len(raw_text))

                        sub_citations = [raw_text[splits[i]:splits[i+1]].strip() for i in range(len(splits)-1) if raw_text[splits[i]:splits[i+1]].strip()]

                        for sub_c in sub_citations:
                            m_sub_ref = _NUMBERED_REF_PAT.match(sub_c)
                            if m_sub_ref:
                                lbl = m_sub_ref.group(1) or m_sub_ref.group(2)
                                body = m_sub_ref.group(3).strip()
                            else:
                                lbl = pending_orphan_ref_nums.pop(0) if pending_orphan_ref_nums else str(ref_counter + 1)
                                body = sub_c

                            ref_id = f"ref-{ref_counter:04d}"
                            ref_counter += 1
                            references.append(
                                Reference(id=ref_id, text=body, kind="citation", label=str(lbl))
                            )
                            citation_index[ref_id] = body[:80]

                            b_id = f"b-p{p_idx}-{block_counter:04d}"
                            block_counter += 1
                            block = Block(
                                id=b_id,
                                page=p_idx,
                                kind="reference",
                                text=f"[{lbl}] {body}",
                                bbox=None,
                                source="text",
                            )
                            page_blocks.append(block)
                            reading_order.append(b_id)
                            reading_order_full.append(ReadingOrderEntry(id=b_id, type="block"))
                        return

                # Normal block classification
                b_id = f"b-p{p_idx}-{block_counter:04d}"
                block_counter += 1

                b_kind = "paragraph"
                text = raw_text
                if text.startswith("#"):
                    b_kind = "heading"
                    text = text.lstrip("#").strip()
                elif re.match(r"^[\*\-\+]\s+", text):
                    b_kind = "list_item"
                    text = re.sub(r"^[\*\-\+]\s+", "", text).strip()
                elif re.match(r"^[0-9]+\.\s+[a-zA-Z]", text):
                    b_kind = "list_item"

                block = Block(
                    id=b_id,
                    page=p_idx,
                    kind=b_kind,
                    text=text,
                    bbox=None,
                    source="text",
                )
                page_blocks.append(block)
                reading_order.append(b_id)
                reading_order_full.append(
                    ReadingOrderEntry(
                        id=b_id,
                        type="block",
                    )
                )

            for line_idx, line in enumerate(md_lines):
                if line_idx in excluded_lines:
                    flush_paragraph()
                    continue

                raw_line = line.strip()
                if not raw_line:
                    flush_paragraph()
                    continue

                if (
                    raw_line.startswith("#")
                    or raw_line.startswith("- ")
                    or raw_line.startswith("* ")
                    or _ORPHAN_REF_NUM_PAT.match(raw_line)
                    or _NUMBERED_REF_PAT.match(raw_line)
                    or _JOURNAL_HEADER_PAT.match(raw_line.lstrip("#").strip())
                    or _FOOTER_PAT.search(raw_line)
                ):
                    flush_paragraph()
                    current_paragraph.append(raw_line)
                    flush_paragraph()
                else:
                    current_paragraph.append(raw_line)

            flush_paragraph()

            # Construct Page object
            pages.append(
                Page(
                    index=p_idx,
                    width=612.0,
                    height=792.0,
                    blocks=page_blocks,
                    tables=page_tables,
                    images=page_images,
                    annotations=[],
                )
            )

        # Build raw Document
        raw_doc = Document(
            version="0.1.0",
            document_id=doc_id,
            source_hash=source_sha256,
            metadata=Metadata(
                title=doc_id,
                page_count=extraction.page_count or len(pages),
                mime="application/pdf",
                detected_type="pdf",
            ),
            provenance=Provenance(
                parser_version="pdf-inspector-v1.20.0",
                dom_schema_version="0.1.0",
            ),
            pages=pages,
            reading_order=reading_order,
            reading_order_full=reading_order_full,
            references=references,
            citation_index=citation_index,
        )

        # Pass through Normalizer
        try:
            norm_doc = self.normalizer.normalize(raw_doc)
            return norm_doc
        except Exception:
            return raw_doc
