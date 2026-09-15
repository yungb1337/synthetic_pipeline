#!/usr/bin/env python
"""Monitor progress of full corpus run."""
import glob
import json
import time
from pathlib import Path
try:
    import psutil
except ImportError:
    psutil = None

def report(parsed_dir="checkpoints/run/run-2026-09-14-full-corpus/parsed"):
    root = Path(parsed_dir)
    plans = list((root / "manifest").glob("*/plan.json"))
    ok = 0
    total_pages = 0
    total_blocks = 0
    total_tables = 0
    total_images = 0
    total_refs = 0
    routes = {}

    for p in plans:
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            if d.get("assembly", {}).get("status") == "ok":
                ok += 1
                pages_cnt = len(d.get("expected_page_set", []))
                total_pages += pages_cnt

                # Check DOM counts & route
                doc_id = p.parent.name
                dom_files = sorted((root / "dom" / doc_id).glob("dom-*.docJSON"))
                if dom_files:
                    dom_data = json.loads(dom_files[-1].read_text(encoding="utf-8"))
                    dom_pages = dom_data.get("pages", [])
                    total_blocks += sum(len(pg.get("blocks", [])) for pg in dom_pages)
                    total_tables += sum(len(pg.get("tables", [])) for pg in dom_pages)
                    total_images += sum(len(pg.get("images", [])) for pg in dom_pages)
                    total_refs += len(dom_data.get("references", []))
                    r = dom_data.get("provenance", {}).get("routing", {}).get("route", "unknown")
                    routes[r] = routes.get(r, 0) + 1
        except Exception:
            pass

    mem_str = ""
    if psutil:
        vm = psutil.virtual_memory()
        mem_str = f" | RAM: {vm.percent}% ({vm.used/(1024**3):.2f}/{vm.total/(1024**3):.2f} GB)"

    pct = (ok / 945.0) * 100
    print(f"[{ok}/945 ({pct:.1f}%)] Pages: {total_pages} | Blocks: {total_blocks} | Tables: {total_tables} | Images: {total_images} | Refs: {total_refs}{mem_str}")
    print(f"Routes: {routes}")

if __name__ == "__main__":
    report()
