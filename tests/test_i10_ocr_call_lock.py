"""I-10 regression tests — OCR engine calls are serialized.

`ocr._engine` is one module-global RapidOCR shared by all native-pool threads;
its thread-safety is undocumented. The fix serializes engine CALLS with a
dedicated `_call_lock` (distinct from the init `_lock`). Tests use a fake
engine that detects concurrent entry — no real model needed.
"""

from __future__ import annotations

import threading
import time

import pytest

from app.parser import ocr


class _ConcurrencyDetectingEngine:
    """Fake RapidOCR: records the max number of simultaneous __call__ bodies."""

    def __init__(self, hold: float = 0.05):
        self.hold = hold
        self._active = 0
        self._guard = threading.Lock()
        self.max_concurrent = 0
        self.calls = 0

    def __call__(self, image):
        with self._guard:
            self._active += 1
            self.calls += 1
            self.max_concurrent = max(self.max_concurrent, self._active)
        try:
            time.sleep(self.hold)
            return _FakeResult()
        finally:
            with self._guard:
                self._active -= 1


class _FakeResult:
    """Shaped like a rapidocr v6 RapidOCROutput (txts/boxes/scores)."""

    txts = ("hello", "world")
    boxes = [[[0, 0], [1, 0], [1, 1], [0, 1]], [[0, 2], [1, 2], [1, 3], [0, 3]]]
    scores = (0.98, 0.97)


@pytest.fixture()
def fake_engine(monkeypatch):
    eng = _ConcurrencyDetectingEngine()
    monkeypatch.setattr(ocr, "_engine", eng)
    return eng


def test_i10_concurrent_calls_do_not_overlap(fake_engine):
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=4) as pool:
        futs = [pool.submit(ocr.ocr_image, b"fakepng") for _ in range(8)]
        results = [f.result(timeout=30) for f in futs]

    assert len(results) == 8
    assert all(r[0][0] == "hello" for r in results)
    # I-10 contract: engine bodies never overlap (was unbounded before).
    assert fake_engine.max_concurrent == 1, (
        f"engine re-entered concurrently: max_concurrent={fake_engine.max_concurrent}"
    )
    assert fake_engine.calls == 8


def test_i10_call_lock_distinct_from_init_lock(fake_engine):
    """The call lock must not be the same lock that guards engine creation."""
    assert ocr._call_lock is not ocr._lock


def test_i10_engine_unavailable_returns_empty(monkeypatch):
    monkeypatch.setattr(ocr, "_engine", False)
    assert ocr.ocr_image(b"x") == []
