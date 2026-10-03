"""Configuration & permutation registry for the Table + Layout Model Benchmark."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class PermutationSpec:
    """Specification for one candidate permutation."""

    id: str
    name: str
    layout_detector: str
    table_detector: str
    table_structure: str
    ocr_backend: str
    description: str
    requires_gpu: bool = False
    options: dict[str, Any] = field(default_factory=dict)


@dataclass
class BenchmarkConfig:
    """Master benchmark configuration."""

    # Hardware & Performance Constraints
    vram_safety_ceiling_mb: float = 3200.0  # 3.2 GB max on RTX 3050 4GB
    timeout_seconds_per_page: float = 60.0
    render_dpi: int = 150
    concurrency: int = 1  # strict sequential safety

    # Storage & Paths
    artifacts_dir: Path = field(
        default_factory=lambda: Path("artifacts") / "table_eval"
    )
    evaluation_dir: Path = field(
        default_factory=lambda: Path("evaluation") / "table_benchmark"
    )
    report_path: Path = field(
        default_factory=lambda: Path("docs") / "table-layout-benchmark.md"
    )
    reference_corpus_dir: Path = field(
        default_factory=lambda: (
            Path("checkpoints") / "run" / "run-2026-09-03-llm-judge-test" / "sources"
        )
    )

    # LLM Judge Settings
    judge_model: str = "gemini-3.5-flash-lite"
    judge_max_chars: int = 12000
    judge_pacing_seconds: float = 3.0


# Permutations Registry (P000 - P016)
PERMUTATIONS: dict[str, PermutationSpec] = {
    "P000": PermutationSpec(
        id="P000",
        name="docling-heavy-baseline",
        layout_detector="docling_heron",
        table_detector="docling_heron",
        table_structure="docling_tableformer_cpu",
        ocr_backend="docling_native",
        description="Docling full pipeline baseline (CPU TableFormer)",
        requires_gpu=False,
    ),
    "P001": PermutationSpec(
        id="P001",
        name="rapidocr-no-tables",
        layout_detector="none",
        table_detector="none",
        table_structure="none",
        ocr_backend="rapidocr_gpu",
        description="PP-OCRv6 alone without table extraction (previous unlimited-ocr baseline, ~0.28 table score)",
        requires_gpu=True,
    ),
    "P002": PermutationSpec(
        id="P002",
        name="pymupdf-lines-ppocr",
        layout_detector="none",
        table_detector="pymupdf_lines",
        table_structure="pymupdf_lines",
        ocr_backend="rapidocr_gpu",
        description="PyMuPDF line-based table extraction + RapidOCR for paragraph blocks",
        requires_gpu=True,
        options={"strategy": "lines"},
    ),
    "P003": PermutationSpec(
        id="P003",
        name="pymupdf-hybrid-ppocr",
        layout_detector="none",
        table_detector="pymupdf_hybrid",
        table_structure="pymupdf_hybrid",
        ocr_backend="rapidocr_gpu",
        description="PyMuPDF multi-strategy (lines + text columns + whitespace snapping) + RapidOCR",
        requires_gpu=True,
        options={"strategy": "hybrid", "snap_tolerance": 3.0},
    ),
    "P004": PermutationSpec(
        id="P004",
        name="pdfplumber-lines-ppocr",
        layout_detector="none",
        table_detector="pdfplumber_lines",
        table_structure="pdfplumber_lines",
        ocr_backend="rapidocr_gpu",
        description="pdfplumber vector lines & rectangles extractor + RapidOCR",
        requires_gpu=True,
        options={"vertical_strategy": "lines", "horizontal_strategy": "lines"},
    ),
    "P005": PermutationSpec(
        id="P005",
        name="pdfplumber-text-ppocr",
        layout_detector="none",
        table_detector="pdfplumber_text",
        table_structure="pdfplumber_text",
        ocr_backend="rapidocr_gpu",
        description="pdfplumber text-alignment & whitespace extractor + RapidOCR",
        requires_gpu=True,
        options={"vertical_strategy": "text", "horizontal_strategy": "text"},
    ),
    "P006": PermutationSpec(
        id="P006",
        name="camelot-ppocr",
        layout_detector="none",
        table_detector="camelot",
        table_structure="camelot",
        ocr_backend="rapidocr_gpu",
        description="Camelot table extractor (lattice/stream) + RapidOCR",
        requires_gpu=True,
    ),
    "P007": PermutationSpec(
        id="P007",
        name="tatr-fullpage-ppocr",
        layout_detector="none",
        table_detector="tatr_det",
        table_structure="tatr_struct",
        ocr_backend="rapidocr_gpu",
        description="Microsoft Table Transformer (Full-Page Detection + TSR) + RapidOCR cell assignment",
        requires_gpu=True,
        options={"crop_only": False},
    ),
    "P008": PermutationSpec(
        id="P008",
        name="pymupdf-crop-tatr-ppocr",
        layout_detector="none",
        table_detector="pymupdf_det",
        table_structure="tatr_struct_crop",
        ocr_backend="rapidocr_gpu",
        description="Crop-Only TATR: PyMuPDF detects bbox -> TATR TSR strictly on cropped table -> RapidOCR",
        requires_gpu=True,
        options={"crop_only": True},
    ),
    "P009": PermutationSpec(
        id="P009",
        name="heron-crop-tatr-ppocr",
        layout_detector="docling_heron",
        table_detector="docling_heron",
        table_structure="tatr_struct_crop",
        ocr_backend="rapidocr_gpu",
        description="Docling Heron layout detects table -> TATR TSR on crop -> RapidOCR",
        requires_gpu=True,
        options={"crop_only": True},
    ),
    "P010": PermutationSpec(
        id="P010",
        name="pymupdf-crop-slanet-ppocr",
        layout_detector="none",
        table_detector="pymupdf_det",
        table_structure="slanet_struct_crop",
        ocr_backend="rapidocr_gpu",
        description="PyMuPDF table bbox -> SLANet (PP-Structure) on crop -> RapidOCR",
        requires_gpu=True,
        options={"crop_only": True},
    ),
    "P011": PermutationSpec(
        id="P011",
        name="heron-crop-slanet-ppocr",
        layout_detector="docling_heron",
        table_detector="docling_heron",
        table_structure="slanet_struct_crop",
        ocr_backend="rapidocr_gpu",
        description="Docling Heron layout -> SLANet on crop -> RapidOCR",
        requires_gpu=True,
        options={"crop_only": True},
    ),
    "P012": PermutationSpec(
        id="P012",
        name="heron-crop-tf-fast-ppocr",
        layout_detector="docling_heron",
        table_detector="docling_heron",
        table_structure="tf_fast_crop",
        ocr_backend="rapidocr_gpu",
        description="Docling Heron layout -> TableFormer FAST on isolated crop -> RapidOCR",
        requires_gpu=True,
        options={"crop_only": True},
    ),
    "P013": PermutationSpec(
        id="P013",
        name="heron-crop-tf-acc-ppocr",
        layout_detector="docling_heron",
        table_detector="docling_heron",
        table_structure="tf_acc_crop",
        ocr_backend="rapidocr_gpu",
        description="Docling Heron layout -> TableFormer ACCURATE on isolated crop -> RapidOCR",
        requires_gpu=True,
        options={"crop_only": True},
    ),
    "P014": PermutationSpec(
        id="P014",
        name="h1-vector-neural-cascade",
        layout_detector="none",
        table_detector="hybrid_cascade",
        table_structure="hybrid_cascade",
        ocr_backend="rapidocr_gpu",
        description="H1 Cascade: Vector grid lines -> PyMuPDF; Borderless/uncertain -> Neural TSR (TATR/SLANet)",
        requires_gpu=True,
        options={"cascade_mode": "vector_neural"},
    ),
    "P015": PermutationSpec(
        id="P015",
        name="h2-confidence-geometry-cascade",
        layout_detector="none",
        table_detector="confidence_cascade",
        table_structure="confidence_cascade",
        ocr_backend="rapidocr_gpu",
        description="H2 Cascade: Confidence-gated table detection and per-table neural cropping",
        requires_gpu=True,
        options={"cascade_mode": "confidence_gated"},
    ),
    "P016": PermutationSpec(
        id="P016",
        name="h3-triband-complexity-router",
        layout_detector="triband_router",
        table_detector="triband_router",
        table_structure="triband_router",
        ocr_backend="rapidocr_gpu",
        description="H3 Tri-Band Router: Simple text page -> PP-OCR; Medium table -> PyMuPDF/TATR; Ultra -> Docling CPU",
        requires_gpu=True,
        options={"cascade_mode": "triband"},
    ),
}
