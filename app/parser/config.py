"""Configuration for the parser module.

Every tunable lives here as a versioned value so projections stay
reproducible (a key principle: the parser must be deterministic given
(bytes, config).)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from app.routing.config import RoutingConfig


@dataclass(frozen=True)
class ParserConfig:
    """Immutable, per-parse configuration. Snapshot it into provenance."""

    parser_version: str = "parser-v0.1.0"
    dom_schema_version: str = "dom-schema-v0.1.0"

    # OCR
    ocr_enabled: bool = True            # scan/image path
    ocr_lang: str = "en"
    ocr_min_confidence: float = 0.0     # keep everything by default; filter later

    # PDF
    pdf_extract_tables: bool = True
    pdf_heading_threshold_ratio: float = 1.12  # font size ratio that marks a heading

    # Layout backend (ADR-007 + ADR-007 amendment): Docling is present but only
    # triggers where layout analysis is required. "auto" = the intelligent router
    # (ADR-011) inspects each doc and dispatches Native / Enrichment / Docling;
    # "native" and "docling" remain valid manual overrides (ADR-007 semantics
    # preserved; only the default flipped).
    layout_backend: str = "auto"
    docling_models_dir: str = "models/docling"  # on-prem model cache
    # Table-structure mode passed to Docling's TableFormer (ADR-013 addendum:
    # extraction-quality run). "FAST" recovers correct logical rows for dense /
    # borderless tables (Tables 1/5/6 in the fixture) where "ACCURATE" collapses
    # them into a single mega-row; "ACCURATE" remains a documented opt-in for
    # rare layouts where FAST over-segments (see module_status known limitation).
    docling_table_mode: str = "FAST"
    # When True, the Docling pipeline runs its built-in OCR stage using
    # RapidOCR/onnxruntime (the SAME engine family as app/parser/ocr.py) with
    # on-demand OcrMode.DEFAULT so text-rich pages are NOT OCR'd. This lets a
    # docling-routed document with scanned pages still recover their text.
    docling_ocr: bool = True

    # ADR-011: the routing config snapshot used when layout_backend == "auto".
    # None => factory defaults (RoutingConfig()). Also gates routing on/off.
    routing: Optional["RoutingConfig"] = None

    # Retry policy (ADR-013 T13): bounded page-level retries in the Assembler.
    # Threaded Extractor -> Assembler; the assembler no longer hardcodes a literal.
    page_retries: int = 2

    # Security / resource limits
    max_file_bytes: int = 512 * 1024 * 1024   # 512 MiB; enforced in Extractor.extract

    temp_dir: str = "work"
    event_sink: str = "console"

    def snapshot(self) -> dict:
        """A JSON-safe fingerprint of this config (for provenance)."""
        out = {k: v for k, v in vars(self).items() if not k.startswith("_") and k != "event_sink"}
        routing = out.get("routing")
        if routing is not None:
            out["routing"] = routing.snapshot()  # JSON-safe fingerprint
        return out


def default_config() -> ParserConfig:
    return ParserConfig()