"""Tests for scripts/preflight_check.py."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from scripts.preflight_check import check_disk, check_ram, check_swap, run_preflight


def test_check_ram_pass():
    mock_mem = MagicMock()
    mock_mem.available = 8 * (1024**3)  # 8 GB available
    mock_mem.total = 16 * (1024**3)
    mock_mem.percent = 50.0

    with patch("scripts.preflight_check.psutil") as mock_psutil:
        mock_psutil.virtual_memory.return_value = mock_mem
        passed, msg = check_ram(min_ram_gb=3.0)
        assert passed is True
        assert "[PASS] RAM:" in msg
        assert "8.00 GB available" in msg


def test_check_ram_fail():
    mock_mem = MagicMock()
    mock_mem.available = 1.5 * (1024**3)  # 1.5 GB available (< 3.0 GB)
    mock_mem.total = 16 * (1024**3)
    mock_mem.percent = 90.0

    with patch("scripts.preflight_check.psutil") as mock_psutil:
        mock_psutil.virtual_memory.return_value = mock_mem
        passed, msg = check_ram(min_ram_gb=3.0)
        assert passed is False
        assert "[FAIL] RAM:" in msg
        assert "1.50 GB available" in msg


def test_check_disk_pass():
    mock_usage = MagicMock()
    mock_usage.free = 20 * (1024**3)  # 20 GB free
    mock_usage.total = 500 * (1024**3)
    mock_usage.percent = 96.0

    with patch("scripts.preflight_check.psutil") as mock_psutil:
        mock_psutil.disk_usage.return_value = mock_usage
        passed, msg = check_disk(".", min_disk_gb=5.0)
        assert passed is True
        assert "[PASS] Disk" in msg
        assert "20.00 GB free" in msg


def test_check_disk_fail():
    mock_usage = MagicMock()
    mock_usage.free = 2.0 * (1024**3)  # 2 GB free (< 5.0 GB)
    mock_usage.total = 500 * (1024**3)
    mock_usage.percent = 99.5

    with patch("scripts.preflight_check.psutil") as mock_psutil:
        mock_psutil.disk_usage.return_value = mock_usage
        passed, msg = check_disk(".", min_disk_gb=5.0)
        assert passed is False
        assert "[FAIL] Disk" in msg
        assert "2.00 GB free" in msg


def test_check_swap():
    mock_swap = MagicMock()
    mock_swap.used = 4 * (1024**3)
    mock_swap.total = 16 * (1024**3)
    mock_swap.percent = 25.0

    with patch("scripts.preflight_check.psutil") as mock_psutil:
        mock_psutil.swap_memory.return_value = mock_swap
        passed, msg = check_swap()
        assert passed is True
        assert "[INFO] Swap/Pagefile:" in msg
        assert "4.00 GB used" in msg


def test_run_preflight_all_pass():
    with (
        patch("scripts.preflight_check.check_ram", return_value=(True, "RAM OK")),
        patch("scripts.preflight_check.check_disk", return_value=(True, "Disk OK")),
        patch("scripts.preflight_check.check_swap", return_value=(True, "Swap OK")),
    ):
        assert run_preflight() is True


def test_run_preflight_fails_on_low_memory():
    with (
        patch("scripts.preflight_check.check_ram", return_value=(False, "RAM LOW")),
        patch("scripts.preflight_check.check_disk", return_value=(True, "Disk OK")),
        patch("scripts.preflight_check.check_swap", return_value=(True, "Swap OK")),
    ):
        assert run_preflight() is False
