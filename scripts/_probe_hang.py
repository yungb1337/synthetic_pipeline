#!/usr/bin/env python
"""probe_hang — reproduce test_docling_path_records_provenance_and_order body
with a stdlib faulthandler stack-dump watchdog, so the hang location is visible.

This is a DIAGNOSTIC script (not product code). It mirrors the pytest test exactly
so a hang proves environmental/pre-existing vs introduced by the B4 edit.
Run: .venv/Scripts/python.exe scripts/_probe_hang.py
"""
import faulthandler
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Dump all threads' stacks if anything is still running after 60s, then exit.
faulthandler.dump_traceback_later(60, exit=True, file=sys.stderr)

import fitz  # noqa: E402
from app.parser.events import EventPublisher  # noqa: E402
from app.parser.extraction import Extractor  # noqa: E402
from app.parser.storage import FilesystemStore  # noqa: E402
from app.parser.config import ParserConfig  # noqa: E402

t0 = time.time()
store = FilesystemStore(str(Path("_probe_hang_store")))
pub = EventPublisher(sink=lambda name, payload: None)
ex = Extractor(ParserConfig(layout_backend="docling"), store, events=pub)


def pdf_bytes():
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 100), "Clinical Report", fontsize=20)
    page.insert_text((72, 130), "The patient has stable diabetes on metformin.", fontsize=11)
    try:
        tab = page.new_table()
        tab.add_row(["patient_id", "age"])
        tab.add_row(["P0001", "62"])
        tab.add_row(["P0002", "31"])
        page.insert_table((72, 200), tab, border=0.5)
    except Exception:
        pass
    return doc.tobytes()


raw = pdf_bytes()
print(f"[probe] extract #1 starting at t={time.time()-t0:.1f}s", flush=True)
out = ex.extract(raw, "complex.pdf")
print(f"[probe] extract #1 done at t={time.time()-t0:.1f}s ok={out.ok}", flush=True)
print(f"[probe] extract #2 starting at t={time.time()-t0:.1f}s", flush=True)
out2 = ex.extract(raw, "complex2.pdf")
print(f"[probe] extract #2 done at t={time.time()-t0:.1f}s ok={out2.ok}", flush=True)
print("[probe] COMPLETE", flush=True)