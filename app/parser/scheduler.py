"""Scheduler + ResourceGovernor (ADR-013 T12).

`ResourceGovernor` derives `heavy_concurrency` from measured RAM (and an
optional measured per-engine footprint `F`), never a fixed cap — "scale by
hardware". `Scheduler` decouples a wide `native_pool` (ThreadPoolExecutor: PyMuPDF
/ enrichment / image / simple — GIL-releasing, cheap) from a bounded
`heavy_pool` (ProcessPoolExecutor for Docling). The heavy engine is built INSIDE
each worker (initializer sets `OMP_NUM_THREADS=1` / `MKL_NUM_THREADS=1` etc.) so
the BLAS-thread multiplier is neutralized and the engine is reused per process
(no N× warm-up). Backpressure + per-page persistence + exception containment
mean one crashing heavy page becomes `FAILED`, never a whole-run crash.
"""
from __future__ import annotations

import multiprocessing as mp
import os
import traceback
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from dataclasses import dataclass

from .config import ParserConfig
from .engines.base import (
    DOCLING,
    ENRICHMENT,
    IMAGE,
    NATIVE,
    SIMPLE,
    PageWorkItem,
)
from .page_result import PageResult, PageStatus
from .storage_pages import Ledger, PageStore

HEADROOM = 0.80  # reserve OS + native pool + orchestrator


# --- module-level heavy worker (picklable on Windows spawn) -----------------
# C1: the F footprint probe is performed INSIDE the heavy worker (never in the
# orchestrator). The measured value is published to a shared multiprocessing.Value
# so the orchestrator's governor can refine `heavy_concurrency` on the fly.
_heavy_f_value = None  # set when the pool is created (mp.Value)


def _heavy_initializer(models_dir: str):
    os.environ["OMP_NUM_THREADS"] = "1"
    os.environ["MKL_NUM_THREADS"] = "1"
    os.environ["TORCHDYNAMO_DISABLE"] = "1"
    os.environ["PYTORCH_CUDA_ALLOC_CONF"] = "expandable_segments:True"
    if models_dir:
        os.environ.setdefault("DOCLING_MODELS_PATH", models_dir)
    # Warm the engine + mark available (defensive; never crash the pool).
    try:
        from .loaders import docling_loader

        if docling_loader.engine_available():
            # Run the F probe once per worker and publish it to the shared Value
            # so the governor can re-derive a tighter concurrency.
            if _heavy_f_value is not None:
                try:
                    f = ResourceGovernor().measure_footprint()
                    if f:
                        with _heavy_f_value.get_lock():
                            _heavy_f_value.value = f
                except Exception:
                    pass
    except Exception:
        pass


def _run_heavy(item: PageWorkItem, config: ParserConfig) -> PageResult:
    from .engines.heavy_docling import HeavyDoclingEngine

    try:
        return HeavyDoclingEngine(config).process(item)
    except Exception as e:  # pragma: no cover - defensive containment
        return PageResult(
            doc_id=item.doc_id, page_index=item.page_index, route=DOCLING,
            status=PageStatus.FAILED,
            errors=[{"page_no": item.page_index + 1, "category": "heavy_worker",
                     "message": f"{type(e).__name__}: {e}", "traceback": traceback.format_exc()}],
            source_hash=item.source_hash,
        )


def _run_native(item: PageWorkItem, band: str, config: ParserConfig) -> PageResult:
    from .engines.enrichment import EnrichmentEngine
    from .engines.image import ImageEngine
    from .engines.native_pdf import NativePdfEngine
    from .engines.simple import SimpleEngine

    try:
        if band == ENRICHMENT:
            return EnrichmentEngine(config).process(item)
        if band == IMAGE:
            return ImageEngine(config).process(item)
        if band == SIMPLE:
            return SimpleEngine(config).process(item)
        return NativePdfEngine(config).process(item)
    except Exception as e:
        return PageResult(
            doc_id=item.doc_id, page_index=item.page_index, route=band,
            status=PageStatus.FAILED,
            errors=[{"page_no": item.page_index + 1, "category": "native_worker",
                     "message": f"{type(e).__name__}: {e}", "traceback": traceback.format_exc()}],
            source_hash=item.source_hash,
        )


def _resolve_band(item: PageWorkItem) -> str:
    return item.route or NATIVE


@dataclass
class ResourceGovernor:
    """Derive heavy concurrency from measured RAM / GPU (Fact + Recommendation)."""

    config: ParserConfig | None = None
    # A measured per-engine footprint in bytes; None => auto/probe or unknown.
    measured_f: float | None = None
    _heavy_concurrency: int = 1

    # --- measured footprint (cold-start probe) -------------------------------
    def measure_footprint(self) -> float | None:
        """Measure per-engine RAM `F` by warming the engine and converting two
        representative pages. Returns bytes or None when Docling is unavailable
        or psutil is missing (caller then uses concurrency 1)."""
        if self.measured_f is not None:
            return self.measured_f
        try:
            import fitz  # type: ignore
            from .loaders import docling_loader
        except Exception:
            return None
        if not docling_loader.engine_available() or not docling_loader.docling_guard():
            return None
        try:
            import psutil  # type: ignore
        except Exception:
            return None

        # Build a small + a large synthetic PDF for the probe.
        try:
            import tempfile
            small = fitz.open()
            p = small.new_page(width=595, height=842)
            p.insert_text((72, 100), "Probe page small content.", fontsize=11)
            small_b = small.tobytes()
            large = fitz.open()
            for _ in range(3):
                pg = large.new_page(width=1190, height=1684)
                for i in range(60):
                    pg.insert_text((72, 80 + i * 18), "Probe row %d with some moderately long text to occupy memory." % i, fontsize=10)
            large_b = large.tobytes()

            peak = 0
            base = psutil.Process().memory_info().rss
            for blob in (small_b, large_b):
                tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
                tmp.write(blob)
                tmp.close()
                res = docling_loader.convert_path(tmp.name, 0)
                try:
                    peak = max(peak, psutil.Process().memory_info().rss - base)
                except Exception:
                    pass
                try:
                    os.unlink(tmp.name)
                except Exception:
                    pass
            # account both in-flight peak + retained model weights (the engine
            # stays resident for the process lifetime). C3: this must NOT
            # double-count the engine delta — take the MAX, not the SUM.
            self.measured_f = max(peak, psutil.Process().memory_info().rss - base)
            return self.measured_f
        except Exception:
            return None

    def _cgroup_max(self):
        for v2 in ("/sys/fs/cgroup/memory.max",):
            try:
                v = open(v2).read().strip()
                if v.isdigit():
                    return int(v)
            except Exception:
                pass
        for v1 in ("/sys/fs/cgroup/memory/memory.limit_in_bytes",):
            try:
                v = open(v1).read().strip()
                if v.isdigit():
                    return int(v)
            except Exception:
                pass
        return None

    def derive_heavy_concurrency(self, ram_cap: float | None = None,
                                 base_overhead: float | None = None,
                                 F: float | None = None) -> int:
        """Formula: max(1, floor((usable - base_overhead) / F)).

        `usable = min(ram_cap, cgroup_max) * HEADROOM`. When `F is None`
        (Docling absent / unmeasured) the safe floor of 1 is returned.
        """
        try:
            import psutil  # type: ignore
        except Exception:
            psutil = None

        ram_total = ram_cap if ram_cap is not None else (
            psutil.virtual_memory().total if psutil else 16 * 1024**3)
        cgroup = self._cgroup_max()
        cap = min(ram_total, cgroup) if cgroup else ram_total
        usable = cap * HEADROOM

        if F is None:
            return 1
        if F <= 0:
            return 1

        overhead = base_overhead if base_overhead is not None else (2 * 1024**3)
        n = int((usable - overhead) // F)
        return max(1, n)

    def periodic_recheck(self, current: int) -> int:
        """Re-derive from available RAM; only DOWNWARD adjustments are applied
        immediately (never spawn mid-flight upward surges). Returns the (possibly
        lowered) concurrency."""
        try:
            import psutil  # type: ignore
        except Exception:
            return current
        cgroup = self._cgroup_max()
        cap = min(psutil.virtual_memory().available, cgroup) if cgroup else psutil.virtual_memory().available
        usable = cap * HEADROOM
        if self.measured_f and self.measured_f > 0:
            overhead = 2 * 1024**3
            n = max(1, int((usable - overhead) // self.measured_f))
            self._heavy_concurrency = min(current, n)
        return self._heavy_concurrency


class Scheduler:
    """One shared instance per process; holds the pools for the process lifetime."""

    def __init__(self, config: ParserConfig,
                 native_concurrency: int | None = None,
                 heavy_concurrency: int | None = None,
                 page_store: PageStore | None = None,
                 ledger: Ledger | None = None,
                 prefer_in_process_heavy: bool = False):
        self.config = config
        self.page_store = page_store
        self.ledger = ledger
        self.prefer_in_process_heavy = prefer_in_process_heavy

        self.native_concurrency = native_concurrency or min(32, ((mp.cpu_count() or 4) * 2))
        self.native_pool = ThreadPoolExecutor(max_workers=self.native_concurrency)

        # C1: do NOT build/warm the Docling engine in the orchestrator process.
        # The F footprint probe is expensive (multi-hundred-MB load + seconds)
        # and is meaningless for native-only / non-PDF runs. Instead we lazily
        # derive `heavy_concurrency` from RAM only (F=None => safe floor of 1),
        # and run the real F probe inside the heavy worker (via a
        # multiprocessing.Value, see _heavy_initializer) the first time a
        # docling page is actually submitted. The single-doc
        # `prefer_in_process_heavy=True` path may still warm locally and reuse.
        self.governor = ResourceGovernor(config=config)
        self._f_probe_done = False
        if heavy_concurrency is not None:
            self.heavy_concurrency = heavy_concurrency
        else:
            # RAM-only derivation; F probe deferred to the worker.
            self.heavy_concurrency = self.governor.derive_heavy_concurrency(F=None)

        # Shared probe handle (worker-published F). None until first docling job.
        self._mp_f_value = None

        self._heavy_pool = None
        # Lazily created on first heavy submit (and only if not in-process).

    def _get_heavy_pool(self) -> ProcessPoolExecutor:
        if self._heavy_pool is None:
            ctx = mp.get_context("spawn")
            # C1: create the shared F probe value (worker-published) before the
            # pool starts, so the initializer can write to it.
            self._mp_f_value = mp.Value("d", 0.0)
            global _heavy_f_value
            _heavy_f_value = self._mp_f_value
            self._heavy_pool = ProcessPoolExecutor(
                max_workers=self.heavy_concurrency,
                mp_context=ctx,
                initializer=_heavy_initializer,
                initargs=(self.config.docling_models_dir,),
            )
        return self._heavy_pool

    def run_plan(self, plan, prefer_in_process_heavy: bool | None = None) -> list[PageResult]:
        """Execute every `PageWorkItem`; persist each result; return all results.

        Native/enrichment/image/simple run in `native_pool`. Docling runs in the
        bounded `heavy_pool` (or in-process when forced). As each future
        completes we persist the `PageResult` to the page store + ledger and
        contain any exception into a `FAILED` result.
        """
        in_process = self.prefer_in_process_heavy if prefer_in_process_heavy is None else prefer_in_process_heavy

        futures = []
        for item in plan.work_items:
            band = _resolve_band(item)
            if band == DOCLING:
                # Docling ALWAYS runs via HeavyDoclingEngine (the per-page,
                # worker-built engine). When in-process it still executes in the
                # calling process (no ProcessPool fork); otherwise it runs in the
                # bounded heavy pool. Never fall through to _run_native (which only
                # handles native/enrichment/image/simple).
                if in_process:
                    fut = self.native_pool.submit(_run_heavy, item, self.config)
                else:
                    fut = self._get_heavy_pool().submit(_run_heavy, item, self.config)
            else:
                fut = self.native_pool.submit(_run_native, item, band, self.config)
            futures.append((item, fut))

        results: list[PageResult] = []
        by_fut = {fut: item for item, fut in futures}
        completed = 0
        for fut in as_completed(list(by_fut)):
            res = self._collect(by_fut[fut], fut)
            results.append(res)
            # C2: periodically recheck available RAM and downsize the heavy
            # concurrency (never upward mid-flight). Applied to the in-memory
            # governor + the live pool's max_workers where supported.
            completed += 1
            if not in_process and completed % 4 == 0:
                try:
                    f = None
                    if self._mp_f_value is not None and self._mp_f_value.value > 0:
                        f = self._mp_f_value.value
                    self.governor.measured_f = f
                    new_c = self.governor.periodic_recheck(self.heavy_concurrency)
                    if new_c < self.heavy_concurrency:
                        self.heavy_concurrency = new_c
                        try:
                            self._heavy_pool._max_workers = new_c  # bounded downward
                        except Exception:
                            pass
                except Exception:
                    pass

        # preserve page order for downstream assembly
        results.sort(key=lambda r: r.page_index)
        return results

    def _collect(self, item: PageWorkItem, fut) -> PageResult:
        try:
            res = fut.result()
        except Exception as e:  # unhandled in worker -> contained FAILED
            res = PageResult(
                doc_id=item.doc_id, page_index=item.page_index, route=item.route,
                status=PageStatus.FAILED,
                errors=[{"page_no": item.page_index + 1, "category": "scheduler",
                         "message": f"{type(e).__name__}: {e}", "traceback": traceback.format_exc()}],
                source_hash=item.source_hash,
            )
        if self.page_store is not None and self.ledger is not None:
            try:
                self.page_store.put_page(item.doc_id, item.page_index, res)
                self.ledger.update_page(
                    item.doc_id, item.page_index, res.status, res.checksum,
                    res.engine_version or res.docling_version, 1, res.errors,
                )
            except Exception:
                pass
        return res

    def close(self) -> None:
        try:
            self.native_pool.shutdown(wait=True)
        except Exception:
            pass
        if self._heavy_pool is not None:
            try:
                self._heavy_pool.shutdown(wait=True)
            except Exception:
                pass
            self._heavy_pool = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
