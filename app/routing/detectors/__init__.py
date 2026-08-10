"""Detector registry + plugin hook (architecture D1: a plain list, additive, no
plugin-discovery framework; spec §5, §16).

`get_detectors()` returns the 9 built-in detectors in a fixed priority order.
`register_detector()` appends to the registry so tests (or a future plugin)
add a detector without touching the router or pipeline. Registry stays a plain
list — no auto-discovery.
"""
from __future__ import annotations

from .base import Detector, DetectorResult
from .font_detector import FontDetector
from .form_detector import FormDetector
from .image_detector import ImageDetector
from .layout_detector import LayoutDetector
from .meta_detector import MetadataDetector
from .ocr_detector import OcrDetector
from .reading_order_detector import ReadingOrderDetector
from .table_detector import TableDetector
from .text_detector import TextDetector

_BUILTIN: list[type[Detector]] = [
    MetadataDetector,
    TextDetector,
    ImageDetector,
    LayoutDetector,
    OcrDetector,
    TableDetector,
    FormDetector,
    ReadingOrderDetector,
    FontDetector,
]

DETECTOR_PRIORITY: list[str] = [cls.name for cls in _BUILTIN]

_custom: list[type[Detector]] = []


def register_detector(detector_cls: type[Detector]) -> None:
    """Additive registry hook (spec §16; no plugin-discovery framework)."""
    if not isinstance(detector_cls, type) or not issubclass(detector_cls, Detector):
        raise TypeError("register_detector expects a Detector subclass")
    if detector_cls not in _custom and detector_cls not in _BUILTIN:
        _custom.append(detector_cls)


def get_detectors() -> list[Detector]:
    """Instantiate built-in detectors (priority order) + any registered ones."""
    return [cls() for cls in _BUILTIN + _custom]


__all__ = [
    "Detector",
    "DetectorResult",
    "get_detectors",
    "register_detector",
    "DETECTOR_PRIORITY",
]