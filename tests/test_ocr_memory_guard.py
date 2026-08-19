"""Regression tests for the OCR memory guard (ADR-013 addendum).

The guard (`ocr.downscale_for_ocr` / `OCR_MAX_EDGE`) bounds the longest edge of
any image handed to RapidOCR so its C++ preprocess tensor (proportional to pixel
AREA) cannot blow past process memory -> `std::bad_alloc`. These tests verify the
downscaling behaviour WITHOUT needing the heavy OCR engine loaded.
"""
from __future__ import annotations

import io

from PIL import Image

from app.parser import ocr


def _make_png(width: int, height: int, color=(30, 80, 200)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buf, format="PNG")
    return buf.getvalue()


def test_downscale_passes_through_small_image():
    data = _make_png(800, 600)
    out = ocr.downscale_for_ocr(data, ocr.OCR_MAX_EDGE)
    img = Image.open(io.BytesIO(out))
    # Unchanged dimensions when under the cap.
    assert img.size == (800, 600)


def test_downscale_caps_longest_edge():
    data = _make_png(8000, 2000)  # longest edge far above cap
    out = ocr.downscale_for_ocr(data, ocr.OCR_MAX_EDGE)
    img = Image.open(io.BytesIO(out))
    longest = max(img.size)
    # Longest edge must be <= cap (allow 1px rounding slack).
    assert longest <= ocr.OCR_MAX_EDGE + 1
    # Aspect ratio preserved under the uniform scale.
    assert img.size == (ocr.OCR_MAX_EDGE, int(round(2000 * ocr.OCR_MAX_EDGE / 8000)))


def test_downscale_falls_back_on_garbage():
    # Non-image bytes must return the original data (defensive contract).
    garbage = b"not an image"
    assert ocr.downscale_for_ocr(garbage, ocr.OCR_MAX_EDGE) is garbage


def test_ocr_max_edge_constant_sane():
    # 2000px longest edge -> at most 4M px -> well under the 4GB OOM threshold.
    assert ocr.OCR_MAX_EDGE >= 1000
    assert ocr.OCR_MAX_EDGE <= 4000
