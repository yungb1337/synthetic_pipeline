"""Extraction orchestrator (ADR-013 T14) — thin facade over the page-centric engine.

The public entry point `Extractor.extract(data, filename, sha256=None) ->
ParseOutcome` is UNCHANGED (signature + report keys). Internally it now drives
the page-centric pipeline:

    SourceScan.scan  ->  Planner.plan  ->  Scheduler.run_plan  ->  Assembler.assemble

The document is assembled by reusing `DocumentBuilder.build` (unchanged,
constraint #2) and persisted through `Store.put_image/put_dom/put_raw`
(additive `pages/`+`manifest/` prefixes for the page store + ledger, constraint
#3 — the `raw/`,`dom/`,`images/` layout is untouched). The reported `pages` is
validated against `expected_page_set` so a document is never reported `parsed`
when `actual < expected` (constraint #8 — ZERO silent page loss).
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from . import detection
from .config import ParserConfig
from .dom import DocumentBuilder
from .events import EventPublisher
from .loaders import Loaders
from .loaders.loaders import UnsupportedFormat
from .storage import Store

# Page-centric seams (additive; never break the legacy contract).
from .assembler import Assembler
from .planner import Planner
from .scheduler import Scheduler
from .source import SourceScan, SourceScanError
from .storage_pages import Ledger, PageStore


@dataclass
class ParseOutcome:
    document_id: str
    status: str                # "parsed" | "unsupported" | "unresolved" | "failed"
    document: object | None = None
    detected: object | None = None
    report: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == "parsed"


@dataclass
class _SharedScheduler:
    """Process-wide shared scheduler (set by the executor / CLI)."""

    scheduler: Optional[Scheduler] = None


def get_shared_scheduler() -> Optional[Scheduler]:
    return _SharedScheduler.scheduler


def set_shared_scheduler(s: Optional[Scheduler]) -> None:
    _SharedScheduler.scheduler = s


class Extractor:
    def __init__(
        self,
        config: ParserConfig,
        store: Store,
        events: EventPublisher | None = None,
        scheduler: Scheduler | None = None,
        page_store: PageStore | None = None,
        ledger: Ledger | None = None,
    ):
        self.config = config
        self.store = store
        self.events = events or EventPublisher()
        self.loaders = Loaders(config)
        self.builder = DocumentBuilder(config)  # reused for any legacy path
        self._router = None  # lazily built on the auto path

        # Derive the store root for additive page store + ledger.
        root = getattr(store, "root", None)
        if root is None:
            root = Path(getattr(store, "root_path", "."))
        self.root = Path(root)
        self.page_store = page_store or PageStore(str(self.root))
        self.ledger = ledger or Ledger(str(self.root))
        self.planner = Planner(self.page_store, self.ledger)
        self.assembler = Assembler(config, store, self.ledger)

        # Use the shared scheduler (injected by the executor) if present, else
        # build a one-off scheduler that runs heavy pages in-process (no fork for
        # a single-doc interactive call).
        self.scheduler = scheduler or get_shared_scheduler()
        self._own_scheduler = self.scheduler is None
        if self.scheduler is None:
            self.scheduler = Scheduler(
                config, page_store=self.page_store, ledger=self.ledger,
                prefer_in_process_heavy=True,
            )

    def extract(self, data: bytes, filename: str = "", sha256: str | None = None,
                 resume: bool = False) -> ParseOutcome:
        t0 = time.time()
        sha = sha256 or hashlib.sha256(data).hexdigest()

        t_detect = time.time()
        detected = detection.detect(data, filename)
        if detected.unresolved:
            self._emit("document.parse_failed", None, {"reason": "unresolved", "slug": detected.slug})
            return ParseOutcome(None, "unresolved", None, detected)

        if len(data) > self.config.max_file_bytes:
            self._emit("document.parse_failed", None, {"reason": "too_large", "bytes": len(data)})
            return ParseOutcome(None, "failed", None, detected, {"error": "file exceeds max_file_bytes"})

        # --- route (legacy router kept; only informs the Planner band) --------
        t_route0 = time.time()
        route, decision = None, None
        try:
            route, decision = self._compute_route(data, detected)
        except Exception:
            route, decision = None, None
        t_route1 = time.time()

        # --- source scan (expected page set) ---------------------------------
        t_scan0 = time.time()
        try:
            manifest = SourceScan.scan(data, filename, self._fs_store())
        except UnsupportedFormat as e:
            self._emit("document.parse_failed", None, {"reason": f"unsupported:{e}"})
            return ParseOutcome(None, "unsupported", None, detected, {"error": str(e)})
        except SourceScanError as e:
            # Corrupt / unreadable source: never report `parsed` with 0 pages.
            self._emit("document.parse_failed", None, {"reason": f"source_scan:{e}"})
            return ParseOutcome(None, "failed", None, detected, {"error": str(e)})
        t_scan1 = time.time()

        doc_id = manifest.doc_id

        # --- plan ------------------------------------------------------------
        t_plan0 = time.time()
        # D1: genuine page-level resume. The batch executor opts in (resume=True)
        # so re-runs skip already-OK pages and reschedule FAILED/DEAD pages
        # instead of reparsing everything. A single Extractor.extract() call
        # defaults to resume=False (always reproduce the full document).
        plan = self.planner.plan(manifest, route, decision, self.config, resume=resume)
        t_plan1 = time.time()

        # --- execute pages (scheduler persists each result) ------------------
        t_run0 = time.time()
        results = self.scheduler.run_plan(plan)
        t_run1 = time.time()

        # --- assemble + validate (hard gate) --------------------------------
        t_assemble0 = time.time()
        report = self.assembler.assemble(plan, results, manifest.src_path, sha,
                                          max_retries=self.config.page_retries)
        t_assemble1 = time.time()

        document = report.document
        elapsed = (time.time() - t0) * 1000
        timings = {
            "detect_ms": round((t_route0 - t_detect) * 1000, 1),
            "route_ms": round((t_route1 - t_route0) * 1000, 1),
            "scan_ms": round((t_scan1 - t_scan0) * 1000, 1),
            "plan_ms": round((t_plan1 - t_plan0) * 1000, 1),
            "run_ms": round((t_run1 - t_run0) * 1000, 1),
            "assemble_ms": round((t_assemble1 - t_assemble0) * 1000, 1),
            "total_ms": round(elapsed, 1),
        }
        doc_report = {
            "elapsed_ms": round(elapsed, 1),
            "timings": timings,
            "blocks": document.num_blocks() if document else 0,
            "tables": document.num_tables() if document else 0,
            "images": document.num_images() if document else 0,
            "pages": report.actual_pages,
            "expected_pages": report.expected_pages,
            "ocr": document.provenance.ocr_engine if document and document.provenance else None,
            "route": route,
            "dom_key": report.dom_key,
            "raw_key": report.raw_key,
        }

        # ZERO silent page loss: only report `parsed` when assembled == expected.
        if report.status == "ok":
            status = "parsed"
        elif report.status == "partial":
            status = "parsed" if report.actual_pages == report.expected_pages else "partial"
            # a partial with actual==expected (all PARTIAL but complete set) is parsed
            if report.actual_pages == report.expected_pages:
                status = "parsed"
        else:
            status = "failed"

        self._emit(
            "document.parsed.v1",
            doc_id,
            {
                "type": detected.slug,
                "probe": detected.probe,
                "parser_version": self.config.parser_version,
                "sha256": sha,
                "expected_pages": report.expected_pages,
                "actual_pages": report.actual_pages,
                "assembly_status": report.status,
                **doc_report,
            },
        )
        if status != "parsed":
            self._emit("document.parse_failed", doc_id,
                       {"reason": "incomplete", "expected": report.expected_pages,
                        "actual": report.actual_pages,
                        "missing": report.missing_pages,
                        "failed": report.failed_pages,
                        "dead": report.dead_pages})
        return ParseOutcome(doc_id, status, document, detected, doc_report)

    # --- small helpers -------------------------------------------------------
    def _fs_store(self):
        # SourceScan needs a FilesystemStore (it uses `.root`); wrap if needed.
        if isinstance(self.store, Store) and getattr(self.store, "root", None) is not None:
            return self.store
        from .storage import FilesystemStore

        return FilesystemStore(str(self.root))

    def _compute_route(self, data: bytes, detected) -> tuple[str | None, object | None]:
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
