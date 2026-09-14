"""Native PDF engine — per-page PyMuPDF extraction (ADR-013 T5).

This is the SINGLE source of truth for native PDF text/font/bold + `find_tables`
+ images, extracted one page at a time. The extraction loop is lifted verbatim
from the former `Loaders._pdf` and reduced to a single page via
`_native_page_from_doc` (the reusable core). The legacy `Loaders._pdf` now calls
that same core so there is no behaviour regression and no duplicated logic.
"""
from __future__ import annotations

import hashlib
import threading

from ..config import ParserConfig
from ..mime import MIME as _MIME
from ..page_result import PageResult, PageStatus
from ..parts import RecoveredBlock, RecoveredTable, RecoveredImage
from .base import NATIVE, PageWorkItem


def _image_mime(ext: str) -> str:
    return {
        "png": _MIME["png"],
        "jpg": _MIME["jpg"],
        "jpeg": _MIME["jpg"],
        "tiff": _MIME["tiff"],
    }.get(ext, "image/" + ext)


def _native_page_from_doc(page, page_index: int, config: ParserConfig,
                          body_med: float | None = None) -> PageResult:
    """Extract ONE already-open fitz `page` into a `PageResult`.

    Pure per-page extraction. A genuinely blank page still returns `OK` with
    empty parts and no error (a successfully-processed but empty page).

    `body_med` is the document-wide median font size used for heading
    classification (F1: restores parity with the legacy `Loaders._pdf`, which
    compared each block against the whole-document body size rather than a
    per-page median — per-page medians drift on title/cover pages and mislabel
    headings). When `None`, a per-page median fallback is used so the helper
    stays callable standalone.
    """
    all_blocks: list = []

    for blk in page.get_text("dict").get("blocks", []):
        if blk.get("type") != 0:
            continue
        bbox = tuple(blk["bbox"])
        parts_text = []
        size = 0.0
        bold = False
        for line in blk.get("lines", []):
            line_text = ""
            for span in line.get("spans", []):
                line_text += span.get("text", "")
                size = max(size, span.get("size", 0.0))
                if span.get("flags") & 16:
                    bold = True
            parts_text.append(line_text)
        text = "\n".join(s.strip() for s in parts_text).strip()
        if not text:
            continue
        all_blocks.append(
            RecoveredBlock(
                page=page_index, kind="paragraph", text=text, bbox=tuple(bbox),
                seq=len(all_blocks), font_size=size, bold=bold, source="text",
            )
        )

    if config.pdf_extract_tables:
        try:
            finder = page.find_tables()
        except Exception:
            finder = None
        if finder is not None:
            for t in getattr(finder, "tables", []):
                try:
                    rows = t.extract()
                except Exception:
                    rows = []
                if not rows:
                    continue
                header = [str(c).strip() for c in rows[0]]
                data = [[str(c).strip() for c in r] for r in rows[1:]]
                bbox = getattr(t, "bbox", None)
                all_blocks.append(
                    RecoveredTable(page=page_index, bbox=tuple(bbox) if bbox else None,
                                   header=header, rows=data, source="native")
                )

    images: list[RecoveredImage] = []
    try:
        for xref in page.get_images(full=True):
            einfo = page.parent.extract_image(xref[0])
            rects = page.get_image_rects(xref[0])
            bbox = tuple(rects[0]) if rects else None
            ext = einfo.get("ext", "png")
            mime = _image_mime(ext)
            blob = einfo["image"]
            images.append(
                RecoveredImage(page=page_index, bbox=bbox, mime=mime,
                               checksum=hashlib.sha256(blob).hexdigest(), blob=blob)
            )
    except Exception:
        pass

    # heading classification by font size vs median body size. F1: prefer the
    # document-wide `body_med` (parity with legacy `Loaders._pdf`); fall back to
    # a per-page median when none is supplied (standalone helper / test usage).
    if body_med is None:
        sizes = [b.font_size for b in all_blocks if isinstance(b, RecoveredBlock) and b.font_size]
        body_med = sorted(sizes)[len(sizes) // 2] if sizes else 12.0
    for b in all_blocks:
        if isinstance(b, RecoveredBlock) and b.font_size and b.font_size > body_med * config.pdf_heading_threshold_ratio:
            b.kind = "heading"

    # D6: supply this page's geometry keyed 0-based (== `page_index` == the
    # `page` field on every block/table/image this engine emits). The assembler
    # merges these per-producer, so a 0-based native key and a 1-based docling key
    # never collide; the builder fills any remaining gaps with the document median.
    try:
        pw, ph = float(page.rect.width), float(page.rect.height)
    except Exception:
        pw = ph = None
    page_sizes = {page_index: (pw, ph)} if pw is not None else {}

    return PageResult(
        doc_id="", page_index=page_index, route=NATIVE, status=PageStatus.OK,
        blocks=[b for b in all_blocks if isinstance(b, RecoveredBlock)],
        tables=[t for t in all_blocks if isinstance(t, RecoveredTable)],
        images=images,
        page_sizes=page_sizes,
    )


class NativePdfEngine:
    """The cheap native path: PyMuPDF + reading order + find_tables.

    This engine processes EACH PAGE independently (so the per-page persistence
    model remains), but optimizes document-wide operations: the PDF is opened
    once per engine instance and the median font size is computed once and
    cached (F-05/F-06 fix). The handle is cleared only on explicit close or
    when switching to a different document.

    I-01 fix: locking is PER DOCUMENT. One `fitz.Document` handle is not
    thread-safe across pages, so pages of the SAME document serialize against
    each other — but DIFFERENT documents extract fully in parallel on the wide
    native pool. (The old single process-global RLock serialized the entire
    native band, including every document's O(N)-page median scan, which froze
    all other documents whenever any document opened.)
    """
    route_band = NATIVE

    # I-01: cap on simultaneously cached document handles (FD/memory bound).
    _MAX_DOC_CACHE = 32

    def __init__(self, config: ParserConfig):
        self.config = config
        # I-01 fix: per-document locks (src_path -> RLock) guarded by a small
        # meta-lock. Lock ordering: callers take a document lock alone; the
        # meta-lock is never held while blocking on a document lock (eviction
        # only tries document locks non-blocking), so no deadlock is possible.
        self._locks_guard = threading.Lock()
        self._doc_locks: dict[str, threading.RLock] = {}  # src_path -> lock
        # F-05/F-06 fix: cache document handle + median per source path
        self._doc_cache: dict[str, tuple[object, float]] = {}  # path -> (fitz.Document, median)

    def _lock_for(self, src_path: str) -> threading.RLock:
        """Get-or-create the per-document lock (I-01)."""
        with self._locks_guard:
            lock = self._doc_locks.get(src_path)
            if lock is None:
                lock = threading.RLock()
                self._doc_locks[src_path] = lock
            return lock

    def _evict_if_over_budget_locked(self, keep_path: str) -> None:
        """Bound `_doc_cache` size (file-descriptor / memory safety).

        Called while HOLDING `keep_path`'s document lock. A victim is only
        evicted when its own document lock can be acquired WITHOUT blocking —
        a document with an in-flight page (lock held/busy) is non-evictable,
        so we never close a handle out from under a running extraction.
        """
        if len(self._doc_cache) <= self._MAX_DOC_CACHE:
            return
        for old_path in list(self._doc_cache.keys()):
            if old_path == keep_path:
                continue
            old_lock = self._doc_locks.get(old_path)
            if old_lock is None or not old_lock.acquire(blocking=False):
                continue
            try:
                with self._locks_guard:
                    entry = self._doc_cache.pop(old_path, None)
                    self._doc_locks.pop(old_path, None)
                old_doc = entry[0] if entry else None
                if old_doc is not None:
                    try:
                        old_doc.close()
                    except Exception:
                        pass
                return  # evicted one handle; enough for now
            finally:
                old_lock.release()

    def _open_and_compute_median(self, src_path: str):
        """Open document once and compute median font size (cached per path).

        F-05/F-06 fix: avoids O(n²) median recomputation and N fitz.open() calls.
        I-01: the caller MUST already hold the per-document lock (`_lock_for`).
        The median scan is a document-wide O(N) pass and runs under THIS
        document's lock only — it never blocks other documents.
        """
        import fitz

        if src_path in self._doc_cache:
            return self._doc_cache[src_path]

        try:
            doc = fitz.open(src_path)
        except Exception as e:
            # Cache the failure so we don't retry on every page
            self._doc_cache[src_path] = (None, 12.0)
            raise e

        # Compute document-wide median body size once (per-document lock held)
        sizes: list[float] = []
        for pi in range(doc.page_count):
            for blk in doc[pi].get_text("dict").get("blocks", []):
                if blk.get("type") != 0:
                    continue
                for line in blk.get("lines", []):
                    for span in line.get("spans", []):
                        if span.get("text", "").strip():
                            sizes.append(float(span.get("size", 0.0)))
        body_med = sorted(sizes)[len(sizes) // 2] if sizes else 12.0

        # Bound cache size to prevent leaking file descriptors in long-running
        # processes (I-01: eviction is in-flight-aware — see helper docstring).
        self._evict_if_over_budget_locked(src_path)

        self._doc_cache[src_path] = (doc, body_med)
        return doc, body_med

    def extract_page(self, src_path: str, page_index: int) -> PageResult:
        # I-01: serialize only THIS document's pages under its own lock. The
        # open + O(N) median scan also run under the same per-document lock, so
        # other documents keep extracting in parallel the whole time.
        try:
            with self._lock_for(src_path):
                try:
                    doc, body_med = self._open_and_compute_median(src_path)
                except Exception as e:
                    return PageResult(
                        doc_id="", page_index=page_index, route=NATIVE, status=PageStatus.FAILED,
                        errors=[{"page_no": page_index + 1, "category": "native_open", "message": str(e)}],
                    )
                if doc is None:
                    return PageResult(
                        doc_id="", page_index=page_index, route=NATIVE, status=PageStatus.FAILED,
                        errors=[{"page_no": page_index + 1, "category": "native_open",
                                 "message": "failed to open document"}],
                    )
                try:
                    if page_index < 0 or page_index >= doc.page_count:
                        return PageResult(
                            doc_id="", page_index=page_index, route=NATIVE, status=PageStatus.FAILED,
                            errors=[{"page_no": page_index + 1, "category": "native_range",
                                     "message": f"page {page_index} out of range (doc has {doc.page_count})"}],
                        )
                    return _native_page_from_doc(doc[page_index], page_index, self.config, body_med=body_med)
                except Exception as e:
                    return PageResult(
                        doc_id="", page_index=page_index, route=NATIVE, status=PageStatus.FAILED,
                        errors=[{"page_no": page_index + 1, "category": "native_extract", "message": str(e)}],
                    )
        except Exception as e:  # defensive: lock/meta-lock failures never crash the pool
            return PageResult(
                doc_id="", page_index=page_index, route=NATIVE, status=PageStatus.FAILED,
                errors=[{"page_no": page_index + 1, "category": "native_extract", "message": str(e)}],
            )

    def process(self, item: PageWorkItem) -> PageResult:
        res = self.extract_page(item.src_path, item.page_index)
        res.doc_id = item.doc_id
        res.route = self.route_band
        res.source_hash = item.source_hash
        return res

    def close(self) -> None:
        """Close all cached document handles (F-05/F-06 fix cleanup).

        I-01: each handle is closed under ITS document lock, so an in-flight
        page of that document finishes before the handle goes away.
        """
        with self._locks_guard:
            paths = list(self._doc_locks.keys())
        for path in paths:
            lock = self._doc_locks.get(path)
            if lock is None:
                continue
            with lock:
                with self._locks_guard:
                    entry = self._doc_cache.pop(path, None)
                    self._doc_locks.pop(path, None)
                if entry is not None and entry[0] is not None:
                    try:
                        entry[0].close()
                    except Exception:
                        pass
        with self._locks_guard:
            self._doc_cache.clear()
            self._doc_locks.clear()