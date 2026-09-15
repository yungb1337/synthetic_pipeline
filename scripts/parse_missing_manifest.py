#!/usr/bin/env python
"""parse_missing_manifest.py — Parse any manifest records that have not been parsed yet."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))

from app.parser.config import default_config
from app.parser.events import EventPublisher
from app.parser.extraction import Extractor, set_shared_scheduler
from app.parser.scheduler import Scheduler
from app.parser.storage import FilesystemStore
from app.parser.storage_pages import Ledger, PageStore

RUN_DIR = ROOT_DIR / "checkpoints" / "run" / "run-2026-09-14-eval-1000"
SOURCES_DIR = RUN_DIR / "sources"
MANIFEST_PATH = SOURCES_DIR / "manifest.json"
PARSED_DIR = RUN_DIR / "parsed"


def main() -> int:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    doms = list((PARSED_DIR / "dom").glob("*/dom-*.docJSON"))

    parsed_hashes = set()
    for d in doms:
        try:
            data = json.loads(d.read_text(encoding="utf-8"))
            h = data.get("source_hash")
            if h:
                parsed_hashes.add(h)
        except Exception:
            pass

    missing_records = [m for m in manifest if m["sha256"] not in parsed_hashes]
    print(f"Total manifest records: {len(manifest)}")
    print(f"Already parsed: {len(manifest) - len(missing_records)}")
    print(f"Missing to parse: {len(missing_records)}")

    if not missing_records:
        print("All 1,000 manifest records are already parsed!")
        return 0

    cfg = default_config()
    store = FilesystemStore(str(PARSED_DIR))
    page_store = PageStore(str(store.root))
    ledger = Ledger(str(store.root))
    scheduler = Scheduler(
        cfg,
        page_store=page_store,
        ledger=ledger,
        prefer_in_process_heavy=True,
    )
    set_shared_scheduler(scheduler)
    try:
        extractor = Extractor(
            cfg,
            store,
            events=EventPublisher(),
            scheduler=scheduler,
            page_store=page_store,
            ledger=ledger,
        )

        success = 0
        failed = 0
        total = len(missing_records)

        for i, rec in enumerate(missing_records, 1):
            pdf_path = Path(rec["local_path"])
            if not pdf_path.is_file():
                print(f"[{i}/{total}] Missing file on disk: {pdf_path}")
                failed += 1
                continue

            try:
                data = pdf_path.read_bytes()
                outcome = extractor.extract(data, filename=pdf_path.name, resume=True)
                if outcome.ok:
                    print(f"[{i}/{total}] OK: {pdf_path.name:24} pages={len(outcome.document.pages)} blocks={outcome.report['blocks']}")
                    success += 1
                else:
                    print(f"[{i}/{total}] SKIP/FAIL: {pdf_path.name:24} {outcome.status}")
                    failed += 1
            except Exception as exc:
                print(f"[{i}/{total}] ERROR: {pdf_path.name} -> {exc}")
                failed += 1

        print(f"\nDone! Successfully parsed: {success}, Failed: {failed}")
        return 0 if failed == 0 else 1
    finally:
        set_shared_scheduler(None)
        scheduler.close()


if __name__ == "__main__":
    sys.exit(main())
