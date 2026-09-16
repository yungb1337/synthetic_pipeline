"""Configuration for the experimental Unlimited-OCR evaluation path.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class UnlimitedOCRConfig:
    # Engine & Models
    engine_name: str = "unlimited-ocr-ppocrv6"
    det_model: str = "PP-OCRv6_det_small.onnx"
    cls_model: str = "ch_ppocr_mobile_v2.0_cls_mobile.onnx"
    rec_model: str = "PP-OCRv6_rec_small.onnx"
    use_angle_cls: bool = True
    use_det: bool = True
    use_rec: bool = True

    # Rendering & Preprocessing
    use_gpu: bool = False
    render_dpi: int = 150
    max_image_edge: int = 2000
    min_confidence: float = 0.5

    # Execution & Limits
    timeout_seconds: float = 120.0
    batch_size: int = 1  # sequential execution per ADR / hardware safety
    repetition_ngram_size: int = 4
    repetition_threshold: float = 0.35

    # Storage Paths
    artifacts_dir: Path = field(default_factory=lambda: Path("artifacts") / "unlimited_ocr_eval")
    evaluation_dir: Path = field(default_factory=lambda: Path("evaluation") / "unlimited_ocr")
    report_path: Path = field(default_factory=lambda: Path("docs") / "unlimited-ocr-evaluation.md")

    # LLM Judge Settings (reuses existing repo defaults)
    judge_model: str = "gemini-3.5-flash-lite"
    judge_max_chars: int = 12000
    judge_pacing_seconds: float = 3.0
