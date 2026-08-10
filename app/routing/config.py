"""Immutable routing configuration snapshot (spec §6, §17).

Every calibration value lives HERE, not in code — weights, band tiers, and the
low-confidence fallback thresholds are config, so tuning them is a config
change, not a code change (ADR-011 challenge: values below are INITIAL GUESSES
to be calibrated against the `_cli_out` verification corpus).
"""
from __future__ import annotations

from dataclasses import dataclass, field

# signal_name -> weight. Zero = informational-only (metadata), never drives the
# score. Every `Signal.name` a detector ever emits MUST be present here (additive,
# Gap B); an unknown signal is warned + skipped, never a crash.
_DEFAULT_WEIGHTS: dict[str, float] = {
    # --- complexity-driving signals (value 0..1; higher = more complex) ---
    # Calibrated 2026-08-10 against the real test_cases corpus. ABSOLUTE weighted
    # sum (0-100) -> these weights ARE the band map:
    #   * scan cluster (scanned/ocr/low_text/low_char/image) totals ~58 maxed ->
    #     a purely-scanned doc lands in ENRICHMENT (OCR), per spec §5.
    #   * font_diversity, reading_order, multi_column, tables drive the Docling
    #     tier for genuinely-structured layout.
    # NOTE: layout_complexity / block_fragmentation are kept LIGHT because they
    # are computed from raw page geometry and over-flag clean multi-paragraph
    # text (a 30-paragraph single-column doc measures ~0.75). Heavier weighting
    # would over-route simple docs. Tracked follow-up (ADR-011): refine these
    # (and reading-order) detectors so complex-academic docs reliably reach the
    # DOCLING band without over-flagging simple text. Re-tune here as the
    # corpus grows.
    "metric_scanned_page_probability": 15.0,
    "metric_low_char_density": 8.0,
    "metric_ocr_required": 18.0,
    "metric_low_text_ratio": 12.0,
    "metric_image_density": 5.0,
    "metric_table_probability": 12.0,
    "metric_form_probability": 4.0,
    "metric_font_diversity": 25.0,
    "metric_multi_column_probability": 15.0,
    "metric_reading_order_ambiguity": 25.0,
    "metric_layout_complexity": 20.0,
    "metric_block_fragmentation": 0.0,
    # --- informational / zero-weight signals (must exist for sign coverage) ---
    "metric_foundation_meta_available": 0.0,
    "metric_pdf_version": 0.0,
    "metric_has_outline": 0.0,
    "metric_has_tag": 0.0,
    "metric_encrypted": 0.0,
    "metric_producer_present": 0.0,
    "metric_creator_present": 0.0,
    "metric_total_char_count": 0.0,
    "metric_char_per_page": 0.0,
    "metric_text_ratio": 0.0,
    "metric_page_char_density_none": 0.0,
    "metric_image_count": 0.0,
    "metric_full_image_page_count": 0.0,
    "metric_images_per_page": 0.0,
    "metric_table_present": 0.0,
    "metric_font_embedded": 0.0,
    "metric_unusual_font": 0.0,
    "metric_text_extraction_confidence": 0.0,
    # failure markers (Gap B: never a negative, thus never a weight)
    "metric_detector_failed": 0.0,
}


@dataclass(frozen=True)
class RoutingConfig:
    """Immutable per-routing configuration. Frozen => determinism (§12)."""

    router_version: str = "router-v0.1.0"
    scoring_version: str = "scoring-v0.1.0"
    policy_version: str = "policy-v0.1.0"

    # (lo, hi, band) contiguous tiers covering 0..100 (validation test).
    bands: tuple[tuple[int, int, str], ...] = (
        (0, 30, "native"),
        (31, 60, "enrichment"),
        (61, 100, "docling"),
    )
    weights: dict[str, float] = field(default_factory=lambda: dict(_DEFAULT_WEIGHTS))

    # conservative escalation thresholds (one tier toward complex; §14)
    native_low_conf: float = 0.50
    enrichment_low_conf: float = 0.35
    docling_low_conf: float = 0.0      # top tier never escalates (bounded)

    inspection_engine: str = "pymupdf"
    max_signals: int = 512
    max_bytes: int = 512 * 1024 * 1024

    @property
    def band_names(self) -> dict[str, tuple[int, int]]:
        """band-name -> (lo, hi), e.g. {"native": (0,30), ...} for audit."""
        return {name: (lo, hi) for lo, hi, name in self.bands}

    def low_conf_threshold(self, band: str) -> float:
        return {
            "native": self.native_low_conf,
            "enrichment": self.enrichment_low_conf,
            "docling": self.docling_low_conf,
        }.get(band, 0.5)

    def snapshot(self) -> dict:
        """A JSON-safe fingerprint for determinism provenance (matches
        `ParserConfig.snapshot()` style)."""
        return {
            "router_version": self.router_version,
            "scoring_version": self.scoring_version,
            "policy_version": self.policy_version,
            "bands": [list(b) for b in self.bands],
            "weights": dict(self.weights),
            "native_low_conf": self.native_low_conf,
            "enrichment_low_conf": self.enrichment_low_conf,
            "docling_low_conf": self.docling_low_conf,
            "inspection_engine": self.inspection_engine,
            "max_signals": self.max_signals,
        }