"""On-prem OCR (RapidOCR-onnxruntime) wrapper.

Design (from spec + SYN2/3 privacy posture): OCR runs in the customer's own
environment — raw data never leaves the hospital. The engine is imported
lazily and defensively: if it ever fails to load, `ocr_image` returns []
and the caller can treat the region as OCR-unavailable rather than crash.

Engine: the modern `rapidocr` package (PP-OCRv6 models bundled with the pip
package, fully on-prem — no Hugging Face / network inference). This is the
SAME engine package + model files Docling's OCR stage uses, so the pipeline is
unified on one RapidOCR (v6) instead of two different models (2026-08-11).
The legacy `rapidocr_onnxruntime` (PP-OCRv4) package has been removed — this is
the only OCR dependency.

Output: per text line [quad(4 pts), text, confidence].
"""
from __future__ import annotations

import threading
from pathlib import Path

# Minimize import cost + keep module importable without the heavy engine.
_engine = None
_lock = threading.Lock()
# engine_name() reports the onnxruntime family; version kept generic so the
# upgrade to the v6 `rapidocr` package is transparent to provenance consumers.
_engine_name = "rapidocr-onnxruntime"

# --- OCR memory guard -------------------------------------------------------
# RapidOCR/onnxruntime builds C++ tensors proportional to the IMAGE PIXEL AREA
# during its `preprocess`/`ocr` stage. On a large-rendered page that allocation
# can exceed what the process can allocate -> `std::bad_alloc` (the "Stage
# preprocess failed ...: std::bad_alloc" lines). The page-centric model already
# CONTAINS these (per-page retry + dead-letter), but we additionally bound the
# image size BEFORE it reaches RapidOCR so the allocation is far less likely to
# fail in the first place. `OCR_MAX_EDGE` caps the longest edge (px) of any image
# handed to RapidOCR. 2000px is comfortably below the OOM threshold on a 4GB box
# while preserving legible text for normal documents. (Hardcoded; tune here if a
# specific corpus needs more/less fidelity vs alloc headroom.)
OCR_MAX_EDGE = 2000


def engine_available() -> bool:
    global _engine
    if _engine is None:
        with _lock:
            if _engine is None:
                # The modern `rapidocr` package (PP-OCRv6 — the same engine
                # Docling uses). Fully on-prem; models bundled in the venv.
                try:
                    from rapidocr import RapidOCR  # type: ignore
                    _engine = RapidOCR()
                except Exception:
                    _engine = False
    return _engine is not False  # type: ignore[comparison-overlap]


def engine_name() -> str:
    return _engine_name if engine_available() else None


def _quad_to_bbox(quad) -> tuple[float, float, float, float]:
    xs = [pt[0] for pt in quad]
    ys = [pt[1] for pt in quad]
    return (min(xs), min(ys), max(xs), max(ys))


def downscale_for_ocr(data: bytes, max_edge: int = OCR_MAX_EDGE) -> bytes:
    """Shrink an image so its longest edge <= `max_edge` before it reaches
    RapidOCR. Keeps the byte stream (PNG) the OCR wrapper expects.

    RapidOCR's C++ preprocess tensor is proportional to pixel area, so capping
    the edge (=> 4x smaller area at most) is the single biggest lever against
    `std::bad_alloc` on large pages. Defensive: any failure returns the original
    bytes so callers never lose the image (the existing retry/dead-letter net
    still applies downstream)."""
    try:
        from io import BytesIO
        from PIL import Image

        img = Image.open(BytesIO(data))
        w, h = img.size
        longest = max(w, h)
        if longest <= max_edge:
            return data
        scale = max_edge / float(longest)
        nw, nh = max(1, int(round(w * scale))), max(1, int(round(h * scale)))
        img = img.resize((nw, nh), Image.LANCZOS)
        out = BytesIO()
        img.convert("RGB").save(out, format="PNG")
        return out.getvalue()
    except Exception:
        return data


def _extract_results(res) -> list[tuple[str, tuple[float, float, float, float], float]]:
    """Parse a `rapidocr` v6 result into (text, bbox, confidence) lines.

    The engine returns a `RapidOCROutput` exposing `.txts` / `.scores` /
    `.boxes` (numpy arrays). Malformed items are skipped; a failure in one line
    never drops the doc; any non-conforming result yields [] safely.
    """
    out: list[tuple[str, tuple[float, float, float, float], float]] = []
    if res is None or not hasattr(res, "txts") or not hasattr(res, "boxes"):
        return out
    txts = res.txts or ()
    for i, text in enumerate(txts):
        try:
            quad = res.boxes[i]
            conf = res.scores[i]
            out.append((str(text), _quad_to_bbox(quad), float(conf)))
        except Exception:
            continue
    return out


def ocr_image(image) -> list[tuple[str, tuple[float, float, float, float], float]]:
    """Run OCR on an image. Returns list of (text, bbox, confidence).

    `image` may be raw bytes, a file path, a numpy array, or a PIL image.
    Returns [] if the engine is not available.

    NOTE (bugfix, 2026-08-05): the engine's `__call__` only accepts str |
    numpy.ndarray | bytes | pathlib.Path — a PIL Image raised an error that used
    to be swallowed into an empty result. A PIL image is converted to a numpy
    array first (the modern `rapidocr` also accepts numpy/bytes, not PIL).
    """
    if not engine_available():
        return []
    if not isinstance(image, (str, bytes, Path)):
        from PIL import Image as _PIL
        if isinstance(image, _PIL.Image):
            import numpy as _np
            image = _np.asarray(image)
    try:
        res = _engine(image)
    except Exception:
        return []
    return _extract_results(res)


def ocr_bytes(data: bytes) -> list[tuple[str, tuple[float, float, float, float], float]]:
    """OCR raw image bytes (loads via Pillow). Returns [] if unavailable."""
    if not engine_available():
        return []
    from io import BytesIO
    from PIL import Image

    img = Image.open(BytesIO(data)).convert("RGB")
    return ocr_image(img)


def batch_ocr_bytes(items: list[bytes]) -> list[list[tuple[str, tuple[float, float, float, float], float]]]:
    """OCR many images sequentially, reusing ONE loaded engine.

    The heavy model is loaded once (see `engine_available`) and reused across
    all items, avoiding a cold-start per image. This is a warm loop, not a true
    model-batched call (RapidOCR is per-image). Returns a per-item list of
    (text, bbox, confidence); an unavailable engine yields [] for every item.

    NOTE: not yet wired into the pipeline (scale-batch spec overstates this);
    kept as the seam a future batch caller would use.
    """
    if not engine_available():
        return [[] for _ in items]
    from io import BytesIO
    from PIL import Image

    out = []
    for data in items:
        try:
            img = Image.open(BytesIO(data)).convert("RGB")
            out.append(ocr_image(img))
        except Exception:
            out.append([])
    return out


def warm() -> bool:
    """Preload the OCR engine so a worker pool doesn't pay cold-start mid-run."""
    return engine_available()