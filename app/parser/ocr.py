"""On-prem OCR (RapidOCR-onnxruntime) wrapper.

Design (from spec + SYN2/3 privacy posture): OCR runs in the customer's own
environment — raw data never leaves the hospital. The engine is imported
lazily and defensively: if it ever fails to load, `ocr_image` returns []
and the caller can treat the region as OCR-unavailable rather than crash.

RapidOCR returns, per text line: [quad(4 pts), text, confidence].
"""
from __future__ import annotations

import threading
from pathlib import Path

# Minimize import cost + keep module importable without the heavy engine.
_engine = None
_lock = threading.Lock()
_engine_name = "rapidocr-onnxruntime"


def engine_available() -> bool:
    global _engine
    if _engine is None:
        with _lock:
            if _engine is None:
                try:
                    from rapidocr_onnxruntime import RapidOCR  # type: ignore
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


def ocr_image(image) -> list[tuple[str, tuple[float, float, float, float], float]]:
    """Run OCR on an image. Returns list of (text, bbox, confidence).

    `image` may be raw bytes, a file path, a numpy array, or a PIL image.
    Returns [] if the engine is not available.

    NOTE (bugfix, 2026-08-05): RapidOCR.__call__ only accepts str |
    numpy.ndarray | bytes | pathlib.Path — passing a PIL Image raises
    LoadImageError, which used to be swallowed into an empty result. A PIL
    image is now converted to a numpy array first.
    """
    if not engine_available():
        return []
    if not isinstance(image, (str, bytes, Path)):
        from PIL import Image as _PIL
        if isinstance(image, _PIL.Image):
            import numpy as _np
            image = _np.asarray(image)
    try:
        result, _elapse = _engine(image)
    except Exception:
        return []
    if not result:
        return []
    out = []
    for item in result:
        try:
            quad, text, conf = item
            out.append((str(text), _quad_to_bbox(quad), float(conf)))
        except Exception:
            continue
    return out


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