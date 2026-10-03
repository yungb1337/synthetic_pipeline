"""Iteration-2 throughput probes (no parser changes; measurement only).

Probes the heavy-band cost model that explains Run A (F=1) ~ Run B (F=4):
  engine : cold-cache vs warm-cache worker spawn cost (import + converter build)
           + warm per-page convert cost.
  pool   : ProcessPoolExecutor with the scheduler's exact shape
           (initializer=_heavy_initializer-like, max_tasks_per_child=20),
           F=1 vs F=4, measuring per-task convert ms + first-task build ms
           per worker process.
"""

from __future__ import annotations

import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
os.environ.setdefault("TORCHDYNAMO_DISABLE", "1")

PDF = os.path.abspath(
    "checkpoints/run/run-2026-09-04-parser-reliability/sources/pdf/PMC10121009.pdf"
)


def _worker_init():
    t0 = time.perf_counter()
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    os.environ.setdefault("MKL_NUM_THREADS", "1")
    os.environ.setdefault("TORCHDYNAMO_DISABLE", "1")
    from app.parser.loaders import docling_loader

    ok = docling_loader.engine_available()
    dt = time.perf_counter() - t0
    _worker_init.first_build_s = dt  # type: ignore[attr-defined]
    _worker_init.ok = ok  # type: ignore[attr-defined]


def _convert_task(page: int):
    t0 = time.perf_counter()
    from app.parser.loaders import docling_loader

    engine = docling_loader.get_engine()
    r = engine.convert(PDF, page_range=(page + 1, page + 1))
    ms = (time.perf_counter() - t0) * 1000
    status = str(getattr(getattr(r, "status", None), "name", "?"))
    build_s = float(getattr(_worker_init, "first_build_s", -1.0))
    return (os.getpid(), page, round(ms, 1), status, round(build_s, 2))


def probe_engine() -> None:
    t0 = time.perf_counter()
    from app.parser.loaders import docling_loader

    ok = docling_loader.engine_available()
    t_build = time.perf_counter() - t0
    print(f"[engine] import+engine_available: {t_build:.1f}s ok={ok}")
    for p in (0, 1, 2):
        pid, page, ms, status, _ = _convert_task(p)
        print(f"[engine] convert page {page}: {ms:.0f} ms status={status}")


def probe_pool(workers: int, tasks: int, mtasks: int = 20) -> None:
    from concurrent.futures import ProcessPoolExecutor, as_completed

    pages = [i % 23 for i in range(tasks)]
    t0 = time.perf_counter()
    build_per_pid = {}
    results = []
    ex = ProcessPoolExecutor(
        max_workers=workers,
        initializer=_worker_init,
        max_tasks_per_child=mtasks if mtasks > 0 else None,
    )
    futs = {ex.submit(_convert_task, p): p for p in pages}
    try:
        for fut in as_completed(futs):
            r = fut.result()
            results.append(r)
            pid, page, ms, status, build_s = r
            if build_s >= 0 and pid not in build_per_pid:
                build_per_pid[pid] = build_s
            import psutil

            m = psutil.virtual_memory()
            print(
                f"[pool] task {len(results)}/{tasks} page={page} pid={pid} "
                f"ms={ms:.0f} build_s={build_s:.1f} ram_avail={m.available / 2**30:.1f}GB",
                flush=True,
            )
        if len(results) % 10 == 0:
            print(
                f"[pool] ... {len(results)}/{tasks} elapsed={time.perf_counter() - t0:.0f}s",
                flush=True,
            )
    finally:
        ex.shutdown(wait=False, cancel_futures=True)
    wall = time.perf_counter() - t0
    pids = {r[0] for r in results}
    converts = [r[2] for r in results]
    n_respawns = max(0, len(pids) - 1) if workers == 1 else max(0, len(pids) - workers)
    if converts:
        print(
            f"[pool] F={workers} mtasks={mtasks} tasks={len(results)}/{tasks} "
            f"wall={wall:.1f}s procs={len(pids)} respawns~{n_respawns} "
            f"build_s_per_proc={list(build_per_pid.values())} "
            f"convert_ms mean={sum(converts) / len(converts):.0f} "
            f"min={min(converts):.0f} max={max(converts):.0f}",
            flush=True,
        )


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "engine"
    if mode == "engine":
        probe_engine()
    elif mode == "pool":
        probe_pool(
            int(sys.argv[2]),
            int(sys.argv[3]),
            int(sys.argv[4]) if len(sys.argv) > 4 else 20,
        )
