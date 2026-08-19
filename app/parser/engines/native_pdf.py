"""Native PDF engine — per-page PyMuPDF extraction (ADR-013 T5).

This is the SINGLE source of truth for native PDF text/font/bold + `find_tables`
+ images, extracted one page at a time. The extraction loop is lifted verbatim
from the former `Loaders._pdf` and reduced to a single page via
`_native_page_from_doc` (the reusable core). The legacy `Loaders._pdf` now calls
that same core so there is no behaviour regression and no duplicated logic.
"""
from __future__ import annotations

import hashlib

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

    return PageResult(
        doc_id="", page_index=page_index, route=NATIVE, status=PageStatus.OK,
        blocks=[b for b in all_blocks if isinstance(b, RecoveredBlock)],
        tables=[t for t in all_blocks if isinstance(t, RecoveredTable)],
        images=images,
    )


class NativePdfEngine:
    route_band = NATIVE

    def __init__(self, config: ParserConfig):
        self.config = config

    def extract_page(self, src_path: str, page_index: int) -> PageResult:
        import fitz  # PyMuPDF

        try:
            doc = fitz.open(src_path)
        except Exception as e:
            return PageResult(
                doc_id="", page_index=page_index, route=NATIVE, status=PageStatus.FAILED,
                errors=[{"page_no": page_index + 1, "category": "native_open", "message": str(e)}],
            )
        try:
            if page_index < 0 or page_index >= doc.page_count:
                return PageResult(
                    doc_id="", page_index=page_index, route=NATIVE, status=PageStatus.FAILED,
                    errors=[{"page_no": page_index + 1, "category": "native_range",
                             "message": f"page {page_index} out of range (doc has {doc.page_count})"}],
                )
            # F1: document-wide median body size for heading classification
            # (parity with legacy Loaders._pdf), computed once over the whole
            # document then reused for every page.
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
            res = _native_page_from_doc(doc[page_index], page_index, self.config, body_med=body_med)
        finally:
            try:
                doc.close()
            except Exception:
                pass
        return res

    def process(self, item: PageWorkItem) -> PageResult:
        res = self.extract_page(item.src_path, item.page_index)
        res.doc_id = item.doc_id
        res.route = self.route_band
        res.source_hash = item.source_hash
        return res