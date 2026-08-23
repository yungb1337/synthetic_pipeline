"""Assembler + DocumentValidator (ADR-013 T13).

The Assembler folds the per-page `PageResult`s produced by the `Scheduler` into
a single `RecoveredDocument`, then reuses the EXISTING `DocumentBuilder.build`
(unchanged, constraint #2) and the EXISTING `Store.put_image/put_dom/put_raw`
(constraint #3 — `raw/`,`dom/`,`images/` layout untouched) to emit the canonical
DOM. It retries a bounded number of times (backoff) and DEAD-letters a document
when pages are exhausted (constraint #8 — ZERO silent page loss).

`DocumentValidator` is the hard gate: a document is only `parsed` (status OK) when
the assembled page set EXACTLY equals the `expected_page_set` from the source
scan. Any missing page => the document is NOT reported as parsed; exhausted pages
are dead-lettered with an explicit actual-vs-expected record.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from .config import ParserConfig
from .parts import RecoveredDocument
from .dom import DocumentBuilder
from .dom import reading_order
from .dom.reference_extractor import extract_references
from .loaders import docling_loader
from .page_result import PageResult, PageStatus
from .planner import ExecutionPlan
from .storage import Store
from .storage_pages import Ledger


@dataclass
class AssemblyReport:
    doc_id: str
    status: str                       # "ok" | "partial" | "failed" | "dead"
    expected_pages: int = 0
    actual_pages: int = 0
    missing_pages: list[int] = field(default_factory=list)
    failed_pages: list[int] = field(default_factory=list)
    dead_pages: list[int] = field(default_factory=list)
    dom_key: str | None = None
    raw_key: str | None = None
    document: "object | None" = None
    errors: list[dict] = field(default_factory=list)


class DocumentValidator:
    """Hard gate: assembled_page_set must equal expected_page_set."""

    @staticmethod
    def assembled_page_set(results: list[PageResult]) -> set[int]:
        # §3.13: a page counts as assembled only if it is OK/PARTIAL AND carries
        # recovered content. A PARTIAL page with zero content must NOT count as
        # assembled — it falls into missing/failed and dead-letters instead of
        # being reported as a silent success.
        ok = {r.page_index for r in results
              if r.status == PageStatus.OK and r.content_present}
        partial = {r.page_index for r in results
                   if r.status == PageStatus.PARTIAL and r.content_present}
        return ok | partial

    @staticmethod
    def is_complete(results: list[PageResult], plan: ExecutionPlan) -> bool:
        expected = set(plan.expected_page_set)
        actual = DocumentValidator.assembled_page_set(results)
        return actual == expected

    @staticmethod
    def classify(results: list[PageResult], plan: ExecutionPlan) -> AssemblyReport:
        expected = set(plan.expected_page_set)
        ok = {r.page_index for r in results
              if r.status == PageStatus.OK and r.content_present}
        partial = {r.page_index for r in results
                   if r.status == PageStatus.PARTIAL and r.content_present}
        failed = {r.page_index for r in results if r.status == PageStatus.FAILED}
        dead = {r.page_index for r in results if r.status == PageStatus.DEAD}
        actual = ok | partial
        missing = sorted(expected - actual)
        status = "ok"
        if missing or failed or dead:
            if actual:
                status = "partial"
            else:
                status = "failed"
        return AssemblyReport(
            doc_id=plan.doc_id, status=status,
            expected_pages=len(expected), actual_pages=len(actual),
            missing_pages=missing, failed_pages=sorted(failed),
            dead_pages=sorted(dead),
        )


def _fold_results(results: list[PageResult], plan: ExecutionPlan) -> RecoveredDocument:
    """Fold per-page results into one RecoveredDocument (reusing Recovered* parts)."""
    rec = RecoveredDocument(
        detected_type=plan.detected_type,
        mime=plan.mime,
        declared_extension=plan.declared_extension,
        probe=plan.probe,
        page_count=plan.page_count,
        # D6: Do NOT seed from the 0-based `plan.page_sizes`. Docling blocks carry
        # 1-based page numbers while native blocks are 0-based, so a single shared
        # 0-based seed mis-maps every non-uniform page (page 24 was dropped; 1-23
        # off-by-one). Instead each engine supplies `page_sizes` keyed to ITS OWN
        # blocks' page convention (native 0-based, docling 1-based), and the
        # builder falls back to the document median for any page still missing
        # dims. This keeps b.page -> page_sizes[b.page] consistent per producer.
        reading_order_authoritative=any(r.route == "docling" for r in results),
        routing=plan.decision,  # ADR-011: forwarded when the auto route ran
    )
    seq = 0
    for r in sorted(results, key=lambda x: x.page_index):
        for b in r.blocks:
            b.seq = seq
            seq += 1
            rec.blocks.append(b)
        rec.tables.extend(r.tables)
        rec.images.extend(r.images)
        rec.annotations.extend(r.annotations)
        for k, v in (r.page_sizes or {}).items():
            rec.page_sizes.setdefault(int(k), tuple(v))
    # Carry the source PDF info dict (title/author/subject/...) into the DOM so
    # the page-centric path preserves the legacy native loader's provenance.
    for k in ("title", "author", "creator", "producer", "subject",
              "created", "modified", "language"):
        if plan.metadata.get(k):
            setattr(rec, k, plan.metadata[k])
    # Carry the docling/layout version through from the page results so the DOM
    # provenance records which engine produced the document (constraint #2 reuses
    # DocumentBuilder.build verbatim, which reads rec.docling_version).
    for r in results:
        if getattr(r, "docling_version", None):
            rec.docling_version = r.docling_version
        if getattr(r, "route", None) == "docling" and getattr(r, "engine_version", None):
            rec.docling_version = rec.docling_version or r.engine_version
    return rec


class Assembler:
    def __init__(self, config: ParserConfig, store: Store, ledger: Ledger | None = None):
        self.config = config
        self.store = store
        self.ledger = ledger
        self.builder = DocumentBuilder(config)

    def assemble(self, plan: ExecutionPlan, results: list[PageResult],
                 src_path: str, sha256: str,
                 max_retries: int = 2) -> AssemblyReport:
        """Fold pages -> DocumentBuilder.build -> Store -> emit.

        Retries FAILED/PARTIAL pages a bounded number of times with backoff; when
        a page is exhausted it is marked `DEAD` (dead-letter) with explicit
        actual-vs-expected so the run NEVER silently loses a page.
        """
        report = DocumentValidator.classify(results, plan)
        attempt = 0
        while (report.missing_pages or report.failed_pages) and attempt < max_retries:
            attempt += 1
            time.sleep(min(0.5 * attempt, 2.0))
            # Re-run the failed pages in-process (native/simple/image/enrichment or
            # a fresh heavy attempt). The scheduler is not re-entered here for
            # simplicity; we reuse the engines via a small synchronous re-run.
            results = self._retry_pages(plan, results, report)
            report = DocumentValidator.classify(results, plan)

        if report.missing_pages or report.failed_pages:
            # Exhausted: dead-letter any still-failed/missing pages.
            for p in list(report.failed_pages) + list(report.missing_pages):
                results = self._mark_dead(results, p, plan)
            report = DocumentValidator.classify(results, plan)
            report.status = "dead" if not report.actual_pages else "partial"

        # Fold + build + persist. G4: only write the canonical DOM + raw blob when
        # the document actually assembled (assembled_page_set == expected). Failed
        # / dead docs are dead-lettered with the report and must not emit a
        # misleading `parsed` DOM artifact (downstream keys off po.ok == False).
        rec = _fold_results(results, plan)
        for img in rec.images:
            try:
                img.storage_ref = self.store.put_image(plan.doc_id, img)
            except Exception:
                pass
        is_success = not report.missing_pages and not report.failed_pages and not report.dead_pages
        if is_success:
            # Source bytes are read once; needed by the table-reconstruction
            # evidence-graph recovery (D2) and the geometric bibliography label
            # recovery (D3). Read here so a missing/corrupt source degrades to
            # "no recovery" rather than crashing the assemble.
            src_bytes = _read_src(src_path)
            # D2: run the table-reconstruction safety net on the FOLDED rec (all
            # pages present) so multi-page continuation merge + evidence-graph row
            # recovery only fire when fragments are adjacent.
            rec = docling_loader.reconstruct_tables(rec, src_bytes)
            document = self.builder.build(rec, plan.doc_id, sha256)
            # D3: generic bibliography extraction. `extract_references` is pure and
            # guarded against mis-firing on prose that merely contains `[n]`; it
            # returns ([], {}) when no bibliography signal is found. Source bytes are
            # passed so a `[n]` marker dropped during layout mapping can be recovered
            # geometrically from the left margin (faithful; never fabricated).
            refs, citation_index = extract_references(document.pages, plan.doc_id, src_bytes)
            if refs:
                document.references = refs
                document.citation_index = citation_index
            # D4: complete typed reading sequence (blocks + tables + images), so
            # every semantic content unit appears exactly once in canonical order.
            document.reading_order_full = reading_order.build_reading_order_full(document.pages)
            report.document = document
            try:
                report.dom_key = self.store.put_dom(plan.doc_id, document)
            except Exception as e:
                report.errors.append({"category": "store_dom", "message": str(e)})
            try:
                report.raw_key = self.store.put_raw(plan.doc_id, sha256,
                                                    _read_src(src_path), plan.declared_extension)
            except Exception as e:
                report.errors.append({"category": "store_raw", "message": str(e)})
        else:
            # Dead-letter: no DOM/raw artifact; keep the report (with explicit
            # actual-vs-expected + dead pages) so the run can prove zero loss.
            report.document = None

        if self.ledger is not None:
            try:
                assembled = sorted(DocumentValidator.assembled_page_set(results))
                self.ledger.update_assembly(
                    plan.doc_id,
                    PageStatus(report.status) if report.status in ("ok", "partial", "failed", "dead")
                    else PageStatus.PARTIAL,
                    assembled,
                    {"expected_pages": report.expected_pages,
                     "actual_pages": report.actual_pages,
                     "missing_pages": report.missing_pages,
                     "failed_pages": report.failed_pages,
                     "dead_pages": report.dead_pages},
                )
            except Exception:
                pass
        return report

    def _retry_pages(self, plan: ExecutionPlan, results: list[PageResult],
                     report: AssemblyReport) -> list[PageResult]:
        from .engines.enrichment import EnrichmentEngine
        from .engines.image import ImageEngine
        from .engines.native_pdf import NativePdfEngine
        from .engines.simple import SimpleEngine

        by_page = {r.page_index: r for r in results}
        retry_pages = set(report.failed_pages) | set(report.missing_pages)
        for p in retry_pages:
            item = None
            for wi in plan.work_items:
                if wi.page_index == p:
                    item = wi
                    break
            if item is None:
                continue
            band = item.route or "native"
            try:
                if band == "enrichment":
                    r = EnrichmentEngine(self.config).process(item)
                elif band == "image":
                    r = ImageEngine(self.config).process(item)
                elif band == "simple":
                    r = SimpleEngine(self.config).process(item)
                elif band == "docling":
                    # A3: a docling page must be retried by the heavy engine,
                    # NEVER downgraded to native. This preserves routing parity
                    # and re-enters the Docling layout path (with the same
                    # silent-loss guard via docling_guard_status).
                    from .engines.heavy_docling import HeavyDoclingEngine
                    r = HeavyDoclingEngine(self.config).process(item)
                else:
                    r = NativePdfEngine(self.config).process(item)
            except Exception as e:
                r = PageResult(
                    doc_id=item.doc_id, page_index=p, route=band,
                    status=PageStatus.FAILED,
                    errors=[{"page_no": p + 1, "category": "assemble_retry", "message": str(e)}],
                    source_hash=item.source_hash,
                )
            by_page[p] = r
        return list(by_page.values())

    def _mark_dead(self, results: list[PageResult], page_index: int,
                   plan: ExecutionPlan) -> list[PageResult]:
        by_page = {r.page_index: r for r in results}
        prev = by_page.get(page_index)
        errs = prev.errors if prev is not None else []
        errs = list(errs) + [{"page_no": page_index + 1, "category": "dead_letter",
                              "message": "page exhausted after retries; dead-lettered"}]
        by_page[page_index] = PageResult(
            doc_id=plan.doc_id, page_index=page_index, route="",
            status=PageStatus.DEAD, errors=errs,
            source_hash=plan.source_hash,
        )
        return list(by_page.values())


def _read_src(src_path: str) -> bytes:
    try:
        return open(src_path, "rb").read()
    except Exception:
        return b""
