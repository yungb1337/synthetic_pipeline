"""Storage for parser outputs.

`Store` is the seam between parsing and persistence. A `FilesystemStore`
defaults for v1: raw bytes, DOM JSON, and extracted images go to an immutable,
hash-keyed layout. Swapping to S3/GCS/Postgres means a new Store impl — the
parser pipeline is unchanged (dependency inversion).
"""
from __future__ import annotations

import hashlib
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
    """Layout under `root`.

        root/
          raw/<sha256>.{suffix}            immutable, content-addressed (write-if-absent)
          dom/<doc_id>/dom-v{version}.docJSON   versioned (one file per doc_id × version)
          dom/<doc_id>/norm-v{version}.docJSON
          images/<doc_id>/<sha256>.{ext}   immutable, content-addressed (write-if-absent)

    Raw bytes and extracted images are content-addressed and written only if
    absent. DOM outputs are keyed by `doc_id` and the producing parser/
    normalizer version (the role prefix, e.g. `parser-v0.1.0`, is stripped to
    `v0.1.0`), so lineage is retained across versions; a re-write of the same
    version is a deterministic no-op (same doc + version -> identical bytes).
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
        version = _version_suffix(doc.provenance.parser_version) if doc.provenance else "unknown"
        return self._put_dom_json(doc_id, "dom", version, doc)

    def put_normalized(self, doc_id: str, doc: Document) -> str:
        version = _version_suffix(doc.provenance.normalizer_version) if doc.provenance else "unknown"
        return self._put_dom_json(doc_id, "norm", version, doc)

    def _put_dom_json(self, doc_id: str, prefix: str, version: str, doc: Document) -> str:
        # versioned output (ADR #8): each parser/normalizer version gets its own
        # file under dom/<doc_id>/ so prior versions are retained. A re-write of
        # the SAME version overwrites that file — a deterministic no-op since a
        # given doc_id + version always produces identical bytes.
        key = f"dom/{doc_id}/{prefix}-{version}.docJSON"
        p = self.root / key
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(doc.model_dump_json(indent=2), encoding="utf-8")
        return key

    def put_image(self, doc_id: str, image: RecoveredImage) -> str:
        if not image.blob:
            return image.storage_ref or ""
        # content-addressed by the image blob sha256: deterministic, idempotent,
        # and never orphaned by run history (ADR #8).
        checksum = image.checksum or hashlib.sha256(image.blob).hexdigest()
        key = f"images/{doc_id}/{checksum}.{_img_ext(image.mime)}"
        p = self.root / key
        if not p.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(image.blob)
        return key

    def get(self, key: str) -> bytes | None:
        p = self.root / key
        return p.read_bytes() if p.exists() else None


def _img_ext(mime: str) -> str:
    return {"image/png": "png", "image/jpeg": "jpg", "image/tiff": "tiff", "image/gif": "gif"}.get(mime, "bin")


def _version_suffix(version: str) -> str:
    """Strip the role prefix so keys read dom-v0.1.0 / norm-v0.1.0.

    parser_version "parser-v0.1.0" -> "v0.1.0"; "unknown" stays as-is.
    """
    return version.split("-", 1)[-1] if version and "-" in version else version