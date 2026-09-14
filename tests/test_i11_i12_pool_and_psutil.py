"""I-11 + I-12 regression tests.

I-11 — no private CPython attribute pokes on the live heavy pool: the old
`run_plan` recheck loop mutated `self._heavy_pool._max_workers` (an
implementation detail that may vanish across Python versions). The I-02
refactor removed the mid-run rescale entirely; the pool is built once from
`heavy_concurrency` and rebuilt (after BrokenExecutor) from the current value.

I-12 — no fabricated RAM: both governor derive paths return the safe floor 1
when psutil is unavailable, instead of assuming a 16 GiB box.
"""
from __future__ import annotations

import sys

import pytest

from app.parser.config import ParserConfig
from app.parser.scheduler import ResourceGovernor, Scheduler


def test_i11_no_private_max_workers_mutation_anywhere():
    import inspect

    import app.parser.scheduler as sched_mod

    src = inspect.getsource(sched_mod)
    assert "_max_workers" not in src, (
        "scheduler still pokes the private ProcessPoolExecutor._max_workers"
    )


def test_i11_pool_built_from_declared_heavy_concurrency(monkeypatch):
    """The pool constructor receives max_workers == the declared concurrency;
    there is no post-construction resize path to test anymore."""
    captured = {}
    sched = Scheduler(ParserConfig(), heavy_concurrency=3)

    def mock_pool_constructor(*args, **kwargs):
        captured.update(kwargs)

        class FakePool:
            def submit(self, *a, **k):  # pragma: no cover - never called here
                raise AssertionError

        return FakePool()

    monkeypatch.setattr("app.parser.scheduler.ProcessPoolExecutor",
                        mock_pool_constructor)
    sched._get_heavy_pool()
    assert captured.get("max_workers") == 3
    sched.close()


def test_i11_recheck_updates_only_the_declared_value(monkeypatch):
    """periodic_recheck (explicit measured_f) adjusts the declared value;
    a live pool is never poked — a rebuilt pool picks the new size up."""
    import types

    g = ResourceGovernor()
    g.measured_f = 1024**3
    fake_psutil = types.SimpleNamespace(
        virtual_memory=lambda: types.SimpleNamespace(available=2.5 * 1024**3))
    monkeypatch.setattr(g, "_cgroup_max", lambda: None)
    monkeypatch.setitem(sys.modules, "psutil", fake_psutil)
    assert g.periodic_recheck(4) == 1  # downward only


def test_i12_derive_heavy_concurrency_without_psutil_is_safe(monkeypatch):
    g = ResourceGovernor()
    # `import psutil` raises ImportError when sys.modules maps it to None.
    monkeypatch.setitem(sys.modules, "psutil", None)
    # Even with a measured F, RAM is UNKNOWN -> safe floor 1
    # (old code fabricated 16 GiB here and could derive >1).
    assert g.derive_heavy_concurrency(F=1024**3) == 1
    assert g.derive_heavy_concurrency(F=None) == 1


def test_i12_derive_default_without_psutil_is_safe(monkeypatch):
    g = ResourceGovernor()
    monkeypatch.setitem(sys.modules, "psutil", None)
    assert g.derive_default_heavy_concurrency() == 1
