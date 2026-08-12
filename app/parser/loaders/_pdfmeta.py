"""Shared PDF metadata extraction (fitz/PyMuPDF).

Both the native `_pdf` loader and the Docling backend feed the canonical DOM's
document-level metadata from the PDF info dictionary, so every PDF path exposes
title/author/subject/creator/producer/dates consistently. Docling's own
`DoclingDocument.metadata` is frequently empty for plain PDFs; the PDF info
dict (via PyMuPDF) is the reliable source and is already a hard dependency.
"""
from __future__ import annotations

import re

# PDF date strings look like "D:20230814153012+02'00'" or "D:20230814". We keep
# a readable `YYYY-MM-DD HH:MM:SS` (trailing timezone offset dropped), and fall
# back to the raw value when the shape is unexpected — never fabricated.
_PDFDATE = re.compile(r"^D:(\d{4})(\d{2})?(\d{2})?(\d{2})?(\d{2})?(\d{2})?")


def _iso_date(value) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    m = _PDFDATE.match(value)
    if not m:
        return value
    y, mo, d, h, mi, s = m.groups()
    if not mo:
        return y
    core = f"{y}-{mo}-{d}" if d else f"{y}-{mo}"
    if h:
        core += f" {h}:{mi or '00'}:{s or '00'}"
    return core


def fitz_metadata(doc) -> dict:
    """Map a PyMuPDF document's info dictionary to RecoveredDocument fields.

    Returns a dict with keys: title, author, subject, creator, producer,
    created, modified. Missing/empty values map to ''. The lookup is
    best-effort per key (a broken dict must never break the parse).
    """
    try:
        meta = dict(doc.metadata or {})
    except Exception:
        meta = {}
    return {
        "title": (meta.get("title") or "").strip(),
        "author": (meta.get("author") or "").strip(),
        "subject": (meta.get("subject") or "").strip(),
        "creator": (meta.get("creator") or "").strip(),
        "producer": (meta.get("producer") or "").strip(),
        "created": _iso_date(meta.get("creationDate")),
        "modified": _iso_date(meta.get("modDate")),
    }
