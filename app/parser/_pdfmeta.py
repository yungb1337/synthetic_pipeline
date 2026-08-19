"""Extract the PDF info dict (title/author/subject/...) from a fitz document.

This preserves the provenance the legacy native loader carried into the DOM,
so the page-centric path behaves like the original `Extractor` for PDFs.
"""
from __future__ import annotations

from typing import Any


def fitz_metadata(doc: "Any") -> dict:
    """Return a flat dict of the PDF metadata / info dictionary.

    Returns an empty dict when the document has no metadata or when the access
    raises (never crash the scan over a malformed info dict).
    """
    out: dict = {}
    try:
        meta = doc.metadata or {}
    except Exception:
        return out
    for key in ("title", "author", "subject", "creator", "producer",
                "creationDate", "modDate", "keywords"):
        val = meta.get(key)
        if val is None:
            continue
        # Normalize the legacy date keys to `created` / `modified` so the
        # RecoveredDocument attribute names line up with DocumentBuilder.build.
        if key == "creationDate":
            out["created"] = val
        elif key == "modDate":
            out["modified"] = val
        else:
            out[key] = val
    # Also surface the natural language only when present.
    lang = meta.get("language")
    if lang:
        out["language"] = lang
    return out
