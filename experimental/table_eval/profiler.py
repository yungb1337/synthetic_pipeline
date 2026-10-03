"""Hardware & latency profiler with strict CUDA VRAM and host RAM tracking."""

from __future__ import annotations

import gc
from dataclasses import dataclass
from typing import Any

try:
    import psutil
except ImportError:
    psutil = None

try:
    import torch
except ImportError:
    torch = None


@dataclass
class HardwareProfile:
    """Telemetry captured for one document or stage."""

    total_wall_ms: float = 0.0
    init_ms: float = 0.0
    preprocess_ms: float = 0.0
    table_det_ms: float = 0.0
    tsr_ms: float = 0.0
    ocr_ms: float = 0.0
    cell_match_ms: float = 0.0
    dom_conversion_ms: float = 0.0

    peak_vram_allocated_mb: float = 0.0
    peak_vram_reserved_mb: float = 0.0
    baseline_vram_mb: float = 0.0
    delta_vram_mb: float = 0.0

    peak_ram_mb: float = 0.0
    baseline_ram_mb: float = 0.0

    gpu_name: str = "none"
    cuda_available: bool = False
    vram_safe: bool = True  # True if <= safety threshold (e.g. 3200MB)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_wall_ms": round(self.total_wall_ms, 2),
            "init_ms": round(self.init_ms, 2),
            "preprocess_ms": round(self.preprocess_ms, 2),
            "table_det_ms": round(self.table_det_ms, 2),
            "tsr_ms": round(self.tsr_ms, 2),
            "ocr_ms": round(self.ocr_ms, 2),
            "cell_match_ms": round(self.cell_match_ms, 2),
            "dom_conversion_ms": round(self.dom_conversion_ms, 2),
            "peak_vram_allocated_mb": round(self.peak_vram_allocated_mb, 2),
            "peak_vram_reserved_mb": round(self.peak_vram_reserved_mb, 2),
            "baseline_vram_mb": round(self.baseline_vram_mb, 2),
            "delta_vram_mb": round(self.delta_vram_mb, 2),
            "peak_ram_mb": round(self.peak_ram_mb, 2),
            "gpu_name": self.gpu_name,
            "cuda_available": self.cuda_available,
            "vram_safe": self.vram_safe,
        }


class HardwareProfiler:
    """Manages precise GPU memory and stage latency profiling."""

    def __init__(self, safety_threshold_mb: float = 3200.0):
        self.safety_threshold_mb = safety_threshold_mb
        self.cuda_available = torch is not None and torch.cuda.is_available()
        self.gpu_name = torch.cuda.get_device_name(0) if self.cuda_available else "none"

    def get_ram_mb(self) -> float:
        if psutil is None:
            return 0.0
        try:
            return psutil.Process().memory_info().rss / (1024.0 * 1024.0)
        except Exception:
            return 0.0

    def get_vram_allocated_mb(self) -> float:
        if not self.cuda_available:
            return 0.0
        try:
            return torch.cuda.memory_allocated() / (1024.0 * 1024.0)
        except Exception:
            return 0.0

    def get_vram_reserved_mb(self) -> float:
        if not self.cuda_available:
            return 0.0
        try:
            return torch.cuda.memory_reserved() / (1024.0 * 1024.0)
        except Exception:
            return 0.0

    def reset_gpu_peak(self) -> None:
        if self.cuda_available:
            gc.collect()
            torch.cuda.empty_cache()
            torch.cuda.reset_peak_memory_stats()

    def start_profile(self) -> HardwareProfile:
        self.reset_gpu_peak()
        prof = HardwareProfile(
            cuda_available=self.cuda_available,
            gpu_name=self.gpu_name,
            baseline_ram_mb=self.get_ram_mb(),
            baseline_vram_mb=self.get_vram_allocated_mb(),
        )
        return prof

    def finalize_profile(
        self, prof: HardwareProfile, total_wall_ms: float
    ) -> HardwareProfile:
        prof.total_wall_ms = total_wall_ms
        if self.cuda_available:
            prof.peak_vram_allocated_mb = torch.cuda.max_memory_allocated() / (
                1024.0 * 1024.0
            )
            prof.peak_vram_reserved_mb = torch.cuda.max_memory_reserved() / (
                1024.0 * 1024.0
            )
            prof.delta_vram_mb = max(
                0.0, prof.peak_vram_allocated_mb - prof.baseline_vram_mb
            )
            prof.vram_safe = prof.peak_vram_reserved_mb <= self.safety_threshold_mb
        else:
            prof.vram_safe = True

        prof.peak_ram_mb = max(prof.baseline_ram_mb, self.get_ram_mb())
        return prof
