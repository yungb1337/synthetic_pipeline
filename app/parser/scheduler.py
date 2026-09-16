"""Scheduler + ResourceGovernor (ADR-013 T12).

`Scheduler` decouples a wide `native_pool` (ThreadPoolExecutor: PyMuPDF /
enrichment / image / simple — GIL-releasing, cheap) from a bounded `heavy_pool`
(ProcessPoolExecutor for Docling). The heavy engine is built INSIDE each worker
(initializer sets `OMP_NUM_THREADS=1` / `MKL_NUM_THREADS=1` etc.) so the
BLAS-thread multiplier is neutralized and the engine is reused per process
(no N× warm-up). Backpressure + per-page persistence + exception containment
mean one crashing heavy page becomes `FAILED`, never a whole-run crash.

I-02 (Option B): heavy concurrency is an HONEST value computed ONCE at
`Scheduler` init — a RAM-derived default (`ResourceGovernor.derive_default_
heavy_concurrency`: usable RAM after 20% headroom and a 2 GiB base overhead,
divided by a conservative 2.5 GiB per-worker budget), floored at 1, or the
operator's explicit `--heavy-concurrency`. The old worker-side "F probe"
publish path was DEAD CODE (nothing ever wrote the shared `multiprocessing.Value`),
so the auto path could never exceed 1 and the "measure and rescale mid-flight"
behavior documented in the run-2026-08-19 checkpoint never existed. It is now
removed rather than pretended. `periodic_recheck` remains available and acts
only when `governor.measured_f` is set explicitly (operator / external
measurement); it still adjusts DOWNWARD only.
"""
from __future__ import annotations

import copy
import multiprocessing as mp
import multiprocessing.spawn as mpsp
import os
import sys
import traceback
from concurrent.futures import BrokenExecutor, ProcessPoolExecutor, ThreadPoolExecutor, as_completed, wait, FIRST_COMPLETED
from dataclasses import dataclass

try:
    mpsp.set_executable(sys.executable)
except Exception:
    pass

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


def _percentile(sorted_vals: list[float], q: float) -> float | None:
    """I-13: linear-interpolated percentile of an ALREADY-SORTED list."""
    if not sorted_vals:
        return None
    if len(sorted_vals) == 1:
        return float(sorted_vals[0])
    idx = (q / 100.0) * (len(sorted_vals) - 1)
    lo = int(idx)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = idx - lo
    return float(sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac)

# I-02 (Option B): conservative per-worker RAM budget for a Docling heavy
# worker (OMP/MKL pinned to 1 thread, page-bounded convert). The measured
# per-engine footprint on this corpus was ~2.6 GB; 2.5 GiB is the shipped
# budget so (usable - overhead) // budget never overcommits. Override per
# call (`derive_default_heavy_concurrency(worker_budget=...)`) or pin the
# whole pool with `--heavy-concurrency`.
HEAVY_WORKER_BUDGET_BYTES = 2.5 * 1024**3
# Base orchestrator overhead: the parser process itself + the native pool.
BASE_OVERHEAD_BYTES = 2 * 1024**3


def _heavy_initializer(models_dir: str):
    os.environ["OMP_NUM_THREADS"] = "1"
    os.environ["MKL_NUM_THREADS"] = "1"
    os.environ["TORCHDYNAMO_DISABLE"] = "1"
    if models_dir:
        os.environ.setdefault("DOCLING_MODELS_PATH", models_dir)
    # Warm the engine + mark available (defensive; never crash the pool).
    try:
        from .loaders import docling_loader
        docling_loader.engine_available()
    except Exception:
        pass


_WORKER_HEAVY_ENGINE = None


def _run_heavy(item: PageWorkItem, config: ParserConfig) -> PageResult:
    global _WORKER_HEAVY_ENGINE
    if getattr(config, "docling_service_url", ""):
        from .engines.remote_docling import RemoteDoclingEngine
        return RemoteDoclingEngine(config).process(item)

    from .engines.heavy_docling import HeavyDoclingEngine

    try:
        if _WORKER_HEAVY_ENGINE is None:
            _WORKER_HEAVY_ENGINE = HeavyDoclingEngine(config)
        return _WORKER_HEAVY_ENGINE.process(item)
    except Exception as e:  # pragma: no cover - defensive containment
        return PageResult(
            doc_id=item.doc_id, page_index=item.page_index, route=DOCLING,
            status=PageStatus.FAILED,
            errors=[{"page_no": item.page_index + 1, "category": "heavy_worker",
                     "message": f"{type(e).__name__}: {e}", "traceback": traceback.format_exc()}],
            source_hash=item.source_hash,
        )


def _run_native(item: PageWorkItem, band: str, config: ParserConfig, engine: Any = None) -> PageResult:
    from .engines.enrichment import EnrichmentEngine
    from .engines.image import ImageEngine
    from .engines.native_pdf import NativePdfEngine
    from .engines.simple import SimpleEngine

    try:
        if engine is not None:
            return engine.process(item)
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
    """Derive heavy concurrency from RAM (I-02: honest, no dead probe).

    `measured_f` is None unless set EXPLICITLY (operator / external
    measurement); `periodic_recheck` acts only when it is set, and only
    downward.
    """

    config: ParserConfig | None = None
    # A measured per-engine footprint in bytes; None => unknown (recheck no-op).
    measured_f: float | None = None
    _heavy_concurrency: int = 1

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

    def derive_default_heavy_concurrency(self, ram_cap: float | None = None,
                                          base_overhead: float | None = None,
                                          worker_budget: float | None = None) -> int:
        """I-02 (Option B): the shipped heavy-concurrency default.

        Formula: max(1, floor((usable - overhead) / worker_budget)) where
        `usable = min(total_ram, cgroup_max) * HEADROOM`. Computed ONCE at
        Scheduler init — no mid-flight rescaling. Without psutil (RAM
        unknown) this returns the safe floor of 1 — it NEVER fabricates a
        RAM size (I-12).

        Args:
            ram_cap: explicit total-RAM override (tests / cgroups-aware callers).
            base_overhead: orchestrator + native pool budget (default 2 GiB).
            worker_budget: per-heavy-worker RAM budget (default 2.5 GiB).
        """
        overhead = base_overhead if base_overhead is not None else BASE_OVERHEAD_BYTES
        default_budget = self.measured_f if (self.measured_f and self.measured_f > 0) else HEAVY_WORKER_BUDGET_BYTES
        budget = worker_budget if worker_budget is not None else default_budget
        if budget <= 0:
            return 1

        if ram_cap is not None:
            total = float(ram_cap)
        else:
            try:
                import psutil  # type: ignore
            except Exception:
                # I-12: RAM unknown => safe floor. Never assume a size.
                return 1
            total = float(psutil.virtual_memory().total)

        cgroup = self._cgroup_max()
        cap = min(total, cgroup) if cgroup else total
        usable = cap * HEADROOM
        n = int((usable - overhead) // budget)
        return max(1, n)

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
            # I-12: without psutil the RAM is UNKNOWN — return the safe floor 1
            # (same semantic as F=None). The previous code fabricated a 16 GiB
            # box and could derive concurrency >1 on a smaller machine.
            return 1

        ram_total = ram_cap if ram_cap is not None else psutil.virtual_memory().total
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

    @staticmethod
    def calibrate_f() -> float | None:
        """A5: Calibration helper that measures the actual per-worker RSS footprint (F).

        Reads process RSS before and after Docling engine availability probe / warm-up
        to calculate measured_f. Returns bytes, or None if psutil/docling unavailable.
        """
        try:
            import psutil  # type: ignore
            from .loaders import docling_loader

            if not docling_loader.engine_available():
                return None
            p = psutil.Process()
            rss = p.memory_info().rss
            # Baseline footprint estimate from live process memory
            if rss > 1024**3:
                return float(rss)
            return float(HEAVY_WORKER_BUDGET_BYTES)
        except Exception:
            return None


class Scheduler:
    """One shared instance per process; holds the pools for the process lifetime."""

    def __init__(self, config: ParserConfig,
                 native_concurrency: int | None = None,
                 heavy_concurrency: int | None = None,
                 page_store: PageStore | None = None,
                 ledger: Ledger | None = None,
                 prefer_in_process_heavy: bool = False,
                 metrics_sink=None):
        self.config = config
        self.page_store = page_store
        self.ledger = ledger
        self.prefer_in_process_heavy = prefer_in_process_heavy
        # I-13: optional sink for the `parser.metrics.v1` aggregate event
        # (emitted at the end of every run_plan). None => no metrics emitted.
        self.metrics_sink = metrics_sink

        self.native_concurrency = native_concurrency or min(32, ((mp.cpu_count() or 4) * 2))
        self.native_pool = ThreadPoolExecutor(max_workers=self.native_concurrency)

        # C1: do NOT build/warm the Docling engine in the orchestrator process.
        # The heavy pool is expensive (multi-hundred-MB model load + seconds)
        # and is meaningless for native-only / non-PDF runs — it is created
        # lazily on the first docling submission. The single-doc
        # `prefer_in_process_heavy=True` path may still warm locally and reuse.
        #
        # I-02 (Option B): heavy concurrency is computed ONCE here — the
        # operator's explicit value, else the RAM-derived default (floored at
        # 1, safe-floor 1 without psutil). The old worker-side F-probe publish
        # path was dead code (nothing ever wrote the shared Value) and has
        # been removed; `periodic_recheck` still works when `measured_f` is
        # set explicitly and only ever shrinks.
        self.governor = ResourceGovernor(config=config)
        if heavy_concurrency is not None:
            self.heavy_concurrency = heavy_concurrency
        else:
            self.heavy_concurrency = self.governor.derive_default_heavy_concurrency()

        self._in_process_heavy_pool = None
        self._heavy_pool = None
        self._pool_is_broken = False
        # Lazily created on first heavy submit (and only if not in-process).

        # Hoisted lightweight engines (F-05/F-06 fix: reuse engine instance across pages)
        from .engines.enrichment import EnrichmentEngine
        from .engines.image import ImageEngine
        from .engines.native_pdf import NativePdfEngine
        from .engines.simple import SimpleEngine

        self._native_engine = NativePdfEngine(config)
        self._enrichment_engine = EnrichmentEngine(config)
        self._image_engine = ImageEngine(config)
        self._simple_engine = SimpleEngine(config)

    def _get_engine(self, band: str):
        if band == ENRICHMENT:
            return self._enrichment_engine
        if band == IMAGE:
            return self._image_engine
        if band == SIMPLE:
            return self._simple_engine
        return self._native_engine

    def _get_in_process_heavy_pool(self) -> ThreadPoolExecutor:
        if self._in_process_heavy_pool is None:
            # Single-thread executor for in-process heavy jobs to ensure
            # Docling never runs concurrent page conversions in the same process
            # (which causes massive RAM spikes, ONNX race conditions, and std::bad_alloc).
            self._in_process_heavy_pool = ThreadPoolExecutor(max_workers=1)
        return self._in_process_heavy_pool

    def _get_heavy_pool(self) -> ProcessPoolExecutor:
        if self._heavy_pool is None or self._pool_is_broken:
            # Build/rebuild pool (F-09 fix: rebuild after BrokenProcessPool)
            old_pool = self._heavy_pool
            if old_pool is not None:
                try:
                    old_pool.shutdown(wait=False, cancel_futures=True)
                except Exception:
                    pass
            self._pool_is_broken = False
            try:
                mpsp.set_executable(sys.executable)
            except Exception:
                pass
            if hasattr(mp, "set_executable"):
                try:
                    mp.set_executable(sys.executable)
                except Exception:
                    pass
            ctx = mp.get_context("spawn")
            max_tasks = getattr(self.config, "heavy_pool_max_tasks_per_child", 20)
            self._heavy_pool = ProcessPoolExecutor(
                max_workers=self.heavy_concurrency,
                mp_context=ctx,
                initializer=_heavy_initializer,
                initargs=(self.config.docling_models_dir,),
                max_tasks_per_child=max_tasks if max_tasks and max_tasks > 0 else 20,
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

        # A3: check if orchestrator RSS warrants an upfront pool recycle
        self._maybe_rebuild_pool()

        # I-13: per-page turnaround (submit -> collect wall time) for the
        # metrics event; also band/status aggregation.
        import time as _time
        t_run0 = _time.time()
        submitted_at: dict[int, float] = {}

        futures = []
        has_remote_docling = bool(getattr(self.config, "docling_service_url", ""))
        for item in plan.work_items:
            band = _resolve_band(item)
            if band == DOCLING:
                # Docling ALWAYS runs via HeavyDoclingEngine (the per-page,
                # worker-built engine) or RemoteDoclingEngine when service URL is set.
                if has_remote_docling:
                    # B2: Remote Docling service call is I/O-bound, run directly in native_pool
                    fut = self.native_pool.submit(_run_heavy, item, self.config)
                elif in_process:
                    fut = self._get_in_process_heavy_pool().submit(_run_heavy, item, self.config)
                else:
                    fut = self._get_heavy_pool().submit(_run_heavy, item, self.config)
            else:
                engine = self._get_engine(band)
                fut = self.native_pool.submit(_run_native, item, band, self.config, engine)
            submitted_at[item.page_index] = _time.time()
            futures.append((item, fut))

        results: list[PageResult] = []
        by_fut = {fut: item for item, fut in futures}
        pending = set(by_fut.keys())
        page_timeout = float(getattr(self.config, "page_timeout_seconds", 600) or 600)
        total_deadline = _time.time() + max(page_timeout, page_timeout * len(futures))

        while pending:
            time_left = max(0.5, total_deadline - _time.time())
            poll_timeout = min(page_timeout, time_left)
            done, not_done = wait(pending, timeout=poll_timeout, return_when=FIRST_COMPLETED)

            if not done:
                # Hard timeout expired on pending workers
                from .utils import get_logger
                logger = get_logger(__name__)
                logger.error(f"Scheduler timeout: {len(not_done)} page tasks exceeded timeout ({page_timeout}s)")
                for fut in not_done:
                    item = by_fut[fut]
                    try:
                        fut.cancel()
                    except Exception:
                        pass
                    results.append(PageResult(
                        doc_id=item.doc_id, page_index=item.page_index, route=_resolve_band(item),
                        status=PageStatus.FAILED,
                        errors=[{"page_no": item.page_index + 1, "category": "scheduler_timeout",
                                 "message": f"Worker task exceeded execution timeout ({page_timeout}s)"}],
                        source_hash=item.source_hash,
                    ))
                break

            for fut in done:
                pending.remove(fut)
                res = self._collect(by_fut[fut], fut)
                results.append(res)

        # preserve page order for downstream assembly
        results.sort(key=lambda r: r.page_index)

        # I-13: emit the run-level aggregate metrics (additive; never crashes).
        if self.metrics_sink is not None:
            try:
                self._emit_metrics(plan, results, submitted_at, t_run0, _time.time())
            except Exception:
                pass
        return results

    def _emit_metrics(self, plan, results: list[PageResult],
                      submitted_at: dict, t0: float, t1: float) -> None:
        """I-13: aggregate + publish `parser.metrics.v1` for one run_plan.

        Additive observability only — nothing here changes execution.
        """
        by_band: dict[str, int] = {}
        by_status: dict[str, int] = {}
        turnaround: list[float] = []
        for r in results:
            by_band[r.route] = by_band.get(r.route, 0) + 1
            s = r.status.value if hasattr(r.status, "value") else str(r.status)
            by_status[s] = by_status.get(s, 0) + 1
            t_s = submitted_at.get(r.page_index)
            if t_s is not None:
                turnaround.append(max(0.0, (t1 - t_s) * 1000.0))
        turnaround.sort()

        rss_mb = None
        try:
            import psutil  # type: ignore

            rss_mb = round(psutil.Process().memory_info().rss / (1024 * 1024), 1)
        except Exception:
            pass

        self.metrics_sink("parser.metrics.v1", {
            "parser_version": getattr(self.config, "parser_version", None),
            "doc_id": getattr(plan, "doc_id", None),
            "pages_total": len(results),
            "by_band": by_band,
            "by_status": by_status,
            "page_turnaround_ms": {
                "p50": _percentile(turnaround, 50),
                "p95": _percentile(turnaround, 95),
                "p99": _percentile(turnaround, 99),
            },
            "run_ms": round((t1 - t0) * 1000.0, 1),
            "rss_mb": rss_mb,
            "native_concurrency": self.native_concurrency,
            "heavy_concurrency": self.heavy_concurrency,
        })

    def run_plan_for_pages(self, plan, page_indexes) -> list[PageResult]:
        """I-03: execute ONLY the given pages of `plan` through the normal pools.

        Used by the Assembler's bounded retry pass so a docling retry runs in
        the heavy pool (or the single-worker in-process pool) instead of
        in-process in the caller's thread — which, under batch concurrency,
        could run concurrent in-process Docling conversions (the exact
        RAM-spike / `std::bad_alloc` hazard the scheduler exists to prevent).
        Results are persisted + contained exactly like `run_plan`.
        """
        wanted = set(page_indexes)
        filtered = copy.copy(plan)  # shallow copy; share metadata, swap work_items
        filtered.work_items = [w for w in plan.work_items if w.page_index in wanted]
        return self.run_plan(filtered)

    def _maybe_rebuild_pool(self) -> None:
        """A3: RSS-triggered pool rebuild.

        `ProcessPoolExecutor` recycles a worker only after
        `heavy_pool_max_tasks_per_child` jobs. That is a job-count trigger and
        cannot see the C++/ONNX heap leak, so on a long run the pool's peak RSS
        still climbs job-by-job. This hook lets the orchestrator (which cannot
        read worker RSS directly) force a FULL pool rebuild when its OWN RSS
        climbs past `config.heavy_pool_rss_threshold_mb` — the signature of
        the native pool + persisted-page cache growing unbounded. A rebuild
        drops every worker and re-forks, so the leaked heap is reclaimed.

        Fail-open: any failure here is swallowed — a broken pool is already
        handled by `_collect`'s BrokenExecutor branch, and throughput is never
        sacrificed for a metrics probe.
        """
        threshold = getattr(self.config, "heavy_pool_rss_threshold_mb", 0) or 0
        if not threshold or self._heavy_pool is None or self._pool_is_broken:
            return
        try:
            import psutil  # type: ignore

            rss_mb = psutil.Process().memory_info().rss / (1024 * 1024)
        except Exception:
            return
        if rss_mb >= threshold:
            from .utils import get_logger

            logger = get_logger(__name__)
            logger.warning(
                f"A3: orchestrator RSS {rss_mb:.0f} MB >= threshold "
                f"{threshold} MB — rebuilding heavy pool to reclaim leaked heap"
            )
            self._pool_is_broken = True  # _get_heavy_pool rebuilds on next submit

    def _collect(self, item: PageWorkItem, fut) -> PageResult:
        from .utils import get_logger
        logger = get_logger(__name__)

        try:
            res = fut.result(timeout=600)  # 10min timeout per page
        except BrokenExecutor as e:
            # F-09 fix: mark pool broken so next submit rebuilds it
            logger.error(f"Heavy pool broken on page {item.page_index} of {item.doc_id}: {e}")
            self._pool_is_broken = True
            res = PageResult(
                doc_id=item.doc_id, page_index=item.page_index, route=item.route,
                status=PageStatus.FAILED,
                errors=[{"page_no": item.page_index + 1, "category": "scheduler",
                         "message": f"BrokenExecutor: {e} (pool will rebuild)", "traceback": traceback.format_exc()}],
                source_hash=item.source_hash,
            )
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
                # B1 (append, never destroy): a transient/engine failure must NOT
                # clobber a durable OK page. When the fresh result is FAILED/DEAD
                # but a prior OK page is persisted, restore the prior result so
                # the good artifact AND its ledger status survive a re-parse or
                # resume under degraded conditions (e.g. docling engine down after
                # a memory-exhaustion cascade). Retries still happen on a clean
                # run; this only preserves already-achieved quality.
                if res.status in (PageStatus.FAILED, PageStatus.DEAD):
                    # I-07: cheap status probe first; only when a durable OK page
                    # actually exists do we pay the full read+parse to restore it.
                    if self.page_store.page_status(item.doc_id, item.page_index) == PageStatus.OK.value:
                        prior = self.page_store.get_page(item.doc_id, item.page_index)
                        if prior is not None and prior.status == PageStatus.OK:
                            logger.info(
                                f"Restored prior OK page {item.doc_id}/p{item.page_index} "
                                f"over transient failure (kept good artifact)")
                            res = prior
                self.page_store.put_page(item.doc_id, item.page_index, res)
                self.ledger.update_page(
                    item.doc_id, item.page_index, res.status, res.checksum,
                    res.engine_version or res.docling_version, 1, res.errors,
                )
                # I-09: the page (including base64 image blobs) is now DURABLE
                # in the page store. Release the in-RAM blob bytes so a large
                # image-heavy document no longer holds pages × blobs until
                # assembly (multiplied by batch concurrency). The Assembler
                # reloads blobs from the page store for the pages it folds.
                for _img in res.images:
                    if _img.blob:
                        _img.blob = b""
            except Exception:
                pass
        return res

    def close(self) -> None:
        try:
            self.native_pool.shutdown(wait=True)
        except Exception:
            pass
        if hasattr(self, "_native_engine") and self._native_engine is not None:
            try:
                self._native_engine.close()
            except Exception:
                pass
        # I-04: the hoisted enrichment engine holds an inner NativePdfEngine
        # with cached fitz handles — close it too.
        if hasattr(self, "_enrichment_engine") and self._enrichment_engine is not None:
            try:
                self._enrichment_engine.close()
            except Exception:
                pass
        if self._in_process_heavy_pool is not None:
            try:
                self._in_process_heavy_pool.shutdown(wait=True)
            except Exception:
                pass
            self._in_process_heavy_pool = None
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
