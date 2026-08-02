"""The Normalizer: takes a parsed DOM, returns a normalized DOM.

It is a pure projection:
  * never mutates the input in place (model_copy(deep=True)).
  * only touches `Block.text`; every other field is preserved (bboxes, tables,
    images, reading order, metadata).
  * attaches a normalization report + version to `provenance`.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.parser.dom import Document

from . import pipeline
from .config import NormalizerConfig


@dataclass
class NormalizeResult:
    document: Document
    report: dict
    source_document_id: str


class Normalizer:
    def __init__(self, config: NormalizerConfig | None = None):
        self.config = config or NormalizerConfig()

    def normalize(self, doc: Document) -> Document:
        rule_ids = self.config.enabled_rule_ids
        new = doc.model_copy(deep=True)

        report: dict = {
            "normalizer_version": self.config.normalizer_version,
            "rules": rule_ids,
            "blocks_seen": 0,
            "blocks_changed": 0,
            "chars_in": 0,
            "chars_out": 0,
            "rule_counts": {r: 0 for r in rule_ids},
        }

        for page in new.pages:
            for b in page.blocks:
                report["blocks_seen"] += 1
                report["chars_in"] += len(b.text)
                out, changed = pipeline.apply(b.text, rule_ids)
                for rid, was in changed.items():
                    if was:
                        report["rule_counts"][rid] += 1
                if out != b.text:
                    b.text = out
                    report["blocks_changed"] += 1
                report["chars_out"] += len(out)

        # carry normalization facts in the DOM, never overwrite the source artifact
        if new.provenance is None:
            # extremely defensive: preserve structure even if provenance missing
            new = _attach_without_provenance(new, report)
        else:
            new.provenance.normalizer_version = self.config.normalizer_version
            new.provenance.normalization_report = report
        return new


def _attach_without_provenance(doc: Document, report: dict) -> Document:
    from app.parser.dom import Provenance

    doc.provenance = Provenance(
        parser_version="unknown",
        dom_schema_version=doc.version,
        normalizer_version=report["normalizer_version"],
        normalization_report=report,
    )
    return doc