"""Extraction orchestrator: single-pass Detect -> Load -> Build DOM -> Store -> emit.

This is the module's public entry point. Workers/API call into `Extractor.extract`.
Produces a `ParseOutcome` reflecting success / unsupported / unresolved — never
throws for bad input, so an idempotent worker can retry or dead-letter cleanly.
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field

from . import detection
from .config import ParserConfig
from .dom import Document, DocumentBuilder
from .events import EventPublisher
from .loaders import Loaders
from .loaders.loaders import UnsupportedFormat
from .storage import Store


@dataclass
class ParseOutcome:
    document_id: str
    status: str                # "parsed" | "unsupported" | "unresolved" | "failed"
    document: Document | None = None
    detected: detection.Detected | None = None
    report: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == "parsed"


class Extractor:
    def __init__(
        self,
        config: ParserConfig,
        store: Store,
        events: EventPublisher | None = None,
    ):
        self.config = config
        self.store = store
        self.events = events or EventPublisher()
        self.loaders = Loaders(config)
        self.builder = DocumentBuilder(config)

    def extract(self, data: bytes, filename: str = "", sha256: str | None = None) -> ParseOutcome:
        t0 = time.time()
        # the corpus scan already has the content hash; only recompute when the
        # caller could not provide it (single-doc / interactive path).
        sha = sha256 or hashlib.sha256(data).hexdigest()
        doc_id = f"d-{sha[:16]}"

        detected = detection.detect(data, filename)
        if detected.unresolved:
            self._emit("document.parse_failed", doc_id, {"reason": "unresolved", "slug": detected.slug})
            return ParseOutcome(doc_id, "unresolved", None, detected)

        if len(data) > self.config.max_file_bytes:
            self._emit("document.parse_failed", doc_id, {"reason": "too_large", "bytes": len(data)})
            return ParseOutcome(doc_id, "failed", None, detected, {"error": "file exceeds max_file_bytes"})

        try:
            rec = self.loaders.load(detected, data)
        except UnsupportedFormat as e:
            self._emit("document.parse_failed", doc_id, {"reason": f"unsupported:{e}"})
            return ParseOutcome(doc_id, "unsupported", None, detected, {"error": str(e)})

        # persist extracted images (same pass; image bytes are already in rec)
        for img in rec.images:
            img.storage_ref = self.store.put_image(doc_id, img)

        document = self.builder.build(rec, doc_id, sha)
        dom_key = self.store.put_dom(doc_id, document)
        raw_key = self.store.put_raw(doc_id, sha, data, detected.slug)

        elapsed = (time.time() - t0) * 1000
        report = {
            "elapsed_ms": round(elapsed, 1),
            "blocks": document.num_blocks(),
            "tables": document.num_tables(),
            "images": document.num_images(),
            "pages": len(document.pages),
            "ocr": document.provenance.ocr_engine if document.provenance else None,
            "dom_key": dom_key,
            "raw_key": raw_key,
        }
        self._emit(
            "document.parsed.v1",
            doc_id,
            {
                "type": detected.slug,
                "probe": detected.probe,
                "parser_version": self.config.parser_version,
                "sha256": sha,
                **report,
            },
        )
        return ParseOutcome(doc_id, "parsed", document, detected, report)

    def _emit(self, name, doc_id, payload=None):
        base = {"document_id": doc_id, "parser_version": self.config.parser_version}
        if payload:
            base.update(payload)
        self.events.emit(name, base)