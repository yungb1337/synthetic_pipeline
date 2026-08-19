"""Image engine — standalone image OCR (one page per image file, ADR-013 T8).

Reuses the shared `_image_bytes` helper so behaviour matches `Loaders._image`
exactly. OCR is gated by `config.ocr_enabled`; with it off the page is OK with
zero blocks (never a crash).
"""
from __future__ import annotations

import hashlib

from ..config import ParserConfig
from ..loaders.loaders import _image_bytes
from ..page_result import PageResult, PageStatus
from .base import IMAGE, PageWorkItem


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class ImageEngine:
    route_band = IMAGE

    def __init__(self, config: ParserConfig):
        self.config = config

    def process(self, item: PageWorkItem) -> PageResult:
        from ..parts import RecoveredDocument
        from ..detection import Detected
        from ..mime import MIME as _MIME

        rec = RecoveredDocument(detected_type="image", mime=_MIME["png"])
        try:
            data = open(item.src_path, "rb").read()
        except Exception as e:
            return PageResult(
                doc_id=item.doc_id, page_index=0, route=IMAGE, status=PageStatus.FAILED,
                errors=[{"page_no": 1, "category": "image_read", "message": str(e)}],
                source_hash=item.source_hash,
            )
        _image_bytes(rec, data, self.config)
        # Carry the image blob itself as a RecoveredImage so the page is recorded
        # as having content even when OCR yields nothing (a blank-but-valid image
        # is still a successfully parsed page — content_present must be True, and
        # the image must not be silently dropped from the DOM).
        from ..parts import RecoveredImage

        images = [
            RecoveredImage(
                page=0,
                mime=rec.mime,
                checksum=_sha256(data),
                blob=data,
            )
        ]
        return PageResult(
            doc_id=item.doc_id, page_index=0, route=IMAGE, status=PageStatus.OK,
            blocks=rec.blocks, images=images, timings=rec.timings,
            source_hash=item.source_hash,
        )
