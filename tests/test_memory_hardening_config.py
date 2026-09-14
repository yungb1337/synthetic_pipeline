"""Tests for memory hardening configuration and scheduler recycling."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.parser.config import ParserConfig, default_config
from app.parser.loaders.docling_loader import _make_pipeline_options
from app.parser.scheduler import Scheduler


def test_parser_config_hardening_defaults():
    cfg = default_config()
    assert cfg.docling_generate_picture_images is True
    assert cfg.heavy_pool_max_tasks_per_child == 10

    snap = cfg.snapshot()
    assert "docling_generate_picture_images" in snap
    assert snap["docling_generate_picture_images"] is True
    assert "heavy_pool_max_tasks_per_child" in snap
    assert snap["heavy_pool_max_tasks_per_child"] == 10


def test_parser_config_custom_values():
    cfg = ParserConfig(
        docling_generate_picture_images=False,
        heavy_pool_max_tasks_per_child=5,
    )
    assert cfg.docling_generate_picture_images is False
    assert cfg.heavy_pool_max_tasks_per_child == 5

    snap = cfg.snapshot()
    assert snap["docling_generate_picture_images"] is False
    assert snap["heavy_pool_max_tasks_per_child"] == 5


def test_make_pipeline_options_honors_generate_picture_images():
    class DummyPipelineOptions:
        def __init__(self, do_ocr=True, do_code_formula=False, generate_picture_images=True):
            self.do_ocr = do_ocr
            self.do_code_formula = do_code_formula
            self.generate_picture_images = generate_picture_images

    # When generate_picture_images=True
    opts_true = _make_pipeline_options(
        DummyPipelineOptions, ocr=True, table_mode="", generate_picture_images=True
    )
    assert opts_true.generate_picture_images is True

    # When generate_picture_images=False (memory-hardening mode)
    opts_false = _make_pipeline_options(
        DummyPipelineOptions, ocr=True, table_mode="", generate_picture_images=False
    )
    assert opts_false.generate_picture_images is False


def test_scheduler_passes_max_tasks_per_child():
    cfg = ParserConfig(heavy_pool_max_tasks_per_child=7)
    sched = Scheduler(cfg)

    captured_kwargs = {}

    def mock_pool_constructor(*args, **kwargs):
        captured_kwargs.update(kwargs)
        mock_pool = MagicMock()
        return mock_pool

    with patch("app.parser.scheduler.ProcessPoolExecutor", side_effect=mock_pool_constructor):
        pool = sched._get_heavy_pool()
        assert pool is not None
        assert captured_kwargs.get("max_tasks_per_child") == 7

    sched.close()
