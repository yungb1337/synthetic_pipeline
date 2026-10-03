"""The Normalizer: transforms a parsed DOM into a clean, canonical DOM.

Design guarantees:
  * Pure projection: never mutates input DOM in place (deep copy).
  * Full DOM-tree coverage: normalizes blocks, table headers, table cells,
    captions, annotations, references, and metadata.
  * Structure-aware: respects block kind ('code', 'formula', 'paragraph', 'heading').
  * Rich provenance: attaches comprehensive execution statistics and audit report.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.parser.dom import Document, Provenance

from . import pipeline
from .config import NormalizerConfig
from .rules.base import RuleContext


@dataclass
class NormalizeResult:
    document: Document
    report: dict[str, Any]
    source_document_id: str


class Normalizer:
    def __init__(self, config: NormalizerConfig | None = None):
        self.config = config or NormalizerConfig()

    def _make_context(self, kind: str) -> RuleContext:
        return RuleContext(
            kind=kind,
            unicode_form=self.config.unicode_form,
            preserve_paragraph_breaks=self.config.preserve_paragraph_breaks,
            preserve_code_blocks=self.config.preserve_code_blocks,
            preserve_formulas=self.config.preserve_formulas,
            preserve_multilingual_zwnj=self.config.preserve_multilingual_zwnj,
            preserve_compound_hyphens=self.config.preserve_compound_hyphens,
        )

    def normalize(self, doc: Document) -> Document:
        rule_ids = self.config.enabled_rule_ids
        new = doc.model_copy(deep=True)

        report: dict[str, Any] = {
            "normalizer_version": self.config.normalizer_version,
            "rules": rule_ids,
            "blocks_seen": 0,
            "blocks_changed": 0,
            "tables_seen": 0,
            "table_cells_seen": 0,
            "table_cells_changed": 0,
            "captions_seen": 0,
            "captions_changed": 0,
            "annotations_seen": 0,
            "annotations_changed": 0,
            "references_seen": 0,
            "references_changed": 0,
            "metadata_fields_seen": 0,
            "metadata_fields_changed": 0,
            "chars_in": 0,
            "chars_out": 0,
            "rule_counts": {r: 0 for r in rule_ids},
        }

        def _normalize_str(text: str, kind: str, count_category: str) -> str:
            if not text:
                return text
            report["chars_in"] += len(text)
            ctx = self._make_context(kind)
            out, changed = pipeline.apply(text, rule_ids, context=ctx)
            for rid, was in changed.items():
                if was:
                    report["rule_counts"][rid] += 1
            if out != text:
                if count_category:
                    report[f"{count_category}_changed"] += 1
            report["chars_out"] += len(out)
            return out

        # 1. Blocks traversal
        if self.config.normalize_blocks:
            for page in new.pages:
                for b in page.blocks:
                    report["blocks_seen"] += 1
                    b.text = _normalize_str(b.text, b.kind or "paragraph", "blocks")

        # 2. Tables traversal (headers, cells, captions)
        if self.config.normalize_tables:
            for page in new.pages:
                for t in page.tables:
                    report["tables_seen"] += 1
                    # Table caption
                    if t.caption and self.config.normalize_captions:
                        report["captions_seen"] += 1
                        t.caption = _normalize_str(t.caption, "caption", "captions")

                    # Table header cells
                    if t.header:
                        new_headers = []
                        for h in t.header:
                            report["table_cells_seen"] += 1
                            new_headers.append(
                                _normalize_str(h, "table_cell", "table_cells")
                            )
                        t.header = new_headers

                    # Table body cells
                    for row in t.rows:
                        for cell in row.cells:
                            report["table_cells_seen"] += 1
                            cell.text = _normalize_str(
                                cell.text, "table_cell", "table_cells"
                            )

        # 3. Image captions
        if self.config.normalize_captions:
            for page in new.pages:
                for img in page.images:
                    if img.caption:
                        report["captions_seen"] += 1
                        img.caption = _normalize_str(img.caption, "caption", "captions")

        # 4. Annotations
        for page in new.pages:
            for ann in page.annotations:
                if ann.text:
                    report["annotations_seen"] += 1
                    ann.text = _normalize_str(ann.text, "annotation", "annotations")

        # 5. References
        if self.config.normalize_references and new.references:
            for ref in new.references:
                report["references_seen"] += 1
                if ref.text:
                    ref.text = _normalize_str(ref.text, "reference", "references")
                if ref.label:
                    ref.label = _normalize_str(ref.label, "reference", "references")

        # 6. Metadata strings
        if self.config.normalize_metadata and new.metadata:
            meta_attrs = ["title", "author", "creator", "producer", "subject"]
            for attr in meta_attrs:
                val = getattr(new.metadata, attr, None)
                if val and isinstance(val, str):
                    report["metadata_fields_seen"] += 1
                    norm_val = _normalize_str(val, "metadata", "metadata_fields")
                    setattr(new.metadata, attr, norm_val)

        # Carry provenance normalization report
        if new.provenance is None:
            new = _attach_without_provenance(new, report)
        else:
            new.provenance.normalizer_version = self.config.normalizer_version
            new.provenance.normalization_report = report

        return new


def _attach_without_provenance(doc: Document, report: dict[str, Any]) -> Document:
    doc.provenance = Provenance(
        parser_version="unknown",
        dom_schema_version=doc.version,
        normalizer_version=report["normalizer_version"],
        normalization_report=report,
    )
    return doc
