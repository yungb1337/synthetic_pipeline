"""Storage for parser outputs.

`Store` is the seam between parsing and persistence. A `FilesystemStore`
defaults for v1: raw bytes, DOM JSON, and extracted images go to an immutable,
hash-keyed layout. Swapping to S3/GCS/Postgres means a new Store impl — the
parser pipeline is unchanged (dependency inversion).
"""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from pathlib import Path

from .dom import Document
from .parts import RecoveredImage


class Store(ABC):
    @abstractmethod
    def put_raw(self, doc_id: str, sha256: str, data: bytes, suffix: str) -> str: ...
    @abstractmethod
    def put_dom(self, doc_id: str, doc: Document) -> str: ...
    @abstractmethod
    def put_normalized(self, doc_id: str, doc: Document) -> str: ...
    @abstractmethod
    def put_image(self, doc_id: str, image: RecoveredImage) -> str: ...
    @abstractmethod
    def get(self, key: str) -> bytes | None: ...


class FilesystemStore(Store):
    """Immutable, content-addressed layout under `root`.

        root/
          raw/<sha256>.{suffix}
          dom/<doc_id>/dom-v{version}.docJSON
          images/<doc_id>/img-<n>.<ext>
    """

    def __init__(self, root: str):
        self.root = Path(root)
        (self.root / "raw").mkdir(parents=True, exist_ok=True)
        (self.root / "dom").mkdir(parents=True, exist_ok=True)
        (self.root / "images").mkdir(parents=True, exist_ok=True)

    def put_raw(self, doc_id: str, sha256: str, data: bytes, suffix: str) -> str:
        key = f"raw/{sha256}.{suffix}"
        p = self.root / key
        if not p.exists():
            p.write_bytes(data)
        return key

    def put_dom(self, doc_id: str, doc: Document) -> str:
        key = f"dom/{doc_id}.dom.json"
        p = self.root / key
        p.write_text(doc.model_dump_json(indent=2), encoding="utf-8")
        return key

    def put_normalized(self, doc_id: str, doc: Document) -> str:
        key = f"dom/{doc_id}.norm.json"
        p = self.root / key
        p.write_text(doc.model_dump_json(indent=2), encoding="utf-8")
        return key

    def put_image(self, doc_id: str, image: RecoveredImage) -> str:
        if not image.blob:
            return image.storage_ref or ""
        key = f"images/{doc_id}-{image.page}-{len(list(self.root.glob(f'images/{doc_id}-*')))}.{_img_ext(image.mime)}"
        p = self.root / key
        p.write_bytes(image.blob)
        return key

    def get(self, key: str) -> bytes | None:
        p = self.root / key
        return p.read_bytes() if p.exists() else None


def _img_ext(mime: str) -> str:
    return {"image/png": "png", "image/jpeg": "jpg", "image/tiff": "tiff", "image/gif": "gif"}.get(mime, "bin")


def to_json_bytes(doc: Document) -> bytes:
    return json.dumps(json.loads(doc.model_dump_json()), ensure_ascii=False).encode("utf-8")