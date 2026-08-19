"""Per-page result contract — the durable + processing unit of the
page-centric parser execution model (ADR-013).

A `PageResult` is what each page engine returns and what the page store
persists. It is pure-data (dataclass) so it pickles cleanly across the
`ProcessPoolExecutor` boundary and serializes to `page-v<ver>.docJSON`.

`Recovered*` parts are reused unchanged (no schema change) — only (de)
serialized here for the page-store artifact.
"""
from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import asdict, dataclass, field, fields
from enum import Enum
from typing import Any

from .parts import (
    RecoveredAnnotation,
    RecoveredBlock,
    RecoveredImage,
    RecoveredTable,
)

PAGE_SCHEMA_VERSION = "v0.1.0"


class PageStatus(str, Enum):
    PENDING = "pending"
    OK = "ok"
    PARTIAL = "partial"
    FAILED = "failed"
    DEAD = "dead"


# --- (de)serialization of Recovered* parts --------------------------------
def _bbox_to_dict(bbox):
    return list(bbox) if bbox is not None else None


def _bbox_from_list(b):
    return tuple(b) if b is not None else None


def _block_to_dict(b: RecoveredBlock) -> dict:
    return {
        "page": b.page,
        "kind": b.kind,
        "text": b.text,
        "bbox": _bbox_to_dict(b.bbox),
        "seq": b.seq,
        "confidence": b.confidence,
        "font_size": b.font_size,
        "bold": b.bold,
        "source": b.source,
        "ocr_engine": b.ocr_engine,
    }


def _block_from_dict(d: dict) -> RecoveredBlock:
    return RecoveredBlock(
        page=d.get("page", 0),
        kind=d.get("kind", "paragraph"),
        text=d.get("text", ""),
        bbox=_bbox_from_list(d.get("bbox")),
        seq=d.get("seq", 0),
        confidence=d.get("confidence", 1.0),
        font_size=d.get("font_size"),
        bold=d.get("bold"),
        source=d.get("source", "text"),
        ocr_engine=d.get("ocr_engine"),
    )


def _table_to_dict(t: RecoveredTable) -> dict:
    return {
        "page": t.page,
        "bbox": _bbox_to_dict(t.bbox),
        "header": t.header,
        "rows": t.rows,
        "source": t.source,
        "confidence": t.confidence,
        "caption": t.caption,
        "column_starts": t.column_starts,
        "header_bottom": t.header_bottom,
        "body_bottom": t.body_bottom,
    }


def _table_from_dict(d: dict) -> RecoveredTable:
    return RecoveredTable(
        page=d.get("page", 0),
        bbox=_bbox_from_list(d.get("bbox")),
        header=d.get("header", []),
        rows=d.get("rows", []),
        source=d.get("source", "native"),
        confidence=d.get("confidence", 1.0),
        caption=d.get("caption", ""),
        column_starts=d.get("column_starts", []),
        header_bottom=d.get("header_bottom", 0.0),
        body_bottom=d.get("body_bottom", 0.0),
    )


def _image_to_dict(img: RecoveredImage) -> dict:
    return {
        "page": img.page,
        "bbox": _bbox_to_dict(img.bbox),
        "storage_ref": img.storage_ref,
        "mime": img.mime,
        "checksum": img.checksum,
        "caption": img.caption,
        "blob": base64.b64encode(img.blob).decode("ascii") if img.blob else "",
    }


def _image_from_dict(d: dict) -> RecoveredImage:
    blob = d.get("blob", "") or ""
    return RecoveredImage(
        page=d.get("page", 0),
        bbox=_bbox_from_list(d.get("bbox")),
        storage_ref=d.get("storage_ref", ""),
        mime=d.get("mime", ""),
        checksum=d.get("checksum", ""),
        caption=d.get("caption", ""),
        blob=base64.b64decode(blob) if blob else b"",
    )


def _annotation_to_dict(a: RecoveredAnnotation) -> dict:
    return {"page": a.page, "kind": a.kind, "text": a.text}


def _annotation_from_dict(d: dict) -> RecoveredAnnotation:
    return RecoveredAnnotation(
        page=d.get("page", 0), kind=d.get("kind", "note"), text=d.get("text", "")
    )


@dataclass
class PageResult:
    doc_id: str = ""
    page_index: int = 0
    route: str = ""
    status: PageStatus = PageStatus.PENDING
    blocks: list[RecoveredBlock] = field(default_factory=list)
    tables: list[RecoveredTable] = field(default_factory=list)
    images: list[RecoveredImage] = field(default_factory=list)
    annotations: list[RecoveredAnnotation] = field(default_factory=list)
    content_present: bool = False
    errors: list[dict] = field(default_factory=list)
    engine_version: str | None = None
    docling_version: str | None = None
    source_hash: str = ""
    checksum: str = ""
    timings: dict = field(default_factory=dict)
    page_sizes: dict = field(default_factory=dict)

    # --- content check (the per-page ≥1 recovered content predicate) --------
    def _compute_content_present(self) -> bool:
        if self.blocks:
            return True
        if any(t.rows for t in self.tables):
            return True
        for t in self.tables:
            for r in t.rows:
                if any((c.text if hasattr(c, "text") else str(c)).strip() for c in r):
                    return True
        if any(img.blob or img.storage_ref or img.caption for img in self.images):
            return True
        return False

    def __post_init__(self):
        if not isinstance(self.status, PageStatus):
            self.status = PageStatus(self.status)
        # Derive content_present from parts when not explicitly set by a caller,
        # so a freshly-built PageResult is self-consistent.
        if not self.content_present:
            self.content_present = self._compute_content_present()

    # --- the per-page slice consumed by the Assembler -----------------------
    def to_recovered_slice(self):
        return (self.blocks, self.tables, self.images, self.annotations)

    # --- checksum (idempotent dedup / resume) -------------------------------
    def compute_checksum(self) -> str:
        """sha256 over canonical JSON of the page parts (content-address)."""
        payload = {
            "page_index": self.page_index,
            "blocks": [_block_to_dict(b) for b in self.blocks],
            "tables": [_table_to_dict(t) for t in self.tables],
            "images": [_image_to_dict(i) for i in self.images],
            "annotations": [_annotation_to_dict(a) for a in self.annotations],
        }
        data = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
        return hashlib.sha256(data).hexdigest()

    # --- (de)serialization --------------------------------------------------
    def to_dict(self) -> dict:
        # Ensure the persisted checksum is always content-derived (never an
        # empty string), so a stored page is self-consistent and resumable.
        if not self.checksum:
            self.checksum = self.compute_checksum()
        return {
            "schema_version": PAGE_SCHEMA_VERSION,
            "doc_id": self.doc_id,
            "page_index": self.page_index,
            "route": self.route,
            "status": self.status.value,
            "blocks": [_block_to_dict(b) for b in self.blocks],
            "tables": [_table_to_dict(t) for t in self.tables],
            "images": [_image_to_dict(i) for i in self.images],
            "annotations": [_annotation_to_dict(a) for a in self.annotations],
            "content_present": self.content_present,
            "errors": self.errors,
            "engine_version": self.engine_version,
            "docling_version": self.docling_version,
            "source_hash": self.source_hash,
            "checksum": self.checksum,
            "timings": self.timings,
            "page_sizes": {str(k): v for k, v in self.page_sizes.items()},
        }

    @classmethod
    def from_dict(cls, d: dict) -> "PageResult":
        return cls(
            doc_id=d.get("doc_id", ""),
            page_index=d.get("page_index", 0),
            route=d.get("route", ""),
            status=PageStatus(d.get("status", "pending")),
            blocks=[_block_from_dict(b) for b in d.get("blocks", [])],
            tables=[_table_from_dict(t) for t in d.get("tables", [])],
            images=[_image_from_dict(i) for i in d.get("images", [])],
            annotations=[_annotation_from_dict(a) for a in d.get("annotations", [])],
            content_present=d.get("content_present", False),
            errors=d.get("errors", []),
            engine_version=d.get("engine_version"),
            docling_version=d.get("docling_version"),
            source_hash=d.get("source_hash", ""),
            checksum=d.get("checksum", ""),
            timings=d.get("timings", {}),
            page_sizes={int(k): v for k, v in (d.get("page_sizes") or {}).items()},
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)

    @classmethod
    def from_json(cls, s: str) -> "PageResult":
        return cls.from_dict(json.loads(s))