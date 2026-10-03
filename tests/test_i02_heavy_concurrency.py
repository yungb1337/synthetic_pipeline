"""I-02 regression tests — honest heavy-concurrency derivation (Option B).

The old auto path was `derive_heavy_concurrency(F=None)` → always 1, propped
up by a worker-side F-probe publish that was dead code (nothing ever wrote
the shared multiprocessing.Value). These tests pin the NEW contract:

1. The default now scales with RAM (a 32 GB box is NOT pinned at 1).
2. Small boxes floor at 1; missing psutil floors at 1 (never fabricated RAM).
3. An explicit `heavy_concurrency` override always wins.
4. The dead probe scaffolding is gone.
5. `periodic_recheck` keeps its downward-only semantic when `measured_f` is
   explicitly set.
"""

from __future__ import annotations

import sys
import types

from app.parser.config import ParserConfig
from app.parser.scheduler import (
    BASE_OVERHEAD_BYTES,
    HEAVY_WORKER_BUDGET_BYTES,
    ResourceGovernor,
    Scheduler,
)


def test_i02_derive_default_scales_with_ram():
    g = ResourceGovernor()
    ram = 32 * 1024**3
    n = g.derive_default_heavy_concurrency(ram_cap=ram)
    expected = max(
        1, int((ram * 0.80 - BASE_OVERHEAD_BYTES) // HEAVY_WORKER_BUDGET_BYTES)
    )
    assert n == expected
    # The regression this test guards: a big box must not be pinned at 1.
    assert n > 1


def test_i02_derive_default_small_box_floors_at_one():
    g = ResourceGovernor()
    # 4 GiB: usable 3.2 GiB - 2 GiB overhead = 1.2 GiB -> floor(0.48) -> 1
    assert g.derive_default_heavy_concurrency(ram_cap=4 * 1024**3) == 1
    # 8 GiB: usable 6.4 - 2 = 4.4 / 2.5 -> floor(1.76) -> 1
    assert g.derive_default_heavy_concurrency(ram_cap=8 * 1024**3) == 1
    # 16 GiB: usable 12.8 - 2 = 10.8 / 2.5 -> floor(4.32) -> 4
    assert g.derive_default_heavy_concurrency(ram_cap=16 * 1024**3) == 4


def test_i02_derive_default_without_psutil_is_safe_floor(monkeypatch):
    g = ResourceGovernor()
    # `import psutil` raises ImportError when sys.modules maps it to None.
    monkeypatch.setitem(sys.modules, "psutil", None)
    # I-12 contract: RAM unknown => 1. Never fabricate a RAM size.
    assert g.derive_default_heavy_concurrency() == 1


def test_i02_scheduler_auto_path_not_pinned_at_one(monkeypatch):
    monkeypatch.setattr(ResourceGovernor, "_cgroup_max", lambda self: None)
    sched = Scheduler(ParserConfig())
    expected = ResourceGovernor().derive_default_heavy_concurrency()
    assert sched.heavy_concurrency == expected
    assert sched.heavy_concurrency >= 1
    sched.close()


def test_i02_explicit_override_wins(monkeypatch):
    monkeypatch.setattr(ResourceGovernor, "_cgroup_max", lambda self: None)
    sched = Scheduler(ParserConfig(), heavy_concurrency=3)
    assert sched.heavy_concurrency == 3
    sched.close()


def test_i02_dead_probe_scaffolding_removed():
    import app.parser.scheduler as m

    assert not hasattr(m, "_heavy_f_value"), (
        "dead module-level probe Value still present"
    )
    gov = ResourceGovernor()
    assert not hasattr(gov, "measure_footprint"), "dead F-probe method still present"
    s = Scheduler(ParserConfig())
    try:
        assert not hasattr(s, "_mp_f_value"), "dead shared probe handle still present"
    finally:
        s.close()


def test_i02_periodic_recheck_still_downward_only(monkeypatch):

    g = ResourceGovernor()
    g.measured_f = 1024**3  # set EXPLICITLY (the only supported path now)
    fake_psutil = types.SimpleNamespace(
        virtual_memory=lambda: types.SimpleNamespace(available=2.5 * 1024**3)
    )
    monkeypatch.setattr(g, "_cgroup_max", lambda: None)
    monkeypatch.setitem(sys.modules, "psutil", fake_psutil)
    # usable 2 GiB - 2 GiB overhead -> 0 -> floor 1 (downward from 4)
    assert g.periodic_recheck(4) == 1
    # never adjusts upward
    assert g.periodic_recheck(1) == 1
