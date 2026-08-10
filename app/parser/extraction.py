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
        self._router = None  # lazily built on the auto path

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

        route, decision, rec = None, None, None
        # ADR-011: compute the route after detection, before dispatch (only in
        # "auto" for PDFs; manual native/docling overrides pass through). The
        # router never touches the loaders and only decides.
        try:
            route, decision = self._compute_route(data, detected)
        except Exception:
            route, decision = None, None  # a routing failure never kills the parse

        try:
            rec = self.loaders.load(detected, data, route=route)
            if decision is not None:
                rec.routing = decision
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
            "route": route,  # ADR-011: which tier ran (or None)
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

    def _compute_route(self, data: bytes, detected) -> tuple[str | None, object | None]:
        """Decide the extraction tier + capture the RoutingDecision (ADR-011).

        Manual overrides (`layout_backend` in {"native","docling"}) pass through
        unchanged and record NO routing decision (old behaviour). "auto" routes
        PDFs via the router; every other format keeps the legacy native path.
        """
        lb = self.config.layout_backend
        if lb == "docling":
            return "docling", None
        if lb == "native":
            return "native", None
        if lb == "auto":
            if getattr(detected, "slug", None) == "pdf":
                dec = self._get_router().route(data, detected)
                if dec is not None:
                    return dec.route, dec
            return None, None
        return None, None

    def _get_router(self):
        if self._router is None:
            from app.routing import Router, RoutingConfig

            rc = self.config.routing or RoutingConfig()
            self._router = Router(rc)
        return self._router

    def _emit(self, name, doc_id, payload=None):
        base = {"document_id": doc_id, "parser_version": self.config.parser_version}
        if payload:
            base.update(payload)
        self.events.emit(name, base)