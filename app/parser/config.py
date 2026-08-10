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

    # ADR-011: the routing config snapshot used when layout_backend == "auto".
    # None => factory defaults (RoutingConfig()). Also gates routing on/off.
    routing: Optional["RoutingConfig"] = None

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