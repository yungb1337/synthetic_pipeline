"""Heavy Docling engine — per-page, INSIDE the heavy worker (ADR-013 T7).

This is THE FIX for the silent `std::bad_alloc`/page-loss problem:
  * Each page is its own `convert_path(src_path, page)` call with
    `page_range=(page+1, page+1)` so the C++ heap is bounded to ONE page.
  * The engine is built lazily INSIDE the worker process (never pickled) and
    reused across page jobs (no N× warm-up).
  * We inspect `ConversionResult.status` + `errors` + actual content (via
    `docling_guard_status`) and NEVER synthesize a complete page from an empty
    stub. A FAILURE/empty page becomes `FAILED`/`PARTIAL` with explicit errors.

The mapping reuses the exact existing helpers from `docling_loader`
(`_map_item`, `_map_table`, `_map_image`, `_recover_formula_text`,
`_layout_model_name`) — no duplicated layout/OCR mapping.
"""
from __future__ import annotations

import threading

from ..config import ParserConfig
from ..loaders import docling_loader
from ..page_result import PageResult, PageStatus
from ..parts import RecoveredBlock, RecoveredDocument, RecoveredImage, RecoveredTable
from .base import DOCLING, PageWorkItem


class HeavyDoclingEngine:
    route_band = DOCLING

    def __init__(self, config: ParserConfig):
        self.config = config
        # A2: cache the source bytes PER DOCUMENT. The formula fallback below
        # re-read `item.src_path` on EVERY page (open+read, ~10-50 MB per
        # page) even though the bytes are already inside the ConversionResult
        # and are identical for every page of the same document. Reading once
        # per document and reusing is O(1) file reads per doc instead of O(N).
        self._src_cache: dict[str, bytes] = {}
        self._src_cache_lock = threading.Lock()

    def _src_bytes(self, src_path: str) -> bytes:
        with self._src_cache_lock:
            cached = self._src_cache.get(src_path)
            if cached is not None:
                return cached
        try:
            with open(src_path, "rb") as fh:
                data = fh.read()
        except Exception:
            data = b""
        with self._src_cache_lock:
            self._src_cache[src_path] = data
        return data

    def process(self, item: PageWorkItem) -> PageResult:
        try:
            result = docling_loader.convert_path(
                item.src_path, item.page_index, item.models_dir,
                table_mode=item.docling_table_mode, ocr=item.docling_ocr
            )
        except docling_loader.DoclingConvertError as e:
            # B4: this page's convert() call failed though the engine itself is
            # available. That is a RETRYABLE per-page failure — the assembler's
            # B2 skip only ever skips the genuine `engine_unavailable` category,
            # so this page gets retried on the next pass instead of being
            # dead-lettered as a dead engine.
            return PageResult(
                doc_id=item.doc_id, page_index=item.page_index, route=DOCLING,
                status=PageStatus.FAILED,
                errors=[{"page_no": item.page_index + 1, "category": "docling_convert",
                         "message": str(e)}],
                source_hash=item.source_hash,
            )
        if result is None:
            # None now means ONLY "engine unavailable" (convert_path's contract).
            return PageResult(
                doc_id=item.doc_id, page_index=item.page_index, route=DOCLING,
                status=PageStatus.FAILED,
                errors=[{"page_no": item.page_index + 1, "category": "engine_unavailable",
                         "message": "docling engine unavailable"}],
                source_hash=item.source_hash,
            )

        # --- silent-loss detection (the FIX) ---------------------------------
        status_name, errors, expected, produced = docling_loader.docling_guard_status(result)
        if status_name in ("FAILURE", "SKIPPED"):
            return PageResult(
                doc_id=item.doc_id, page_index=item.page_index, route=DOCLING,
                status=PageStatus.FAILED, errors=errors or [
                    {"page_no": item.page_index + 1, "category": "docling_failure",
                     "message": f"conversion status={status_name}"}],
                source_hash=item.source_hash,
            )
        if status_name == "PARTIAL_SUCCESS" and produced == 0:
            return PageResult(
                doc_id=item.doc_id, page_index=item.page_index, route=DOCLING,
                status=PageStatus.FAILED, errors=errors or [
                    {"page_no": item.page_index + 1, "category": "docling_empty",
                     "message": "partial success with no produced page content"}],
                source_hash=item.source_hash,
            )

        # --- map the single page (scope to item.page_index+1) ---------------
        try:
            doc = result.document
            rec = RecoveredDocument(detected_type="pdf", mime="application/pdf")
            rec.reading_order_authoritative = True
            rec.docling_version = docling_loader.engine_name()
            converter = docling_loader.get_engine()
            rec.layout_model = docling_loader._layout_model_name(converter) if converter else None

            target = item.page_index + 1
            # A1: index the document's items BY PAGE ONCE, then look up this
            # page. The previous loop called `doc.iterate_items()` once per
            # page and filtered `page_no != target`, which is O(N x M) item
            # iterations across the whole document (N pages x M items each) —
            # the same O(N^2) class I-04 fixed in the enrichment band, still
            # open here. `iterate_items` is not free; this turns a 30-page
            # document's ~900 iterations into ~90.
            by_page: dict[int, list] = {}
            for entry in doc.iterate_items():
                item_ = entry[0] if isinstance(entry, tuple) and entry else entry
                try:
                    prov = item_.prov[0] if getattr(item_, "prov", None) else None
                    page_no = int(getattr(prov, "page_no", 0) or 0)
                except Exception:
                    page_no = 0
                by_page.setdefault(page_no, []).append(item_)
            for item_ in by_page.get(target, []):
                docling_loader._map_item(item_, rec, doc)

            # formula fallback: read source bytes (single reused path)
            # A2: reuse the per-document cached bytes instead of re-reading the
            # file on every page.
            try:
                data = self._src_bytes(item.src_path)
                docling_loader._recover_formula_text(data, rec)
            except Exception:
                pass

            content = bool(rec.blocks) or any(t.rows for t in rec.tables)
            status = PageStatus.OK if content else PageStatus.PARTIAL
            if status == PageStatus.PARTIAL and not errors:
                errors = [{"page_no": target, "category": "docling_empty_page",
                           "message": "docling returned no content for this page"}]

            # Page geometry (D6): Docling's `page_no` is 1-based and matches the
            # `page` field on every block/table/image this engine emits, so key
            # `page_sizes` 1-based here. The assembler merges these WITHOUT the
            # 0-based `plan.page_sizes` seed, so a non-uniform PDF maps each page
            # to its true dimensions (no off-by-one).
            page_sizes: dict[int, tuple[float, float]] = {}
            try:
                ps = getattr(doc.pages.get(target), "size", None)
                if ps is not None and getattr(ps, "width", None) is not None:
                    page_sizes[target] = (float(ps.width), float(ps.height))
            except Exception:
                pass

            return PageResult(
                doc_id=item.doc_id, page_index=item.page_index, route=DOCLING,
                status=status, blocks=rec.blocks, tables=rec.tables, images=rec.images,
                page_sizes=page_sizes,
                docling_version=rec.docling_version, engine_version=rec.docling_version,
                errors=errors, source_hash=item.source_hash,
            )
        except Exception as e:
            return PageResult(
                doc_id=item.doc_id, page_index=item.page_index, route=DOCLING,
                status=PageStatus.FAILED,
                errors=[{"page_no": item.page_index + 1, "category": "docling_map",
                         "message": str(e)}],
                source_hash=item.source_hash,
            )