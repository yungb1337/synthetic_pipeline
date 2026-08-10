"""Enrichment band OCR post-pass (ADR-012, spec §7).

For a PDF the router sent to Enrichment, we run the native loader first, then
OCR any page that produced no text blocks. Renders EXACTLY ONE Pixmap per
empty page (the only place a render happens in the routing run — all routed
tiers otherwise match the native loader's no-render rule) and reuses the
existing on-prem `ocr.ocr_bytes` (ADR image-path OCR wrapper; no new OCR dep).

Per-page try/except: a page that fails to render/OCR is skipped (recorded as a
no-op), never a crash and never a fabricated negative (§11). Reading order stays
non-authoritative so the native heuristic reorders every block (incl. the OCR
ones) — page-level orchestration is explicitly out of v1 (§16).
"""
from __future__ import annotations

from .. import ocr
from ..config import ParserConfig
from ..parts import RecoveredBlock, RecoveredDocument

# v1 cap: never OCR more empty pages than this per document (bounds CPU cost).
DEFAULT_MAX_PAGES = 16


def enrich_scanned_pages(
    rec: RecoveredDocument,
    config: ParserConfig,
    *,
    data: bytes | None = None,
    pages: list[int] | None = None,
    ocr_fn=None,
    max_pages: int = DEFAULT_MAX_PAGES,
) -> RecoveredDocument:
    """OCR pages of `rec` that yielded no text blocks, in place; returns it.

    `pages` is the reserved named-arg seam for future page/region selectivity
    (v1 defaults to ALL empty pages). `data` is the source PDF bytes needed to
    render the empty pages (a render happens ONLY here). `ocr_fn` is injected
    so tests can substitute a deterministic engine.
    """
    if not config.ocr_enabled:
        return rec
    if not data:
        return rec  # nothing to render -> enrichment can't run (safe no-op)
    ocr_fn = ocr_fn or ocr.ocr_bytes

    page_count = rec.page_count or (max(rec.page_sizes) + 1 if rec.page_sizes else 0)
    if page_count <= 0:
        return rec

    # pages with at least one already-recovered block (text or prior OCR)
    populated = {b.page for b in rec.blocks} if rec.blocks else set()
    empty = [p for p in range(page_count) if p not in populated]
    if pages is not None:
        wanted = set(pages)
        empty = [p for p in empty if p in wanted]
    if not empty:
        return rec

    try:
        import fitz
    except Exception:
        return rec

    try:
        doc = fitz.open(stream=data, filetype="pdf")
    except Exception:
        return rec

    engine = ocr.engine_name()
    done = 0
    for pno in empty:
        if done >= max_pages:
            break
        try:
            page = doc[pno]
            pix = page.get_pixmap()          # ONE render per page, only here
            png = pix.tobytes("png")
            for text, bbox, conf in ocr_fn(png):
                clean = text.strip()
                if not clean:
                    continue
                rec.blocks.append(
                    RecoveredBlock(
                        page=pno,
                        kind="paragraph",
                        text=clean,
                        bbox=bbox,
                        seq=len(rec.blocks),
                        confidence=conf if conf <= 1.0 else conf / 100.0,
                        source="ocr",
                        ocr_engine=engine,
                    )
                )
            done += 1
        except Exception:
            continue                     # page failed; keep going (§11)

    return rec