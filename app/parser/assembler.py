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
        # A page is assembled if:
        # (1) status=OK/PARTIAL AND has content, OR
        # (2) status=OK AND processed without error (even if blank)
        # This ensures blank pages don't destroy the document (F-01 fix).
        ok_with_content = {r.page_index for r in results
                           if r.status == PageStatus.OK and r.content_present}
        partial_with_content = {r.page_index for r in results
                                if r.status == PageStatus.PARTIAL and r.content_present}
        ok_blank = {r.page_index for r in results
                    if r.status == PageStatus.OK and not r.content_present and not r.errors}
        return ok_with_content | partial_with_content | ok_blank

    @staticmethod
    def is_complete(results: list[PageResult], plan: ExecutionPlan) -> bool:
        expected = set(plan.expected_page_set)
        actual = DocumentValidator.assembled_page_set(results)
        return actual == expected

    @staticmethod
    def classify(results: list[PageResult], plan: ExecutionPlan) -> AssemblyReport:
        expected = set(plan.expected_page_set)
        # Use the same logic as assembled_page_set (includes valid blanks)
        ok_with_content = {r.page_index for r in results
                           if r.status == PageStatus.OK and r.content_present}
        partial_with_content = {r.page_index for r in results
                                if r.status == PageStatus.PARTIAL and r.content_present}
        ok_blank = {r.page_index for r in results
                    if r.status == PageStatus.OK and not r.content_present and not r.errors}
        failed = {r.page_index for r in results if r.status == PageStatus.FAILED}
        dead = {r.page_index for r in results if r.status == PageStatus.DEAD}
        actual = ok_with_content | partial_with_content | ok_blank
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
    def __init__(self, config: ParserConfig, store: Store, ledger: Ledger | None = None,
                 scheduler=None):
        """`scheduler` (I-03): the pipeline's shared `Scheduler`. When supplied,
        page retries are executed THROUGH its pools (docling retries go to the
        heavy pool — never run in-process). Optional for backward compatibility;
        tests may construct an Assembler without one, in which case the legacy
        synchronous path is used and heavy pages are executed in-process
        (single-doc/interactive context only).
        """
        self.config = config
        self.store = store
        self.ledger = ledger
        self.scheduler = scheduler
        # I-09: page store for blob reload at fold time (None = no reload;
        # blobs must then be present in the results as passed in).
        self.page_store = None
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

        # I-09: the scheduler released in-RAM image blobs after persisting each
        # page (durable in the page store). Reload the BLOBS (full page
        # objects, preserving in-RAM table/blocks provenance) for the pages
        # that are about to be folded, so `put_image` sees the same bytes as
        # before this change. Only pages missing blob bytes pay the reload.
        if self.page_store is not None:
            results = self._reload_page_blobs(plan, results)

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
            rec.src_bytes = src_bytes
            # D2: run the table-reconstruction safety net on the FOLDED rec (all
            # pages present) so multi-page continuation merge + evidence-graph row
            # recovery only fire when fragments are adjacent.
            rec = docling_loader.reconstruct_tables(rec, src_bytes)
            document = self.builder.build(rec, plan.doc_id, sha256)
            # D4: complete typed reading sequence (blocks + tables + images), so
            # every semantic content unit appears exactly once in canonical order.
            # Already built by DocumentBuilder.build() via SemanticContext —
            # only re-set here for backward compatibility with older DOMs
            # where reading_order_full was absent.
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

    def _reload_page_blobs(self, plan: ExecutionPlan,
                           results: list[PageResult]) -> list[PageResult]:
        """I-09: restore image blob bytes from the durable page store.

        Returns the original list untouched when every image-bearing page
        already carries its blobs (single-doc / no-strip paths). Pages with
        stripped blobs are replaced by their persisted counterparts.
        """
        by_page = {r.page_index: r for r in results}
        out = list(results)
        replaced = False
        for i, r in enumerate(results):
            if not r.images:
                continue
            if all(img.blob for img in r.images):
                continue  # nothing stripped — no reload needed
            try:
                durable = self.page_store.get_page(plan.doc_id, r.page_index)
            except Exception:
                durable = None
            if durable is None:
                continue  # faithful degradation: strip-safe empty blobs stand
            out[i] = durable
            replaced = True
        return out if replaced else results

    def _retry_pages(self, plan: ExecutionPlan, results: list[PageResult],
                     report: AssemblyReport) -> list[PageResult]:
        from .engines.enrichment import EnrichmentEngine
        from .engines.image import ImageEngine
        from .engines.native_pdf import NativePdfEngine
        from .engines.simple import SimpleEngine

        by_page = {r.page_index: r for r in results}
        retry_pages = set(report.failed_pages) | set(report.missing_pages)

        # B2 skip FIRST (applies to both dispatch paths): never re-run a page
        # whose failure was `engine_unavailable` — the engine is cached-unavailable
        # in this process, so a retry is guaranteed to hit the same wall.
        retry_pages = {
            p for p in retry_pages
            if not (by_page.get(p) is not None and any(
                (e.get("category") or "") == "engine_unavailable"
                for e in (by_page[p].errors or [])))
        }

        # I-03: when a scheduler is wired, execute retries THROUGH its pools so
        # a docling retry never runs in-process in the caller's thread (under
        # batch concurrency that meant concurrent in-process Docling
        # conversions — the RAM-spike / std::bad_alloc hazard the scheduler's
        # single-worker in-process pool exists to prevent).
        if self.scheduler is not None:
            if retry_pages:
                retried = self.scheduler.run_plan_for_pages(plan, retry_pages)
                for r in retried:
                    by_page[r.page_index] = r
            return list(by_page.values())

        # Legacy in-process path (no scheduler wired; single-doc / tests only).
        for p in retry_pages:
            # B2: never retry an engine that is unavailable. `engine_unavailable`
            # means the docling converter could not be built in THIS process
            # (e.g. memory-exhaustion cascade); the engine is cached False, so a
            # retry is guaranteed to hit the same wall and only burns wall-time.
            # Those pages keep their FAILED result (dead-lettered below) — a
            # later clean run retries them via the normal scheduler path.
            # B2: never retry an engine that is unavailable. `engine_unavailable`
            # means the docling converter could not be built in THIS process
            # (e.g. memory-exhaustion cascade); the engine is cached False, so a
            # retry is guaranteed to hit the same wall and only burns wall-time.
            # Those pages keep their FAILED result (dead-lettered below) — a
            # later clean run retries them via the normal scheduler path.
            existing = by_page.get(p)
            if existing is not None and any(
                    (e.get("category") or "") == "engine_unavailable"
                    for e in (existing.errors or [])):
                continue
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
