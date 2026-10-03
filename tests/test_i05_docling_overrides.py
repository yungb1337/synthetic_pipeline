"""I-05 regression tests — docling construction overrides reach the engine.

The old `convert_path` accepted `table_mode`/`ocr` and ignored them; the
converter was always built from `default_config()`. These tests pin the new
contract: overrides are honored, engines are cached per option key, and the
default path is unchanged.
"""

from __future__ import annotations

import pytest

from app.parser.loaders import docling_loader as dl


@pytest.fixture(autouse=True)
def fresh_cache(monkeypatch):
    """Isolate the module-level engine caches per test."""
    _RecordingConverter._reset()
    monkeypatch.setattr(dl, "_engine_cache", {})
    monkeypatch.setattr(dl, "_engine", None)
    yield dl


class _RecordingConverter:
    """Stands in for DocumentConverter; records each construction."""

    built: list = []

    @classmethod
    def _reset(cls):
        cls.built = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        _RecordingConverter.built.append(self)


def test_i05_default_path_builds_and_caches_one_engine(fresh_cache, monkeypatch):
    monkeypatch.setattr(dl, "engine_available", lambda: True)
    monkeypatch.setattr(dl, "_build_converter", _RecordingConverter)

    e1 = dl.get_engine()
    e2 = dl.get_engine()
    assert e1 is e2
    assert len(_RecordingConverter.built) == 1  # built once, reused
    # All lookups without overrides share the default key.
    e3 = dl.get_engine(ocr=True, table_mode="FAST", generate_picture_images=True)
    assert e3 is e1


def test_i05_distinct_overrides_build_distinct_engines(fresh_cache, monkeypatch):
    monkeypatch.setattr(dl, "engine_available", lambda: True)
    monkeypatch.setattr(dl, "_build_converter", _RecordingConverter)

    fast = dl.get_engine(ocr=True, table_mode="FAST")
    acc = dl.get_engine(ocr=True, table_mode="ACCURATE")
    assert fast is not acc
    assert len(_RecordingConverter.built) == 2
    # Same key again -> cached instance
    assert dl.get_engine(ocr=True, table_mode="ACCURATE") is acc
    # Case-insensitive mode key
    assert dl.get_engine(ocr=True, table_mode="accurate") is acc


def test_i05_convert_path_forwards_overrides(fresh_cache, monkeypatch, tmp_path):
    """convert_path must pass the item's table_mode/ocr into the engine cache."""
    monkeypatch.setattr(dl, "engine_available", lambda: True)
    monkeypatch.setattr(dl, "_build_converter", _RecordingConverter)

    seen = {}

    def fake_convert(_self, path, page_range=None):
        seen["page_range"] = page_range
        return object()

    monkeypatch.setattr(_RecordingConverter, "convert", fake_convert, raising=False)

    src = tmp_path / "x.pdf"
    src.write_bytes(b"%PDF-fake")  # never actually parsed; convert is faked
    out = dl.convert_path(str(src), 0, table_mode="ACCURATE", ocr=True)
    assert out is not None
    assert seen["page_range"] == (1, 1)
    # The ACCURATE key was used to resolve the engine
    keys = set()
    # Re-derive the key the module would use (ocr=True, ACCURATE, gpi default)
    from app.parser.config import default_config

    cfg = default_config()
    key = (
        True,
        "ACCURATE",
        bool(getattr(cfg, "docling_generate_picture_images", True)),
    )
    assert key in fresh_cache._engine_cache


def test_i05_unavailable_engine_returns_none(fresh_cache, monkeypatch):
    monkeypatch.setattr(dl, "engine_available", lambda: True)
    monkeypatch.setattr(dl, "_build_converter", lambda **k: False)
    assert dl.get_engine(table_mode="FAST") is None
