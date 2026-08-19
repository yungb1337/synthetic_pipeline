"""Source scan — establish the EXPECTED page set BEFORE any paging.

`SourceScan.scan` is the first stage of the page-centric pipeline. It detects
the file type, counts the PDF pages via `fitz` (the ground-truth expected set),
and writes the bytes ONCE to a reusable path under the store's `manifest/`
directory so the heavy engine can read it directly (no per-page temp-file
churn — `convert_path` reads this path). The expected page set is the contract
every later stage validates against (no silent page loss).

This module is additive: it does not touch `Extractor`, `FilesystemStore`, or
the router.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from .detection import detect

if TYPE_CHECKING:
    from .storage import FilesystemStore


# Image slugs handled by the standalone image OCR engine (one page / file).
# Shared single source of truth (hoisted from a duplicated copy in planner.py).
_IMAGE_SLUGS = {"png", "jpg", "jpeg", "gif", "tiff"}


class SourceScanError(Exception):
    """Raised when the source cannot be read to establish the expected page set.

    A PDF that fitz cannot open (corrupt / truncated / not-a-real-PDF) must NOT
    be silently turned into a 0-page success. This is the central anti-silent-loss
    guarantee: a doc with 0 recovered pages and 0 expected pages would otherwise
    pass `is_complete` and be reported `parsed`. Instead we surface the failure
    so the caller reports `failed`/`unsupported` and never a fake success.
    """


@dataclass
class SourceManifest:
    doc_id: str
    source_hash: str
    expected_page_set: list[int]
    page_count: int
    slug: str
    mime: str
    declared_extension: str
    probe: str
    page_sizes: dict = field(default_factory=dict)   # page_index -> (w, h)
    src_path: str = ""
    metadata: dict = field(default_factory=dict)


class SourceScan:
    """Detect + count pages + write ONE reusable source file."""

    @staticmethod
    def scan(data: bytes, filename: str, store: "FilesystemStore") -> SourceManifest:
        detected = detect(data, filename)
        source_hash = hashlib.sha256(data).hexdigest()
        doc_id = f"d-{source_hash[:16]}"

        # Write bytes once to a reusable path.
        ext = detected.declared_extension or detected.slug or "bin"
        manifest_dir = Path(store.root) / "manifest" / doc_id
        manifest_dir.mkdir(parents=True, exist_ok=True)
        src_path = str(manifest_dir / f"src.{ext}")
        with open(src_path, "wb") as fh:
            fh.write(data)

        page_sizes: dict = {}
        metadata: dict = {}
        if detected.slug == "pdf":
            import fitz

            try:
                doc = fitz.open(stream=data, filetype="pdf")
            except Exception as e:
                # Corrupt / unreadable PDF: do NOT swallow into a 0-page success.
                # Surface the failure so the run never reports a fake `parsed`.
                raise SourceScanError(f"fitz open failed: {e}") from e
            try:
                page_count = doc.page_count
                if page_count <= 0:
                    # A real PDF always has >=1 page. 0 pages means we could not
                    # establish a truthful expected set — treat as unreadable.
                    raise SourceScanError("PDF reported 0 pages (unreadable)")
                page_sizes = {
                    i: (float(doc[i].rect.width), float(doc[i].rect.height))
                    for i in range(page_count)
                }
                # Preserve the PDF info dict (title/author/subject/...) so the
                # page-centric path carries the same provenance the legacy
                # native loader did (constraint #2 behaviour preserved).
                from ._pdfmeta import fitz_metadata
                metadata = dict(fitz_metadata(doc))
            finally:
                doc.close()
            expected_page_set = list(range(page_count))
        else:
            page_count = 1
            expected_page_set = [0]

        return SourceManifest(
            doc_id=doc_id,
            source_hash=source_hash,
            expected_page_set=expected_page_set,
            page_count=page_count,
            slug=detected.slug,
            mime=detected.mime,
            declared_extension=detected.declared_extension,
            probe=detected.probe,
            page_sizes=page_sizes,
            src_path=src_path,
            metadata=metadata,
        )