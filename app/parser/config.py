"""Configuration for the parser module.

Every tunable lives here as a versioned value so projections stay
reproducible (a key principle: the parser must be deterministic given
(bytes, config).)
"""
from __future__ import annotations

from dataclasses import dataclass


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

    # Security / resource limits
    max_file_bytes: int = 512 * 1024 * 1024   # 512 MiB
    max_pages_for_ocr: int = 200              # don't blast huge scans through OCR

    temp_dir: str = "work"
    event_sink: str = "console"

    def snapshot(self) -> dict:
        """A JSON-safe fingerprint of this config (for provenance)."""
        return {k: v for k, v in vars(self).items() if not k.startswith("_") and k != "event_sink"}


@dataclass
class ParseLimits:
    """Expressed limits that don't change code, surfaced for monitoring."""
    max_file_bytes: int = 512 * 1024 * 1024
    max_pages_for_ocr: int = 200


def default_config() -> ParserConfig:
    return ParserConfig()