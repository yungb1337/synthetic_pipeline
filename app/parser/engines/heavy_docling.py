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

from ..config import ParserConfig
from ..loaders import docling_loader
from ..page_result import PageResult, PageStatus
from ..parts import RecoveredBlock, RecoveredDocument, RecoveredImage, RecoveredTable
from .base import DOCLING, PageWorkItem


class HeavyDoclingEngine:
    route_band = DOCLING

    def __init__(self, config: ParserConfig):
        self.config = config

    def process(self, item: PageWorkItem) -> PageResult:
        result = docling_loader.convert_path(item.src_path, item.page_index, item.models_dir)
        if result is None:
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
            for entry in doc.iterate_items():
                item_ = entry[0] if isinstance(entry, tuple) and entry else entry
                try:
                    prov = item_.prov[0] if getattr(item_, "prov", None) else None
                    page_no = int(getattr(prov, "page_no", 0) or 0)
                except Exception:
                    page_no = 0
                if page_no != target:
                    continue
                docling_loader._map_item(item_, rec, doc)

            # formula fallback: read source bytes (single reused path)
            try:
                data = open(item.src_path, "rb").read()
                docling_loader._recover_formula_text(data, rec)
            except Exception:
                pass

            content = bool(rec.blocks) or any(t.rows for t in rec.tables)
            status = PageStatus.OK if content else PageStatus.PARTIAL
            if status == PageStatus.PARTIAL and not errors:
                errors = [{"page_no": target, "category": "docling_empty_page",
                           "message": "docling returned no content for this page"}]
            return PageResult(
                doc_id=item.doc_id, page_index=item.page_index, route=DOCLING,
                status=status, blocks=rec.blocks, tables=rec.tables, images=rec.images,
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