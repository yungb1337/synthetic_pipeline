#!/usr/bin/env python
"""Pre-flight resource check for memory and disk headroom.

Verifies that the system has sufficient free RAM and free disk space before
launching memory-heavy parser batches or benchmarks.

Usage:
    .venv/Scripts/python.exe scripts/preflight_check.py [--min-ram-gb 3.0] [--min-disk-gb 5.0] [--path .]

Exit code:
    0: All checks passed
    1: One or more checks failed (insufficient headroom)
"""

from __future__ import annotations

import argparse
from pathlib import Path

try:
    import psutil
except ImportError:
    psutil = None


def check_ram(min_ram_gb: float = 3.0) -> tuple[bool, str]:
    """Check available system RAM against minimum threshold (in GB)."""
    if psutil is None:
        return True, "RAM: psutil not installed (check skipped)"
    try:
        mem = psutil.virtual_memory()
        avail_gb = mem.available / (1024**3)
        total_gb = mem.total / (1024**3)
        used_pct = mem.percent
        passed = avail_gb >= min_ram_gb
        tag = "PASS" if passed else "FAIL"
        msg = (
            f"[{tag}] RAM: {avail_gb:.2f} GB available / {total_gb:.2f} GB total "
            f"({used_pct:.1f}% used) — required: >= {min_ram_gb:.2f} GB"
        )
        return passed, msg
    except Exception as exc:
        return False, f"[FAIL] RAM check error: {exc}"


def check_disk(path: str | Path = ".", min_disk_gb: float = 5.0) -> tuple[bool, str]:
    """Check available disk space at given path against minimum threshold (in GB)."""
    if psutil is None:
        return True, "Disk: psutil not installed (check skipped)"
    try:
        target = Path(path).resolve()
        usage = psutil.disk_usage(str(target))
        free_gb = usage.free / (1024**3)
        total_gb = usage.total / (1024**3)
        used_pct = usage.percent
        passed = free_gb >= min_disk_gb
        tag = "PASS" if passed else "FAIL"
        msg = (
            f"[{tag}] Disk ({target.drive or str(target)}): {free_gb:.2f} GB free / {total_gb:.2f} GB total "
            f"({used_pct:.1f}% used) — required: >= {min_disk_gb:.2f} GB"
        )
        return passed, msg
    except Exception as exc:
        return False, f"[FAIL] Disk check error: {exc}"


def check_swap() -> tuple[bool, str]:
    """Report swap/pagefile usage (advisory)."""
    if psutil is None:
        return True, "Swap: psutil not installed (check skipped)"
    try:
        swap = psutil.swap_memory()
        used_gb = swap.used / (1024**3)
        total_gb = swap.total / (1024**3)
        pct = swap.percent
        msg = f"[INFO] Swap/Pagefile: {used_gb:.2f} GB used / {total_gb:.2f} GB total ({pct:.1f}% committed)"
        return True, msg
    except Exception as exc:
        return True, f"[INFO] Swap check error: {exc}"


def run_preflight(
    min_ram_gb: float = 3.0, min_disk_gb: float = 5.0, path: str = "."
) -> bool:
    """Run all preflight checks and print formatted results. Returns True if all pass."""
    print("=== Pre-flight System Headroom Check ===")
    ram_ok, ram_msg = check_ram(min_ram_gb=min_ram_gb)
    disk_ok, disk_msg = check_disk(path=path, min_disk_gb=min_disk_gb)
    _, swap_msg = check_swap()

    print(ram_msg)
    print(disk_msg)
    print(swap_msg)

    all_passed = ram_ok and disk_ok
    if all_passed:
        print("=== Result: ALL CHECKS PASSED (Safe to proceed) ===")
    else:
        print("=== Result: INSUFFICIENT HEADROOM (Risk of std::bad_alloc / OOM) ===")
    return all_passed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--min-ram-gb",
        type=float,
        default=3.0,
        help="Min available RAM in GB (default: 3.0)",
    )
    parser.add_argument(
        "--min-disk-gb",
        type=float,
        default=5.0,
        help="Min free disk in GB (default: 5.0)",
    )
    parser.add_argument(
        "--path",
        type=str,
        default=".",
        help="Target directory for disk check (default: .)",
    )
    args = parser.parse_args()

    ok = run_preflight(
        min_ram_gb=args.min_ram_gb, min_disk_gb=args.min_disk_gb, path=args.path
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
