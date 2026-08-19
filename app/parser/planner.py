"""Planner — build per-page work items + the execution plan (ADR-013 T10).

`Planner.plan` turns a `SourceManifest` into an `ExecutionPlan`: one
`PageWorkItem` per element of the pre-established `expected_page_set`, each
tagged with the document's route band. It writes the ledger (`plan.json`) and
supports RESUME: pages already `OK` in the ledger AND present in the page store
are excluded from `work_items` (idempotent — never reparse done pages).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING

from .config import ParserConfig
from .loaders import docling_loader
from .engines.base import PageWorkItem
from .page_result import PageStatus
from .source import SourceManifest, _IMAGE_SLUGS
from .storage_pages import Ledger, PageStore

if TYPE_CHECKING:
    from .parts import RoutingDecision


@dataclass
class ExecutionPlan:
    doc_id: str
    source_hash: str
    sha: str
    route: str
    decision: "RoutingDecision | None"
    detected_type: str
    mime: str
    declared_extension: str
    probe: str
    expected_page_set: list[int]
    page_count: int
    page_sizes: dict
    metadata: dict
    config_snapshot: dict
    work_items: list[PageWorkItem] = field(default_factory=list)

    def to_ledger(self) -> dict:
        pages = {}
        for p in self.expected_page_set:
            pages[str(p)] = {"status": "pending", "checksum": "", "engine": None,
                             "attempts": 0, "errors": []}
        return {
            "doc_id": self.doc_id,
            "source_hash": self.source_hash,
            "route": self.route,
            "expected_page_set": self.expected_page_set,
            "page_count": self.page_count,
            "created_at": "",
            "config_snapshot": self.config_snapshot,
            "pages": pages,
            "assembly": {"status": "pending", "assembled_page_set": [], "report": None},
        }

    def to_dict(self) -> dict:
        return asdict(self)


class Planner:
    def __init__(self, page_store: PageStore, ledger: Ledger):
        self.page_store = page_store
        self.ledger = ledger

    def _band(self, manifest: SourceManifest, route: str | None,
              decision: "RoutingDecision | None", config: ParserConfig) -> str:
        # Band resolution: explicit route overrides; a present decision carries
        # its own route (native/enrichment/docling); else auto (image/simple).
        if route in ("native", "enrichment", "docling"):
            band = route
        elif decision is not None and getattr(decision, "route", None):
            band = decision.route
        else:
            band = "image" if manifest.slug in _IMAGE_SLUGS else "simple"

        # Graceful degrade: a docling route without the engine falls back to the
        # native/enrichment band (never crash; never silently lose the doc).
        if band == "docling" and not docling_loader.engine_available():
            band = "enrichment" if config.ocr_enabled else "native"
        return band

    def plan(self, manifest: SourceManifest, route: str | None,
             decision: "RoutingDecision | None", config: ParserConfig,
             resume: bool = False) -> ExecutionPlan:
        band = self._band(manifest, route, decision, config)

        base = ExecutionPlan(
            doc_id=manifest.doc_id,
            source_hash=manifest.source_hash,
            sha=manifest.source_hash,
            route=band,
            decision=decision,
            detected_type=manifest.slug,
            mime=manifest.mime,
            declared_extension=manifest.declared_extension,
            probe=manifest.probe,
            expected_page_set=list(manifest.expected_page_set),
            page_count=manifest.page_count,
            page_sizes={int(k): v for k, v in manifest.page_sizes.items()},
            metadata=dict(manifest.metadata),
            config_snapshot=config.snapshot(),
        )

        # RESUME (D1): genuine page-level resume. When `resume` is on we read the
        # existing ledger and:
        #   * SKIP pages already OK (present in store + ledger) — never reparse
        #     done work (idempotent / incremental).
        #   * RESCHEDULE pages that were FAILED/DEAD (or simply missing from the
        #     store) with attempt incremented so retries don't clobber provenance.
        # When `resume` is off, every expected page is (re)planned at attempt 0.
        # Either way we write the (possibly reduced) plan so the ledger reflects
        # the work actually going to be performed — we never silently clobber a
        # healthy ledger with an all-pending plan.
        ledger_plan = self.ledger.load_plan(manifest.doc_id)
        done: set[int] = set()
        prior_attempt: dict[int, int] = {}
        if resume and ledger_plan:
            for p_s, info in (ledger_plan.get("pages") or {}).items():
                try:
                    pidx = int(p_s)
                except Exception:
                    continue
                st = info.get("status")
                prior_attempt[pidx] = int(info.get("attempts") or 0)
                if st == "ok" and self.page_store.page_exists(manifest.doc_id, pidx):
                    done.add(pidx)
                elif st in ("failed", "dead"):
                    # Reschedule for another attempt; keep prior attempt count so
                    # the ledger's attempt accumulation (G3) reflects the true
                    # number of tries.
                    pass

        for p in manifest.expected_page_set:
            if p in done:
                continue
            base.work_items.append(PageWorkItem(
                doc_id=manifest.doc_id,
                source_hash=manifest.source_hash,
                src_path=manifest.src_path,
                page_index=p,
                route=band,
                decision=decision,
                models_dir=config.docling_models_dir,
                ocr_enabled=config.ocr_enabled,
                attempt=(prior_attempt.get(p, 0) + (1 if resume and p in prior_attempt else 0)),
            ))

        self.ledger.write_plan(manifest.doc_id, base.to_ledger())
        return base
