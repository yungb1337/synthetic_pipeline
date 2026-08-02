"""File-type detection.

Strategy hierarchy (per docs/parser-module-spec §2):
  1. magic bytes        (primary, deterministic)
  2. container probe    (ZIP-backed: docx/xlsx/pptx/epub)
  3. content sniff      (ambiguous text: json/csv/tsv/md/html/plaintext)
  4. extension          (last, tie-breaker / "declared" only — never trusted)

Disagreement policy: return the strongest probe's result; keep an explicit
`unresolved` state rather than guessing. `declared_extension` is always
carried for lineage + security (e.g. MIME-smuggling detection).
"""
from __future__ import annotations

import io
import json
import zipfile

from dataclasses import dataclass


@dataclass(frozen=True)
class Detected:
    slug: str
    mime: str
    probe: str
    confidence: float
    declared_extension: str
    unresolved: bool = False


_MIME = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "epub": "application/epub+zip",
    "png": "image/png",
    "jpg": "image/jpeg",
    "gif": "image/gif",
    "tiff": "image/tiff",
    "rtf": "application/rtf",
    "csv": "text/csv",
    "tsv": "text/tab-separated-values",
    "json": "application/json",
    "xml": "application/xml",
    "html": "text/html",
    "markdown": "text/markdown",
    "plaintext": "text/plain",
    "zip": "application/zip",
    "riff": "application/octet-stream",
    "unknown": "application/octet-stream",
}

_MAGIC = (
    (b"%PDF-", "pdf", "application/pdf"),
    (b"\x89PNG\r\n\x1a\n", "png", "image/png"),
    (b"\xff\xd8\xff", "jpg", "image/jpeg"),
    (b"GIF87a", "gif", "image/gif"),
    (b"GIF89a", "gif", "image/gif"),
    (b"II*\x00", "tiff", "image/tiff"),
    (b"MM\x00*", "tiff", "image/tiff"),
    (b"{\\rtf", "rtf", "application/rtf"),
    (b"RIFF", "riff", "application/octet-stream"),
)

_ZIP_HEADERS = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")

_CONTAINER = {
    "word/": ("docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    "xl/": ("xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    "ppt/": ("pptx", "application/vnd.openxmlformats-officedocument.presentationml.presentation"),
    "META-INF/": ("epub", "application/epub+zip"),
}


def _declared_extension(filename: str) -> str:
    if not filename:
        return ""
    base = filename.replace("\\", "/").split("/")[-1]
    return base.rsplit(".", 1)[-1].lower() if "." in base else ""


def _probe_zip(data: bytes) -> Detected | None:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = zf.namelist()
    except Exception:
        return None
    for prefix, (slug, mime) in _CONTAINER.items():
        if any(n.startswith(prefix) for n in names):
            return Detected(slug, mime, "container", 0.92, "")
    return Detected("zip", _MIME["zip"], "container", 0.7, "")


def _deduce_delimiter(text: str) -> str | None:
    lines = [l for l in text.splitlines() if l.strip()][:12]
    if not lines:
        return None
    for delim in ("\t", ","):
        counts = [l.count(delim) for l in lines]
        present = [c for c in counts if c > 0]
        if len(present) == len(lines) and max(present) == min(present):
            return delim
    return None


def _sniff_text(data: bytes) -> Detected:
    head = data[:8192]
    try:
        text = head.decode("utf-8-sig", errors="ignore")
    except Exception:
        text = head.decode("latin-1", errors="ignore")
    stripped = text.lstrip()

    if stripped.startswith("<?xml"):
        return Detected("xml", _MIME["xml"], "sniff", 0.92, "")
    low = stripped[:256].lower()
    if low.startswith(("<!doctype", "<html", "<head")):
        return Detected("html", _MIME["html"], "sniff", 0.95, "")
    try:
        json.loads(text)
        return Detected("json", _MIME["json"], "sniff", 0.97, "")
    except Exception:
        pass
    if any(stripped.startswith(m) for m in ("# ", "## ", "### ", "```", "* ", "- ")):
        return Detected("markdown", _MIME["markdown"], "sniff", 0.8, "")
    delim = _deduce_delimiter(text)
    if delim == "\t":
        return Detected("tsv", _MIME["tsv"], "sniff", 0.9, "")
    if delim == ",":
        return Detected("csv", _MIME["csv"], "sniff", 0.88, "")
    return Detected("plaintext", _MIME["plaintext"], "sniff", 0.55, "")


def _is_text(data: bytes) -> bool:
    return b"\x00" not in data[:4096]


def detect(data: bytes, filename: str = "") -> Detected:
    declared = _declared_extension(filename)
    if not data:
        return Detected("unknown", _MIME["unknown"], "empty", 0.0, declared, unresolved=True)

    for sig, slug, mime in _MAGIC:
        if data.startswith(sig):
            return Detected(slug, mime, "magic", 0.99, declared)

    if data.startswith(_ZIP_HEADERS):
        return _probe_zip(data) or Detected("zip", _MIME["zip"], "container", 0.7, declared)

    if _is_text(data):
        d = _sniff_text(data)
        return Detected(d.slug, d.mime, d.probe, d.confidence, declared, d.unresolved)

    return Detected("unknown", _MIME["unknown"], "unknown", 0.0, declared, unresolved=True)