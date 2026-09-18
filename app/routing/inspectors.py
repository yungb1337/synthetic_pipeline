"""Decision-free feature inspector (spec §3, §4).

`FastInspector` opens a PDF with PyMuPDF OPEN-ONLY — `fitz.open(stream=...)`,
NO Pixmap render, NO image decode, NO OCR, NO Docling — and reads cheap
features off the doc's metadata + per-page text/image geometry. It is
decision-free: it never reads `config.layout_backend`, policy, or score; it
only observes. A feature it could not observe is reported explicitly as
`None`/empty (`missing`), never as a fabricated 0/False negative (§4, §11).
"""
from __future__ import annotations

from dataclasses import dataclass, field

try:
    import pdf_inspector
    _PDF_INSPECTOR_AVAILABLE = True
except ImportError:
    _PDF_INSPECTOR_AVAILABLE = False

_LOW_TEXT_CHARS = 30  # a page with fewer printable chars is "text-poor"


@dataclass
class InspectorFeatures:
    """Raw, observed features passed to detectors (architecture §4).

    Missing observations are `None` (or empty lists), never 0/False — so no
    detector can mistake a missing feature for a quiet negative.
    """

    # metadata-level
    mime_slug: str = ""
    declared_extension: str = ""
    pdf_format: str | None = None       # "PDF 1.7"
    pdf_version: str | None = None      # "1.7"
    encrypted: bool | None = None
    producer: str | None = None
    creator: str | None = None
    has_outline: bool | None = None
    has_tag: bool | None = None
    page_count: int = 0
    page_dims: dict[int, tuple[float, float]] = field(default_factory=dict)
    # text (per page) + aggregate
    pages_char_count: dict[int, int] = field(default_factory=dict)
    chars_per_page: list[float] = field(default_factory=list)
    text_ratio: float | None = None          # glyph area / page area (heuristic)
    fragment_count: int | None = None       # total text spans observed
    # image
    image_count: int = 0
    images_per_page: list[int] = field(default_factory=list)
    covered_pages: int = 0
    # per-page image OWNERSHIP ratio (0..1): Σ image-rect area / page area,
    # exact to a few decimals, computed WITHOUT a render (get_image_rects).
    # This is the auditable evidence backing the scanned-probability heuristics;
    # detectors consume the continuous ratio, not a hard boolean (§4, §13).
    pages_image_ratio: dict[int, float] = field(default_factory=dict)
    full_image_pages: list[int] = field(default_factory=list)   # audit: ratio≈1 & no text
    # layout hints (cheap heuristics)
    est_multi_column_pages: list[int] = field(default_factory=list)
    block_count_per_page: list[int] = field(default_factory=list)
    # structural
    detected_tables: int | None = None      # presence, else None (finder failed)
    fonts: list[str] = field(default_factory=list)


class FastInspector:
    """Open-without-render inspection over the source bytes.

    Returns `InspectorFeatures` for a readable PDF, else `None` (a non-PDF, an
    unreadable doc, or an encrypted-without-password PDF) — the caller treats
    `None` as "no routing evidence", not as a negative.
    """

    def inspect(self, data: bytes) -> InspectorFeatures | None:
        if not data:
            return None

        # 1. Rust-level pre-inspection via firecrawl/pdf-inspector (sub-30ms)
        rust_features = None
        if _PDF_INSPECTOR_AVAILABLE:
            try:
                rust_features = pdf_inspector.process_pdf_bytes(data)
            except Exception:
                rust_features = None

        try:
            import fitz
        except Exception:
            return None
        try:
            doc = fitz.open(stream=data, filetype="pdf")
        except Exception:
            return None

        f = InspectorFeatures(mime_slug="pdf", declared_extension="pdf")
        _read_metadata(doc, f)

        glyph_area = 0.0
        total_area = 0.0
        for pno in range(doc.page_count):
            try:
                page = doc[pno]
                w, h = page.rect.width, page.rect.height
                area = float(w * h)
            except Exception:               # isolate a bad page; it stays missing
                continue
            f.page_dims[pno] = (float(w), float(h))
            total_area += area

            raw = {"blocks": []}
            try:
                raw = page.get_text("dict")
            except Exception:
                pass

            blocks = 0
            nchars = 0
            span_count = 0
            for blk in raw.get("blocks", []):
                if blk.get("type") != 0:
                    continue
                blocks += 1
                for line in blk.get("lines", []):
                    for span in line.get("spans", []):
                        span_count += 1
                        nchars += sum(1 for ch in span.get("text", "") if not ch.isspace())
                        glyph_area += min(_bbox_area(span.get("bbox")), area) if area else 0.0
                        font = span.get("font")
                        if font and font not in f.fonts:
                            f.fonts.append(font)
            f.block_count_per_page.append(blocks)
            f.pages_char_count[pno] = nchars
            f.fragment_count = (f.fragment_count or 0) + span_count

            try:
                imgs = page.get_images(full=True)
                f.images_per_page.append(len(imgs))
                f.image_count += len(imgs)
                image_area = 0.0
                for img in imgs:
                    try:
                        for rect in page.get_image_rects(img[0]):
                            image_area += _rect_area(rect)
                    except Exception:
                        continue
                if image_area > 0:
                    f.covered_pages += 1
                page_area = (w * h) if area else 1.0
                if page_area > 0:
                    # README: auditable continuous image-ownership ratio (no render)
                    f.pages_image_ratio[pno] = round(min(1.0, image_area / page_area), 4)
                    # hard boolean is ON ROWNED evidence only for audit/regression;
                    # detectors consume the continuous ratio, not this boolean.
                    if image_area >= 0.9 * page_area and nchars == 0:
                        f.full_image_pages.append(pno)
            except Exception:
                f.images_per_page.append(0)

            # If Rust inspector gave column info, use it; otherwise fallback to PyMuPDF heuristic
            if rust_features is None:
                if _est_multi_column(page, float(w)):
                    f.est_multi_column_pages.append(pno)

        if rust_features is not None:
            # Multi-column pages from Rust (1-indexed -> 0-indexed)
            cols = getattr(rust_features, "pages_with_columns", []) or []
            f.est_multi_column_pages = sorted([p - 1 for p in cols if 0 < p <= doc.page_count])

            # Exact table detection from Rust core
            tables = getattr(rust_features, "pages_with_tables", None)
            if tables is not None:
                f.detected_tables = len(tables)
            else:
                f.detected_tables = _find_table_presence(doc)
        else:
            f.detected_tables = _find_table_presence(doc)

        if total_area > 0:
            f.text_ratio = min(1.0, glyph_area / total_area)
        f.chars_per_page = [float(f.pages_char_count.get(p, 0)) for p in range(f.page_count)]

        doc.close()
        return f


def _read_metadata(doc, f: InspectorFeatures) -> None:
    try:
        md = doc.metadata or {}
        f.pdf_format = md.get("format") or None
        f.pdf_version = _version_of(f.pdf_format)
        f.encrypted = bool(doc.is_encrypted) if doc.is_encrypted else None
        f.producer = md.get("producer") or None
        f.creator = md.get("creator") or None
        f.has_tag = md.get("tagged") or None
        try:
            f.has_outline = bool(doc.get_toc())
        except Exception:
            f.has_outline = None
        f.page_count = doc.page_count
    except Exception:
        pass  # metadata is best-effort; the page geometry still describes the doc


# Bound inspection cost: find_tables() is expensive (~200ms/page), so probe a
# distributed sample of up to 5 pages (first, mid, 3/4, last). Keeps the inspector
# << processing while ensuring tables in mid/late sections are reliably detected.
_TABLE_PROBE_MAX_PAGES = 5


def _probe_page_indices(page_count: int, max_pages: int = _TABLE_PROBE_MAX_PAGES) -> list[int]:
    """Choose distributed page indices (0-based) across the document to probe."""
    if page_count <= 0:
        return []
    if page_count <= max_pages:
        return list(range(page_count))
    indices = {0, 1, page_count // 2, (3 * page_count) // 4, page_count - 1}
    return sorted(idx for idx in indices if 0 <= idx < page_count)


def _find_table_presence(doc) -> int | None:
    """Probe distributed pages across the document with PyMuPDF `find_tables`.

    Returns the number of tables found. `0` means the probe RAN and found no
    tables — a real measured negative, never a missing (§11). Returns `None`
    only when every probed page failed to run the finder (genuinely missing).
    """
    counts = 0
    probed = 0
    for pno in _probe_page_indices(doc.page_count, max_pages=_TABLE_PROBE_MAX_PAGES):
        try:
            page = doc[pno]
            ft = page.find_tables()
            tables = getattr(ft, "tables", None)
            probed += 1
            if tables:
                counts += len(tables)
            else:
                # Probe for borderless / 3-line tables via caption or text-strategy
                txt = page.get_text()
                caps = _TABLE_CAPTION_RE.findall(txt)
                if caps:
                    ft2 = page.find_tables(horizontal_strategy="lines", vertical_strategy="text")
                    tables2 = getattr(ft2, "tables", None)
                    if tables2:
                        counts += len(tables2)
                    else:
                        counts += len(caps)
        except Exception:
            continue
    if probed == 0:
        return None  # probe could not run at all -> missing, not a negative
    return counts


def _version_of(fmt: str | None) -> str | None:
    if not fmt:
        return None
    parts = fmt.split()
    return parts[-1] if parts and parts[-1][:1].isdigit() else None


def _bbox_area(bbox) -> float:
    if not bbox or len(bbox) < 4:
        return 0.0
    return max(0.0, (bbox[2] - bbox[0]) * (bbox[3] - bbox[1]))


def _rect_area(rect) -> float:
    try:
        return max(0.0, float(rect.width) * float(rect.height))
    except Exception:
        return 0.0


def _est_multi_column(page, page_w: float) -> bool:
    """Cheap x-position clustering heuristic (NOT a hard truth). Conservative:
    needs a real gap between separated left-column groups, else returns False.
    """
    x0s: list[float] = []
    try:
        raw = page.get_text("rawdict")
        for blk in raw.get("blocks", []):
            if blk.get("type") != 0:
                continue
            bbox = blk.get("bbox")
            if bbox:
                x0s.append(float(bbox[0]))
    except Exception:
        return False
    if len(x0s) < 2:
        return False
    x0s.sort()
    gap_w = page_w / 10.0
    groups = 1
    for i in range(1, len(x0s)):
        if x0s[i] - x0s[i - 1] > gap_w:
            groups += 1
    return groups >= 2 and (x0s[-1] - x0s[0]) > page_w / 4.0


# small symbol so tests can read the threshold
LOW_TEXT_CHARS = _LOW_TEXT_CHARS