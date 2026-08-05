"""Canonical slug -> MIME type map (single source of truth).

Both `detection` (type sniffing) and the format `loaders` (metadata shim)
look up MIME types here, so the mapping lives in exactly one place.
"""
MIME = {
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
