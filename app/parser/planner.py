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
from .engines.base import PageWorkItem
from .loaders import docling_loader
from .source import _IMAGE_SLUGS, SourceManifest
from .storage_pages import Ledger, PageStore

if TYPE_CHECKING:
    from .parts import RoutingDecision


@dataclass
class ExecutionPlan:
    doc_id: str
    source_hash: str
    sha: str
    route: str
    decision: RoutingDecision | None
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

    def to_ledger(
        self, prior_pages: dict | None = None, prior_assembly: dict | None = None
    ) -> dict:
        pages = {}
        for p in self.expected_page_set:
            if prior_pages and str(p) in prior_pages:
                pages[str(p)] = prior_pages[str(p)]
            else:
                pages[str(p)] = {
                    "status": "pending",
                    "checksum": "",
                    "engine": None,
                    "attempts": 0,
                    "errors": [],
                }
        return {
            "doc_id": self.doc_id,
            "source_hash": self.source_hash,
            "route": self.route,
            "expected_page_set": self.expected_page_set,
            "page_count": self.page_count,
            "created_at": "",
            "config_snapshot": self.config_snapshot,
            "pages": pages,
            "assembly": prior_assembly
            or {"status": "pending", "assembled_page_set": [], "report": None},
        }

    def to_dict(self) -> dict:
        return asdict(self)


class Planner:
    def __init__(self, page_store: PageStore, ledger: Ledger):
        self.page_store = page_store
        self.ledger = ledger

    def _band(
        self,
        manifest: SourceManifest,
        route: str | None,
        decision: RoutingDecision | None,
        config: ParserConfig,
    ) -> str:
        # Band resolution: explicit layout_backend or route overrides;
        # a present decision carries its own route (native/enrichment/docling); else auto.
        if config.layout_backend == "docling" and manifest.slug == "pdf":
            band = "docling"
        elif route in ("native", "enrichment", "docling"):
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

    def _page_band(
        self,
        manifest: SourceManifest,
        base_band: str,
        decision: RoutingDecision | None,
        config: ParserConfig,
        page_idx: int,
        table_pages: set[int] | None = None,
        ocr_pages: set[int] | None = None,
        simple_table_pages: set[int] | None = None,
    ) -> str:
        """Determines the per-page execution band.

        Two-Tier Table Routing (P2):
        - Clean non-table digital pages stay on native fast path (~35-45 p/s).
        - Simple bordered table pages (in simple_table_pages) stay on native path (~30-40 p/s).
        - Complex / borderless table pages escalate to single-page Docling TableFormer.
        - Pages requiring OCR route to enrichment.
        """
        if base_band in ("image", "simple"):
            return base_band

        # If user explicitly configured layout_backend="docling", execute docling
        if config.layout_backend == "docling":
            return (
                "docling"
                if docling_loader.engine_available()
                else ("enrichment" if config.ocr_enabled else "native")
            )

        # If route was explicitly forced (no routing decision), respect base_band
        if decision is None:
            return base_band

        p_num = page_idx + 1

        # Priority 1: Table-bearing pages
        if table_pages is not None and p_num in table_pages:
            # If table is a simple bordered grid, native path handles it with find_tables
            if simple_table_pages is not None and p_num in simple_table_pages:
                return "native"
            # Complex/borderless tables escalate to single-page Docling TableFormer
            if docling_loader.engine_available():
                return "docling"
            return "enrichment" if config.ocr_enabled else "native"

        # Priority 2: Pages requiring OCR route to enrichment
        if (ocr_pages is not None and p_num in ocr_pages) or base_band == "enrichment":
            return "enrichment" if config.ocr_enabled else "native"

        # Priority 3: Clean non-table digital pages stay on native fast path
        return "native"

    def plan(
        self,
        manifest: SourceManifest,
        route: str | None,
        decision: RoutingDecision | None,
        config: ParserConfig,
        resume: bool = False,
    ) -> ExecutionPlan:
        band = self._band(manifest, route, decision, config)

        table_pages = None
        simple_table_pages = set()
        ocr_pages = None
        if manifest.slug == "pdf" and manifest.src_path:
            try:
                import pdf_inspector

                res = pdf_inspector.process_pdf(manifest.src_path)
                p_tables = getattr(res, "pages_with_tables", None)
                if p_tables:
                    table_pages = set(p_tables)
                p_ocr = getattr(res, "pages_needing_ocr", None)
                enc = getattr(res, "has_encoding_issues", False)
                ptype = getattr(res, "pdf_type", "text_based")
                if p_ocr or enc or ptype in ("scanned", "image_based"):
                    if p_ocr:
                        ocr_pages = set(p_ocr)
                    if enc or ptype in ("scanned", "image_based"):
                        ocr_pages = set(range(1, manifest.page_count + 1))
            except Exception:
                pass

            # Two-tier table probe: test if detected tables are simple bordered grids
            if table_pages:
                try:
                    import fitz

                    with fitz.open(manifest.src_path) as doc:
                        for p_num in table_pages:
                            p_idx = p_num - 1
                            if 0 <= p_idx < len(doc):
                                page = doc[p_idx]
                                finder = page.find_tables(strategy="lines")
                                if finder and finder.tables:
                                    all_simple = True
                                    for t in finder.tables:
                                        try:
                                            rows = t.extract()
                                            if (
                                                not rows
                                                or len(rows) < 2
                                                or len(rows[0]) < 2
                                            ):
                                                all_simple = False
                                                break
                                            ncols = len(rows[0])
                                            if ncols > 10:
                                                all_simple = False
                                                break
                                            if not all(len(r) == ncols for r in rows):
                                                all_simple = False
                                                break
                                        except Exception:
                                            all_simple = False
                                            break
                                    if all_simple:
                                        simple_table_pages.add(p_num)
                except Exception:
                    pass

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
            p_band = self._page_band(
                manifest,
                band,
                decision,
                config,
                p,
                table_pages,
                ocr_pages,
                simple_table_pages=simple_table_pages,
            )
            base.work_items.append(
                PageWorkItem(
                    doc_id=manifest.doc_id,
                    source_hash=manifest.source_hash,
                    src_path=manifest.src_path,
                    page_index=p,
                    route=p_band,
                    decision=decision,
                    models_dir=config.docling_models_dir,
                    ocr_enabled=config.ocr_enabled,
                    attempt=(
                        prior_attempt.get(p, 0)
                        + (1 if resume and p in prior_attempt else 0)
                    ),
                    docling_table_mode=config.docling_table_mode,
                    docling_ocr=config.docling_ocr,
                )
            )

        self.ledger.write_plan(
            manifest.doc_id,
            base.to_ledger(
                prior_pages=ledger_plan.get("pages")
                if resume and ledger_plan
                else None,
                prior_assembly=ledger_plan.get("assembly")
                if resume and ledger_plan
                else None,
            ),
        )
        return base
