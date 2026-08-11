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

        # Resolve the pipeline-options class (renamed across versions).
        try:
            from docling.datamodel.pipeline_options import PipelineOptions as PipelineOptions
        except Exception:
            from docling.datamodel.pipeline_options import PdfPipelineOptions as PipelineOptions

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

        try:
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
    try:
        opts = cls(do_ocr=ocr, do_code_formula=False)
    except Exception:
        try:
            opts = cls()
            for attr in ("do_ocr", "do_code_formula"):
                try:
                    setattr(opts, attr, ocr if attr == "do_ocr" else False)
                except Exception:
                    pass
        except Exception:
            return None
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

    doc = _convert(converter, data, filename)
    if doc is None:
        return None

    rec = RecoveredDocument(detected_type=_slug(filename), mime="application/pdf")
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

    for entry in doc.iterate_items():
        try:
            # docling-core yields (item, level) tuples in recent versions; older
            # versions yielded the item directly. Handle both.
            item = entry[0] if isinstance(entry, tuple) and entry else entry
            _map_item(item, rec)
        except Exception:
            continue  # a bad item degrades; never crashes the doc

    # Safety valve: if Docling recovered nothing readable, let the native path
    # try (e.g. scanned PDFs that need OCR, which we deliberately skip).
    if not rec.blocks and not rec.tables:
        return None
    return rec


def _map_item(item, rec: RecoveredDocument) -> None:
    prov = item.prov[0] if getattr(item, "prov", None) else None
    page = int(getattr(prov, "page_no", 0) or 0)
    bbox = _bbox(prov)

    label = _label_name(item)
    if label == "table":
        _map_table(item, rec, page, bbox)
        return
    if label == "picture":
        _map_image(item, rec, page, bbox)
        return

    kind = _KIND.get(label, "paragraph")
    text = (getattr(item, "text", "") or "").strip()
    if not text:
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


def _map_table(item, rec: RecoveredDocument, page: int, bbox) -> None:
    table = getattr(item, "table", None) or getattr(item, "data", None)
    if table is None:
        return

    # Preferred: the structured dataframe (handles merged cells by repetition).
    try:
        df = item.export_to_dataframe()
        if df is not None and not df.empty:
            header = ["" if c is None else str(c) for c in df.columns]
            rows = [["" if v is None else str(v) for v in row] for row in df.itertuples(index=False)]
            rec.tables.append(RecoveredTable(page=page, bbox=bbox, header=header, rows=rows, source="docling"))
            return
    except Exception:
        pass

    # Fallback: rebuild the grid from OOT cells.
    cells = getattr(table, "table_cells", None) or []
    if not cells:
        return
    grid: dict[tuple[int, int], str] = {}
    ncols = 0
    nrows = 0
    for c in cells:
        r = int(getattr(c, "start_row_offset_idx", 0) or 0)
        col = int(getattr(c, "start_col_offset_idx", 0) or 0)
        nrows = max(nrows, r + 1)
        ncols = max(ncols, col + 1)
        grid[(r, col)] = getattr(c, "text", "") or ""
    if ncols == 0 or nrows == 0:
        return
    header = [grid.get((0, c), "") for c in range(ncols)]
    rows = [[grid.get((r, c), "") for c in range(ncols)] for r in range(1, nrows)]
    rec.tables.append(RecoveredTable(page=page, bbox=bbox, header=header, rows=rows, source="docling"))


def _map_image(item, rec: RecoveredDocument, page: int, bbox) -> None:
    img = getattr(item, "image", None)
    if img is None:
        return
    try:
        buf = _io.BytesIO()
        img.save(buf, format="PNG")
        blob = buf.getvalue()
    except Exception:
        return
    rec.images.append(
        RecoveredImage(
            page=page,
            bbox=bbox,
            mime="image/png",
            checksum=hashlib.sha256(blob).hexdigest(),
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


def _bbox(prov):
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
