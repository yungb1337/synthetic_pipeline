"""Deterministic structural and performance metrics for document extraction.
"""
from __future__ import annotations

import collections
import hashlib
import re
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class TextMetrics:
    total_chars: int = 0
    total_words: int = 0
    total_lines: int = 0
    empty_output: bool = True
    repetition_score: float = 0.0
    has_suspicious_repetition: bool = False
    average_confidence: float = 0.0


@dataclass
class PageMetrics:
    expected_pages: int = 0
    processed_pages: int = 0
    missing_pages: int = 0
    pages_with_content: int = 0


@dataclass
class StructuralMetrics:
    total_blocks: int = 0
    total_headings: int = 0
    total_paragraphs: int = 0
    total_tables: int = 0
    total_table_rows: int = 0
    total_table_cells: int = 0
    total_references: int = 0
    reading_order_entries: int = 0


@dataclass
class PerformanceMetrics:
    wall_duration_ms: float = 0.0
    init_duration_ms: float = 0.0
    inference_duration_ms: float = 0.0
    preprocess_duration_ms: float = 0.0
    postprocess_duration_ms: float = 0.0
    pages_per_second: float = 0.0
    chars_per_second: float = 0.0
    peak_ram_mb: float = 0.0
    peak_vram_mb: float = 0.0


@dataclass
class DocumentMetrics:
    document_id: str
    source_hash: str
    text: TextMetrics = field(default_factory=TextMetrics)
    pages: PageMetrics = field(default_factory=PageMetrics)
    structure: StructuralMetrics = field(default_factory=StructuralMetrics)
    performance: PerformanceMetrics = field(default_factory=PerformanceMetrics)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def calculate_repetition_score(text: str, n: int = 4) -> tuple[float, bool]:
    """Calculate the ratio of repeated n-grams in text to detect infinite generation loops."""
    words = re.findall(r"\w+", text.lower())
    if len(words) < 50:
        return 0.0, False
    ngrams = [tuple(words[i : i + n]) for i in range(len(words) - n + 1)]
    if not ngrams:
        return 0.0, False
    counts = collections.Counter(ngrams)
    duplicate_ngrams = sum(c - 1 for c in counts.values() if c > 1)
    repetition_ratio = duplicate_ngrams / len(ngrams)
    # Flag suspicious repetition if repetition ratio is abnormally high or any single 4-gram dominates output
    max_count = max(counts.values()) if counts else 0
    is_suspicious = (len(words) >= 100 and repetition_ratio > 0.45) or (
        max_count > 25 and (max_count / len(ngrams)) > 0.05
    )
    return round(repetition_ratio, 4), is_suspicious


def compute_text_hash(text: str) -> str:
    """Compute normalized text SHA256."""
    normalized = " ".join(text.split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
