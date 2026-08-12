"""Docling layout/table backend (ADR-007).

Docling is PRESENT in the pipeline but only triggers where layout analysis is
required: `ParserConfig.layout_backend == "docling"` routes PDFs (and bare
images) through here, replacing the heuristic reading order and PyMuPDF
`find_tables` for those documents. Everything else keeps the cheap native path.

Design (mirrors `ocr.py`'s lazy-engine pattern):
  * Docling is imported lazily; if it is not installed (or fails to load), the
    engine is marked unavailable and callers fall back to the native path —
    the pipeline never crashes on a missing optional dependency.
  * The converter (and its ML models) is built ONCE per process and reused, so
    the batch worker pool never pays a cold-start per document.
  * Models are cached locally under the configured `models_dir` (on-prem; no
    data leaves the machine).
  * We run the compute-light subset: layout + table-structure, no OCR, no
    code/formula, by default.
"""
from __future__ import annotations

import hashlib
import io as _io
import os
import re
import tempfile
import threading

# Docling's layout model runs through torch.compile, which needs Triton — not
# available on Windows (and some other environments). Disabling dynamo makes the
# model run eagerly (slower, but functional) and lets Docling work here at all.
# Set at import time (before docling/torch are imported on the Docling path).
os.environ.setdefault("TORCHDYNAMO_DISABLE", "1")

from ..parts import RecoveredBlock, RecoveredDocument, RecoveredImage, RecoveredTable

_engine = None
_lock = threading.Lock()

# ItemLabel -> our Block kind. Keyed by the enum's `.value` string, which is
# stable across docling_core versions.
_KIND = {
    "text": "paragraph",
    "section_header": "heading",
    "list_item": "list_item",
    "code": "code",
    "formula": "formula",
    "caption": "caption",
    "page_header": "paragraph",
    "page_footer": "paragraph",
}


def engine_available() -> bool:
    """True if a working Docling converter is loaded (lazy, once per process)."""
    global _engine
    if _engine is None:
        with _lock:
            if _engine is None:
                _engine = _build_converter()
    return _engine is not False  # type: ignore[comparison-overlap]


def engine_name() -> str | None:
    """The installed docling package version, or None if unavailable."""
    try:
        import docling

        return getattr(docling, "__version__", None) or "docling"
    except Exception:
        return None


# --- converter construction -------------------------------------------------
def _build_converter():
    """Build a DocumentConverter with the compute-light pipeline, defensively.

    Docling's API has drifted across versions (PipelineOptions vs
    PdfPipelineOptions, artifacts_path kwarg, ...). Each step is try/except'd so
    we fall back to a plain default converter rather than failing hard.
    """
    models_dir = _models_dir()
    if models_dir:
        os.makedirs(models_dir, exist_ok=True)
        # Env vars MUST be set before docling/huggingface_hub import, or the cache
        # location is already pinned. Keep every downloaded artifact inside the
        # configured on-prem dir (never the user's global HF cache).
        os.environ.setdefault("DOCLING_MODELS_PATH", models_dir)
        os.environ.setdefault("HF_HOME", os.path.join(models_dir, "hf"))
        os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

    try:
        from docling.document_converter import DocumentConverter

        # Belt-and-braces: if TORCHDYNAMO_DISABLE is set too late (torch already
        # imported), suppress tracing errors so the layout model still runs eagerly.
        try:
            import torch._dynamo as _dynamo

            _dynamo.config.suppress_errors = True
        except Exception:
            pass

        # Resolve the pipeline-options class. Prefer PdfPipelineOptions: it is the
        # per-format options type and carries the fields we use (do_ocr,
        # do_code_formula, generate_picture_images). Some docling versions also
        # export a legacy `PipelineOptions` alias that lacks those newer fields —
        # never use it, or option application silently no-ops.
        try:
            from docling.datamodel.pipeline_options import PdfPipelineOptions as PipelineOptions
        except Exception:
            from docling.datamodel.pipeline_options import PipelineOptions as PipelineOptions

        # Docling OCR (the user-requested feature): use Docling's built-in
        # RapidOCR/onnxruntime backend (same engine family as app/parser/ocr.py),
        # on-demand, read from ParserConfig.docling_ocr.
        try:
            from ..config import default_config

            ocr = bool(default_config().docling_ocr)
        except Exception:
            ocr = True

        opts = _make_pipeline_options(PipelineOptions, ocr=ocr)
        kwargs = {}

        # Preferred: per-format PdfFormatOption with the custom pipeline.
        try:
            from docling.datamodel.base_models import InputFormat
            from docling.document_converter import PdfFormatOption

            kwargs["format_options"] = {InputFormat.PDF: PdfFormatOption(pipeline_options=opts)}
        except Exception:
            # Fallback: global pipeline_options.
            try:
                kwargs["pipeline_options"] = opts
            except Exception:
                pass

        # `artifacts_path` is NOT accepted by every DocumentConverter version —
        # adding it blindly makes the whole construction throw, and the fallback
        # below would silently build a converter with NO custom pipeline options
        # (losing generate_picture_images -> figures arrive without pixels). Only
        # add it when the constructor actually declares it; the DOCLING_MODELS_PATH
        # env var (set above) already pins the model cache for every version.
        try:
            import inspect as _inspect

            if "artifacts_path" in _inspect.signature(DocumentConverter.__init__).parameters:
                kwargs["artifacts_path"] = models_dir
        except Exception:
            pass

        try:
            return DocumentConverter(**kwargs)
        except Exception:
            return DocumentConverter()

    except Exception:
        return False


def _make_pipeline_options(cls, ocr: bool = True):
    """Build Docling pipeline options; `ocr` enables Docling's OCR stage (its
    built-in RapidOCR/onnxruntime backend — the same engine family as
    `app/parser/ocr.py`). Defensive across docling's API drift: falls back to
    defaults and best-effort attribute setting rather than failing hard."""
    # `generate_picture_images` asks Docling to crop every detected picture
    # region from the page image, so figures reach the DOM with real pixels
    # (ADR-007: captions + figures become typed nodes). Never silently drop a
    # picture because bytes were not generated.
    try:
        opts = cls(do_ocr=ocr, do_code_formula=False, generate_picture_images=True)
    except Exception:
        try:
            opts = cls(do_ocr=ocr, do_code_formula=False)
        except Exception:
            try:
                opts = cls()
                for attr in ("do_ocr", "do_code_formula", "generate_picture_images"):
                    try:
                        setattr(opts, attr, ocr if attr == "do_ocr" else True)
                    except Exception:
                        pass
            except Exception:
                return None
    try:
        # 2x figure fidelity (crops are 72 dpi x scale), matching the OCR scale.
        opts.images_scale = 2.0
    except Exception:
        pass
    if ocr:
        _set_ocr_options(opts)
    return opts


def _set_ocr_options(opts) -> None:
    """Point Docling's OCR stage at RapidOCR (RapidOcrOptions), on-demand.

    `OcrMode.DEFAULT` makes Docling OCR only the regions/pages it considers
    low-text, so a text-rich document is not wastefully OCR'd. `scale` is kept
    conservative to bound memory on the 4 GB box. All defensively wrapped: if
    the class is absent (docling version drift) we keep defaults.
    """
    try:
        from docling.datamodel.pipeline_options import OcrMode, RapidOcrOptions

        opts.ocr_options = RapidOcrOptions(mode=OcrMode.DEFAULT, scale=2.0)
    except Exception:
        try:
            from docling.datamodel.pipeline_options import RapidOcrOptions

            opts.ocr_options = RapidOcrOptions()
        except Exception:
            pass


def _models_dir() -> str:
    try:
        from ..config import default_config

        return default_config().docling_models_dir
    except Exception:
        return "models/docling"


# --- mapping ----------------------------------------------------------------
def parse(data: bytes, filename: str = "", models_dir: str | None = None) -> RecoveredDocument | None:
    """Run Docling on `data` and map to a RecoveredDocument.

    Returns None (never raises) when Docling is unavailable, conversion fails,
    or nothing text/table-bearing is recovered — the caller falls back to native.
    """
    if models_dir:
        # Let a non-default cache dir win even if the singleton was built earlier.
        os.environ.setdefault("DOCLING_MODELS_PATH", models_dir)
    if not engine_available():
        return None
    converter = _engine  # type: ignore[assignment]

    import time as _time
    t_conv = _time.time()
    doc = _convert(converter, data, filename)
    if doc is None:
        return None

    rec = RecoveredDocument(detected_type=_slug(filename), mime="application/pdf")
    # wall-clock conversion time (the heavy ML stage; any Docling-internal OCR
    # is included here — see rec.timings["docling_ms"]).
    rec.timings["docling_ms"] = round((_time.time() - t_conv) * 1000, 1)
    rec.reading_order_authoritative = True  # Docling's iterate_items order is final
    rec.docling_version = engine_name()
    rec.layout_model = _layout_model_name(converter)

    # page sizes for provenance / native-consistent DOM
    try:
        for pno, page in doc.pages.items():
            size = getattr(page, "size", None)
            if size is not None and getattr(size, "width", None) is not None:
                rec.page_sizes[int(pno)] = (float(size.width), float(size.height))
        rec.page_count = len(doc.pages)
    except Exception:
        pass

    # Document-level metadata from the PDF info dict (the same source the native
    # PDF loader uses): Docling's own Document.metadata is empty for plain PDFs.
    try:
        import fitz as _fitz
        from ._pdfmeta import fitz_metadata

        with _fitz.open(stream=data, filetype="pdf") as mdoc:
            for key, val in fitz_metadata(mdoc).items():
                setattr(rec, key, val)
    except Exception:
        pass

    t_map = _time.time()
    for entry in doc.iterate_items():
        try:
            # docling-core yields (item, level) tuples in recent versions; older
            # versions yielded the item directly. Handle both.
            item = entry[0] if isinstance(entry, tuple) and entry else entry
            _map_item(item, rec, doc)
        except Exception:
            continue  # a bad item degrades; never crashes the doc

    # Formula fallback: Docling's formula-transcription model produces no text
    # for many equations, but the equation IS text in the page layer. Recover
    # it from the page geometry at the block's (already top-left-normalized)
    # bbox. Faithful: only fills blocks Docling flagged as formulas; never
    # invents blocks or rewrites non-formula text.
    _recover_formula_text(data, rec)

    # Evidence-graph row reconstruction: for tables Docling collapsed into a
    # single concatenated body row, recover the logical rows from the page
    # geometry (only when the evidence supports it; faithful otherwise).
    t_ev = _time.time()
    for t in rec.tables:
        if t.confidence < 1.0 and t.column_starts:
            _evidence_reconstruct(data, t)
    rec.timings["docling_map_ms"] = round((t_ev - t_map) * 1000, 1)
    rec.timings["table_reconstruct_ms"] = round((_time.time() - t_ev) * 1000, 1)

    # General structural transformation: re-unify multi-page continuation
    # fragments into one logical table and drop caption/marker rows.
    rec.tables = normalize_tables(rec.tables)

    # Safety valve: if Docling recovered nothing readable, let the native path
    # try (e.g. scanned PDFs that need OCR, which we deliberately skip).
    if not rec.blocks and not rec.tables:
        return None
    return rec


def _map_item(item, rec: RecoveredDocument, doc=None) -> None:
    prov = item.prov[0] if getattr(item, "prov", None) else None
    page = int(getattr(prov, "page_no", 0) or 0)
    # Page height lets us normalize Docling's bottom-left prov boxes into the
    # DOM's PDF-point (top-left) coordinate space.
    page_h = 0.0
    try:
        page_h = float(doc.pages[page].size.height)
    except Exception:
        pass
    bbox = _bbox(prov, page_h)

    label = _label_name(item)
    if label == "table":
        _map_table(item, rec, doc, page, bbox, page_h)
        return
    if label == "picture":
        _map_image(item, rec, doc, page, bbox)
        return

    kind = _KIND.get(label, "paragraph")
    text = (getattr(item, "text", "") or "").strip()
    # No silent loss: a FORMULA with empty text (upstream transcription model
    # produced nothing) still becomes a typed block so its presence and position
    # survive; `parse()` fills the text from the page layer as a fallback. Other
    # textless items carry no content and are dropped as before.
    if not text and kind != "formula":
        return
    rec.blocks.append(
        RecoveredBlock(
            page=page,
            kind=kind,
            text=text,
            bbox=bbox,
            seq=len(rec.blocks),
            source="docling",
        )
    )


def _recover_formula_text(data: bytes, rec: RecoveredDocument) -> None:
    """Fill formula blocks whose transcription model produced no text from the
    page layer, in place.

    Docling detects the formula REGION (layout stage) but its transcription
    (code/formula model) often emits empty text/latex. The equation itself is
    usually selectable text in the PDF, so we extract the words inside the
    block's bbox (already normalized to PDF-point top-left, the same space
    fitz uses). Faithful & fallible: only `kind == "formula"` blocks are
    touched, only when empty, and only when the page geometry yields words.
    """
    todo = [b for b in rec.blocks if b.kind == "formula" and not (b.text or "").strip() and b.bbox]
    if not todo:
        return
    try:
        import fitz

        pdf = fitz.open(stream=data, filetype="pdf")
    except Exception:
        return
    try:
        for b in todo:
            x0, y0, x1, y1 = b.bbox
            try:
                page = pdf[b.page - 1]  # Docling page_no is 1-based; fitz 0-based
                words = page.get_text("words", clip=fitz.Rect(x0, y0, x1, y1))
            except Exception:
                continue
            text = " ".join(w[4] for w in words if w[4].strip())
            text = " ".join(text.split())
            if text:
                b.text = text
    finally:
        try:
            pdf.close()
        except Exception:
            pass


# A body cell longer than `_TABLE_COLLAPSE_RATIO` x its header is treated as a
# concatenation (many source rows joined into one cell) — the row-collapse
# signature. Principled (structural, not doc-specific), non-overfit.
_TABLE_COLLAPSE_RATIO = 3.0


def _table_structural_confidence(header: list[str], rows: list[list[str]]) -> float:
    """Structural-confidence estimate for a Docling table (0..1).

    1.0 = Docling's row segmentation is trustworthy. A lower value means the
    table-structure stage collapsed the body into a single concatenated row
    (multi-column table whose only body row's cells are much longer than the
    header — i.e. several logical source rows were joined into one). Per the
    "faithful & fallible" principle we do NOT fabricate the lost rows; we
    surface the uncertainty via `Table.confidence` so downstream consumers do
    not over-trust the row/column mapping.
    """
    if len(rows) >= 2 or len(header) <= 1:
        return 1.0
    body = rows[0]
    if len(body) != len(header):
        return 1.0
    for h, b in zip(header, body):
        if h and len(b) > len(h) * _TABLE_COLLAPSE_RATIO:
            return 0.3
    return 1.0


class _GridHole:
    """Stand-in for a grid position with no upstream cell (spanned over). Keeps
    the dense-grid logic uniform: every position carries .text/.bbox/span/flags."""

    __slots__ = ("text", "column_header", "col_span", "row_span", "bbox")

    def __init__(self):
        self.text = ""
        self.column_header = False
        self.col_span = 1
        self.row_span = 1
        self.bbox = None


def _dense_grid(table):
    """Return the table structure as a rectangular grid of cell objects.

    Prefers Docling's `grid` (rows of TableCell); otherwise rebuilds a dense
    grid from the flat `table_cells` list (holes filled with `_GridHole`).
    Returns None when there is no usable structure at all. This is the single
    source of truth for table mapping — `export_to_dataframe()` is deliberately
    NOT preferred, because it builds a pandas MultiIndex when a full-width title
    row is flagged `column_header`, fusing the table caption into every column
    name (e.g. "Adult Census Data (10K records).SD Metrics").
    """
    try:
        g = list(getattr(table, "grid", None) or [])
    except Exception:
        g = []
    if g and g[0] is not None:
        return g
    cells = getattr(table, "table_cells", None) or []
    if not cells:
        return None
    nrows = 0
    ncols = 0
    for c in cells:
        r = int(getattr(c, "start_row_offset_idx", 0) or 0)
        col = int(getattr(c, "start_col_offset_idx", 0) or 0)
        nrows = max(nrows, r + 1)
        ncols = max(ncols, col + 1)
    if nrows == 0 or ncols == 0:
        return None
    by_pos: dict[tuple[int, int], object] = {}
    for c in cells:
        r = int(getattr(c, "start_row_offset_idx", 0) or 0)
        col = int(getattr(c, "start_col_offset_idx", 0) or 0)
        by_pos[(r, col)] = c
    rows = []
    for r in range(nrows):
        rows.append([by_pos.get((r, col)) or _GridHole() for col in range(ncols)])
    return rows


def _item_caption(item, doc=None) -> str:
    """Caption text Docling associates with a floating item (picture/table). The
    association is exposed as a `caption_text(doc)` method resolving the item's
    caption refs. Returns the normalized text ('' when absent/empty)."""
    try:
        cap = getattr(item, "caption_text", None)
        if cap is None:
            return ""
        text = cap(doc) if callable(cap) else cap
        return " ".join((text or "").split())
    except Exception:
        return ""


def _map_table(item, rec: RecoveredDocument, doc=None, page: int = 0, bbox=None,
               page_h: float = 0.0) -> None:
    table = getattr(item, "table", None) or getattr(item, "data", None)
    if table is None:
        return
    caption = _item_caption(item, doc)

    grid = _dense_grid(table)
    if grid is None:
        _map_table_via_dataframe(item, rec, page, bbox, caption)
        return
    # Table width = the widest row. A leading full-width TITLE row has 1 cell
    # spanning the whole table, so grid[0]'s width would undercount — the real
    # width comes from the header/body rows.
    ncols = max((len(row) for row in grid), default=0)
    nrows = len(grid)
    if ncols == 0:
        return

    # Leading rows whose cells are ALL flagged column_header form the header
    # block. Within it, a row made of a single cell spanning the FULL table
    # width is a title/caption row (e.g. "Adult Census Data (10K records)") —
    # NOT a column header: strip it into `caption` and keep the remaining
    # column_header rows as the real header. (Structural rule; no table ids.)
    header_rows: list[int] = []
    title_rows: list[int] = []
    for r in range(nrows):
        row = grid[r]
        if not row or not all(getattr(c, "column_header", False) for c in row):
            break
        if len(row) == 1 and getattr(row[0], "col_span", 1) >= ncols:
            title_rows.append(r)
            continue
        header_rows.append(r)

    if header_rows:
        hr = header_rows[-1]  # most specific header row (last in the header block)
        header = [_clean_cell(getattr(c, "text", "")) for c in grid[hr]]
        if not caption and title_rows:
            caption = _clean_cell(getattr(grid[title_rows[0]][0], "text", ""))
        body_rows = range(hr + 1, nrows)
    else:
        # No column_header flags (e.g. old/foreign table data): first row is the
        # header by convention (matches the previous fallback behaviour).
        header = [_clean_cell(getattr(c, "text", "")) for c in grid[0]]
        body_rows = range(1, nrows)

    rows = []
    for r in body_rows:
        row = grid[r]
        # A merged body cell may occupy fewer grid positions than ncols (its
        # span covers the rest) — pad with empty cells to keep the rectangular
        # shape the DOM expects, without inventing text.
        cells = [_clean_cell(getattr(row[c], "text", "")) for c in range(min(len(row), ncols))]
        rows.append(cells + [""] * (ncols - len(cells)))

    # Column geometry for the evidence-graph reconstruction, from the REAL
    # header row (a full-width title row has no per-column x-positions).
    col_starts: list[float] = []
    header_bottom = 0.0
    body_bottom = 0.0
    hr = header_rows[-1] if header_rows else 0
    for c in grid[hr]:
        bb = getattr(c, "bbox", None)
        if bb is not None:
            try:
                col_starts.append(float(bb.l))
            except Exception:
                pass
    try:
        header_bottom = max(
            (float(c.bbox.b) for c in grid[hr] if getattr(c, "bbox", None)), default=0.0)
        if rows:
            last = body_rows[-1]
            body_bottom = max(
                (float(c.bbox.b) for c in grid[last] if getattr(c, "bbox", None)), default=0.0)
    except Exception:
        pass

    rec.tables.append(RecoveredTable(
        page=page, bbox=bbox, header=header, rows=rows, source="docling",
        confidence=_table_structural_confidence(header, rows),
        caption=caption,
        column_starts=col_starts,
        header_bottom=header_bottom, body_bottom=body_bottom))


def _map_table_via_dataframe(item, rec: RecoveredDocument, page: int, bbox,
                             caption: str) -> None:
    """Last-resort table mapping: Docling's structured dataframe (handles merged
    cells by repetition). Only used when no raw grid/cells are available."""
    try:
        df = item.export_to_dataframe()
    except Exception:
        return
    if df is None or df.empty:
        return
    # MultiIndex columns (caption/group rows fused by pandas) flatten to their
    # joined levels; the joined levels become the column names (this fallback
    # path has no raw grid to separate caption rows, so a fused title survives
    # as a joined header level rather than being lost).
    cols = df.columns
    if getattr(cols, "nlevels", 1) > 1:
        header = [".".join(str(x) for x in c if x is not None and str(x).strip())
                  for c in cols]
    else:
        header = ["" if c is None else str(c) for c in cols]
    rows = [["" if v is None else str(v) for v in row] for row in df.itertuples(index=False)]
    rec.tables.append(RecoveredTable(page=page, bbox=bbox, header=header, rows=rows,
                                     source="docling",
                                     confidence=_table_structural_confidence(header, rows),
                                     caption=caption))


# --- general structural table transformation ---------------------------------
# Multi-page tables arrive as separate fragments (one per page). This is a
# general, deterministic normalization that re-unifies them into ONE logical
# table and drops caption/marker rows — derived purely from structure (column
# count, adjacent fragments, repeated/degenerate headers), never from table
# ids, page numbers, or document-specific text.

def _clean_cell(value) -> str:
    return "" if value is None else str(value).strip()


def _is_continuation(later: RecoveredTable, earlier: RecoveredTable) -> bool:
    """True when `later` (a table fragment) continues the same logical table as
    `earlier` (the fragment that immediately precedes it).

    Structural signals only: same column count, later page, and either
    (a) the later header embeds the earlier header (a caption prefix followed
        by the repeated real header, e.g. "Continuation of Table 3. Approach"),
    or (b) the later header is a degenerate repeated marker (every header cell
        is the same string — the continuation page carried no real header).
    """
    if len(later.header) != len(earlier.header):
        return False
    if later.page < earlier.page:
        return False
    earlier_h = [_clean_cell(h) for h in earlier.header]
    if all(eh and _clean_cell(lh).endswith(eh) for lh, eh in zip(later.header, earlier_h)):
        return True
    if len({_clean_cell(h) for h in later.header if _clean_cell(h)}) == 1:
        return True
    return False


def _row_equals_header(row: list[str], header: list[str]) -> bool:
    """True when a fragment's row is the repeated table header (all cells match
    the canonical header) rather than a data row."""
    if not row or not header or len(row) != len(header):
        return False
    return all(_clean_cell(c) and _clean_cell(c) == _clean_cell(h)
               for c, h in zip(row, header))


def _merge_continuation(parent: RecoveredTable, frag: RecoveredTable) -> None:
    """Fold `frag`'s data rows into `parent` (the first fragment). The parent's
    header is canonical; a leading row that repeats that header (the continuation
    page's repeated header) is not data and is dropped. `frag` is consumed."""
    for row in frag.rows:
        if row and _row_equals_header(row, parent.header):
            continue
        parent.rows.append(row)


def _drop_marker_rows(t: RecoveredTable) -> None:
    """Drop a trailing caption/marker row, not data — recognized structurally:
    a LAST row whose non-empty cells are all the SAME string (e.g. an "End of
    Table" marker repeated across columns). Interior all-identical rows are
    legitimate data (e.g. a diagonal/paired table like "Generation | Generation"),
    so only the trailing row is treated as a marker. Never text-matched."""
    while t.rows:
        last = t.rows[-1]
        nonempty = [_clean_cell(c) for c in last if _clean_cell(c)]
        if nonempty and len({c for c in nonempty}) == 1:
            t.rows.pop()
        else:
            break


_SENT_BOUND = re.compile(r"[.!?]\s+(?=[A-Z0-9])")
# Upper bound on a trailing marker fragment after the last sentence boundary.
_TABLE_MARKER_MAX_WORDS = 6


def _strip_trailing_marker_cell(value) -> str:
    """Remove a trailing table-marker fragment fused onto the last cell.

    Structural only (never text-matched): a marker footer rendered under a table
    (e.g. "End of Table") can be fused by the upstream extractor into the final
    cell's text, after a sentence boundary. It is detected as the short, sentence-
    punctuation-free fragment AFTER the LAST sentence boundary of the cell — a
    genuine trailing sentence ends with its own '.', so it is not stripped.
    """
    cell = _clean_cell(value)
    if not cell or len(cell.split()) < 3:
        return cell
    m = list(_SENT_BOUND.finditer(cell))
    if not m:
        return cell
    frag = cell[m[-1].end():].strip()
    if not frag or len(frag.split()) > _TABLE_MARKER_MAX_WORDS:
        return cell
    if re.search(r"[.!?]\s*$", frag):      # sentence-final => real text, keep it
        return cell
    return cell[: m[-1].start()].rstrip()


def normalize_tables(tables: list[RecoveredTable]) -> list[RecoveredTable]:
    """General structural transformation of recovered tables.

    Merges multi-page continuation fragments into ONE logical table and drops
    marker rows. Deterministic; operates on any list of RecoveredTable.
    """
    if not tables:
        return tables
    out: list[RecoveredTable] = []
    for t in tables:
        parent = out[-1] if out else None
        if parent is not None and _is_continuation(t, parent):
            _merge_continuation(parent, t)
        else:
            out.append(t)
    for t in out:
        _drop_marker_rows(t)
        if t.rows:  # a fused trailing marker fragment in the final cell
            last = t.rows[-1]
            last[-1] = _strip_trailing_marker_cell(last[-1])
    return out


# --- evidence-graph row reconstruction ---------------------------------------
# When Docling's table-structure stage collapses several logical rows into ONE
# concatenated body row (borderless / dense tables), the row boundaries can
# still be recovered from the PAGE geometry — but a visual line is NOT a logical
# row: a wrapped cell continues onto following lines, and a single logical row
# may span several lines. Rows are therefore recovered cross-column: a column
# that does not wrap carries exactly one line per logical row, so the row count
# is the minimum number of lines any column shows, and a split is only accepted
# when >= 2 columns independently show that many lines (one column alone cannot
# distinguish wrapped text from rows — the faithful collapsed table then
# stands). Deterministic; faithful & fallible: if the evidence does not
# establish >= 2 rows, the collapsed table stays unchanged.
_TABLE_EV_TOL = 6.0           # px: word start must be within this of a column start
_TABLE_EV_Y_EPS = 2.0         # px: baseline jitter — words this close vertically share one visual line
_TABLE_EV_ROW_GAP = 16.0      # px: a wrapped-cell (continuation) line is within this of its row start
_TABLE_EV_MAX_WORDS_PER_COL = 12  # roomy: wordy cells are fine; paragraphs are already excluded by y-bounding


def _evidence_reconstruct(data: bytes, table: RecoveredTable) -> None:
    """Rebuild `table.rows` from page geometry evidence, in place.

    Only called for tables Docling collapsed (confidence < 1.0). Uses
    `table.column_starts` (Docling's header-cell x-positions) + the raw page
    text lines. A logical row is a cluster of lines: row boundaries come from
    the columns that do NOT wrap (each carries one line per row); wrapped-cell
    continuations are folded into the row they continue. Deterministic;
    faithful & fallible: if the evidence does not establish >= 2 rows, the
    collapsed table stands unchanged.
    """
    # Use the EXACT upstream column positions (rounding shifts a word that
    # starts exactly at a column edge into the previous column).
    col_starts = sorted({float(s) for s in table.column_starts})
    if table.page < 1 or len(col_starts) < 2:
        return
    try:
        import fitz
        pdf = fitz.open(stream=data, filetype="pdf")
        page = pdf[int(table.page) - 1]   # Docling page_no is 1-based; fitz is 0-based
        words = page.get_text("words")
        if not words:
            return
        page_w = page.rect.width
    except Exception:
        return

    # Bound the scan to the table's own region (header bottom .. body bottom) so
    # the header row and any surrounding paragraph text are not picked up.
    y_lo = table.header_bottom if table.header_bottom > 0 else 0.0
    y_hi = table.body_bottom if table.body_bottom > 0 else page.rect.height

    # Cluster words into visual lines by baseline, tolerating sub-pixel y0
    # jitter: PyMuPDF can report ONE rendered line as several y0s ~0.1-1px apart
    # (e.g. "WizardCoder" at 232.49 vs the rest of its row at 232.58). Anchor
    # each cluster to its FIRST word's y0 so gradual drift cannot chain lines.
    lines: list[tuple[float, list]] = []
    for w in sorted(words, key=lambda w: w[1]):
        x0, y0, x1, y1, txt = w[0], w[1], w[2], w[3], w[4]
        if not txt.strip() or not (y_lo <= y0 <= y_hi):
            continue
        if lines and y0 - lines[-1][0] <= _TABLE_EV_Y_EPS:
            lines[-1][1].append((x0, x1, txt, y0))
        else:
            lines.append((y0, [(x0, x1, txt, y0)]))
    if len(lines) < 2:
        return
    ncols = len(col_starts)

    def _assign(ws) -> list[list[str]]:
        # A word belongs to the column whose START it begins at or near (within
        # a small tolerance — a word exactly at/just off a column start must
        # stay in ITS column, robust to float precision). Words inside a
        # multi-word cell that are far from any column start fall to the
        # containing start-based band, so wide cells keep all their words in
        # one column.
        pc: list[list[str]] = [[] for _ in col_starts]
        bounds = col_starts + [page_w]
        for x0, _, txt, _ in ws:
            best, best_d = None, _TABLE_EV_TOL
            for i, s in enumerate(col_starts):
                d = abs(x0 - s)
                if d <= best_d:
                    best_d, best = d, i
            if best is not None:
                pc[best].append(txt)
                continue
            for i in range(len(bounds) - 1):
                if bounds[i] - _TABLE_EV_TOL <= x0 < bounds[i + 1]:
                    pc[i].append(txt)
                    break
        return pc

    # line records: (y, words-per-column); y = the line's first baseline.
    recs: list[tuple[float, list[list[str]]]] = [
        (round(ay, 1), _assign(ws)) for ay, ws in lines
    ]
    nlines = len(recs)

    # Per-column line counts. A column's line count >= the number of rows it
    # spans; a wrapping column shows MORE lines than rows.
    col_counts = [sum(1 for _, pc in recs if pc[c]) for c in range(ncols)]
    row_est = min(col_counts)

    # A split is evidence-backed only when >= 2 columns independently carry the
    # same minimum number of lines: those lines are then the non-wrapping rows.
    # A single column at the minimum is just a column with short cells — it
    # cannot prove the line boundaries are ROW boundaries (a wrapped row can
    # show as many lines as its longest cell), so the faithful collapsed table
    # stands unchanged.
    if row_est < 2 or sum(1 for c in col_counts if c == row_est) < 2:
        return

    # Row anchors = the row_est lines of the first minimum-count column. These
    # are the lines that carry one logical row each.
    anchor_col = next(c for c in range(ncols) if col_counts[c] == row_est)
    anchor_idx = [i for i in range(nlines) if recs[i][1][anchor_col]]
    anchors = [recs[i][0] for i in anchor_idx]  # ascending (lines are in y order)
    row_of_line = {i: j for j, i in enumerate(anchor_idx)}

    # Assign every other line to the row it continues. A wrap line flows DOWN
    # from its row start, so an anchor directly above (within ROW_GAP) wins;
    # otherwise the nearest anchor (handles a sub-pixel sibling split above its
    # row's anchor). Wrapped cells absorb their continuation lines.
    assigned: list[int | None] = [None] * nlines
    for i in range(nlines):
        if i in row_of_line:
            assigned[i] = row_of_line[i]
            continue
        y = recs[i][0]
        best_j, best_key = None, None
        for j, ay in enumerate(anchors):
            d = abs(y - ay)
            key = (0, d) if (ay <= y + _TABLE_EV_Y_EPS and d <= _TABLE_EV_ROW_GAP) else (1, d)
            if best_key is None or key < best_key:
                best_key, best_j = key, j
        assigned[i] = best_j

    # Fold each row's lines into cells (left-to-right column order).
    rows_map: list[list[str]] = [[""] * ncols for _ in anchors]
    for i, (_, pc) in enumerate(recs):
        j = assigned[i]
        if j is None:
            continue
        row = rows_map[j]
        for c in range(ncols):
            if pc[c]:
                row[c] = (row[c] + " " + " ".join(pc[c])).strip()

    new_rows: list[list[str]] = []
    for row in rows_map:
        maxw = max((len(v.split()) for v in row), default=0)
        if maxw <= _TABLE_EV_MAX_WORDS_PER_COL:
            new_rows.append(row)
    if len(new_rows) < 2:
        return  # evidence insufficient -> keep the faithful collapsed table
    table.rows = [r for r in new_rows if r != table.header] or new_rows
    table.confidence = 0.9
    table.source = "docling+evidence"


def _map_image(item, rec: RecoveredDocument, doc=None, page: int = 0, bbox=None) -> None:
    img = getattr(item, "image", None)
    blob = b""
    if img is not None:
        try:
            # Docling >=2.x: PictureItem.image is a lazy ImageRef exposing the
            # crop via `.pil_image`; older versions hand us a PIL image directly.
            pil = getattr(img, "pil_image", None) or img
            buf = _io.BytesIO()
            pil.save(buf, format="PNG")
            blob = buf.getvalue()
        except Exception:
            blob = b""
    caption = _item_caption(item, doc)
    # No silent loss: even when Docling produced no pixels (image generation
    # unavailable), the picture object is preserved with its caption and
    # geometry so downstream sees the figure existed. `storage_ref` stays empty
    # in that case; the DOM still carries the ImageObject.
    rec.images.append(
        RecoveredImage(
            page=page,
            bbox=bbox,
            mime="image/png" if blob else "",
            checksum=hashlib.sha256(blob).hexdigest() if blob else "",
            caption=caption,
            blob=blob,
        )
    )


# --- helpers ----------------------------------------------------------------
def _label_name(item) -> str:
    label = getattr(item, "label", None)
    if label is None:
        return ""
    value = getattr(label, "value", None)
    if isinstance(value, str):
        return value
    return str(label)


def _bbox(prov, page_h: float = 0.0):
    """Normalize a Docling item's prov bbox to the DOM's PDF-point (top-left)
    coordinate space. Docling's floating-item prov boxes use a bottom-left
    origin (y grows upward); PyMuPDF and our DOM use top-left (y grows down).
    Cell-level bboxes (e.g. TableCell) are already top-left. When the origin is
    absent (test doubles) or the page height is unknown, the box is returned
    unchanged."""
    if prov is None:
        return None
    b = getattr(prov, "bbox", None)
    if b is None:
        return None
    l = getattr(b, "l", None)
    if l is None:
        l = getattr(b, "x0", None)
    t = getattr(b, "t", getattr(b, "y0", None))
    r = getattr(b, "r", getattr(b, "x1", None))
    bt = getattr(b, "b", getattr(b, "y1", None))
    if None in (l, t, r, bt):
        return None
    origin = getattr(b, "coord_origin", None)
    origin = getattr(origin, "value", None) or origin
    if page_h and origin and "BOTTOMLEFT" in str(origin):
        # Bottom-left: y grows UP, so `t` is the top edge (larger y) and `b`
        # the bottom edge. Map to top-left (y grows DOWN) by mirroring across
        # the page height: new_t = page_h - old_t, new_b = page_h - old_b.
        t, bt = page_h - t, page_h - bt
    return (float(l), float(t), float(r), float(bt))


def _layout_model_name(converter) -> str | None:
    try:
        pipe = converter.pipeline
        artifacts = getattr(pipe, "artifacts", None)
        lm = getattr(artifacts, "layout_model", None)
        name = getattr(lm, "name", None)
        return name or None
    except Exception:
        return None


def _slug(filename: str) -> str:
    base = (filename or "").replace("\\", "/").split("/")[-1]
    return base.rsplit(".", 1)[-1].lower() if "." in base else "pdf"


def _convert(converter, data: bytes, filename: str) -> object | None:
    """Convert bytes to a DoclingDocument via a temp file (version-robust)."""
    suffix = f".{_slug(filename)}"
    fd, path = tempfile.mkstemp(suffix=suffix)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        result = converter.convert(path)
        # No status-value guessing: a missing/partial document simply falls back.
        return getattr(result, "document", None)
    except Exception:
        return None
    finally:
        try:
            os.unlink(path)
        except Exception:
            pass
