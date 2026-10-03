"""Extract the PDF info dict (title/author/subject/...) from a fitz document.

This preserves the provenance the legacy native loader carried into the DOM,
so the page-centric path behaves like the original `Extractor` for PDFs.
"""

from __future__ import annotations

import html
import unicodedata
from typing import Any


def clean_meta_string(val: Any) -> str:
    """Clean and normalize metadata string values.

    - Unescapes XML/HTML character entities (e.g. &#x02010;, &amp;, &#39;)
    - Normalizes Unicode to canonical NFKC form
    - Strips leading/trailing whitespace and control characters
    """
    if val is None:
        return ""
    if not isinstance(val, str):
        val = str(val)
    s = html.unescape(val)
    s = unicodedata.normalize("NFKC", s)
    s = "".join(ch for ch in s if ch in ("\n", "\t") or ord(ch) >= 32)
    return s.strip()


def fitz_metadata(doc: Any) -> dict:
    """Return a flat dict of the PDF metadata / info dictionary.

    Returns an empty dict when the document has no metadata or when the access
    raises (never crash the scan over a malformed info dict).
    """
    out: dict = {}
    try:
        meta = doc.metadata or {}
    except Exception:
        return out
    for key in (
        "title",
        "author",
        "subject",
        "creator",
        "producer",
        "creationDate",
        "modDate",
        "keywords",
    ):
        val = meta.get(key)
        if val is None:
            continue
        cleaned = clean_meta_string(val)
        # Normalize the legacy date keys to `created` / `modified` so the
        # RecoveredDocument attribute names line up with DocumentBuilder.build.
        if key == "creationDate":
            out["created"] = cleaned
        elif key == "modDate":
            out["modified"] = cleaned
        else:
            out[key] = cleaned
    # Also surface the natural language only when present.
    lang = meta.get("language")
    if lang:
        out["language"] = clean_meta_string(lang)
    return out
