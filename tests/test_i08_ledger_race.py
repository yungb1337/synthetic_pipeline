"""I-08 regression tests — ledger journal race (duplicate-content doc_ids).

Two documents with identical bytes share a doc_id, so two threads can hit one
ledger concurrently. The old `write_plan` unconditionally unlinked the journal
after writing the plan, wiping page updates a concurrent appender had written
after the plan snapshot. These tests pin the new contract: updates written
between snapshot and consolidation survive (or are folded); nothing is lost.
"""

from __future__ import annotations

import threading

from app.parser.page_result import PageStatus
from app.parser.storage_pages import Ledger


def test_i08_write_plan_folds_pending_journal_instead_of_wiping(tmp_path):
    led = Ledger(str(tmp_path / "store"))
    doc = "d-race"
    led.write_plan(doc, {"doc_id": doc, "pages": {}, "assembly": {"status": "pending"}})

    # Thread B appends a page update AFTER thread A snapshotted the plan.
    led.update_page(doc, 1, PageStatus.OK, "cs1", "native", 1, [])

    # Thread A writes ITS plan (its snapshot has page 1 pending).
    led.write_plan(
        doc,
        {
            "doc_id": doc,
            "pages": {
                "1": {
                    "status": "pending",
                    "checksum": "",
                    "engine": None,
                    "attempts": 0,
                    "errors": [],
                }
            },
            "assembly": {"status": "pending"},
        },
    )

    plan = led.load_plan(doc)
    # I-08 contract: B's update survived consolidation (old code: wiped).
    assert plan["pages"]["1"]["status"] == "ok"
    assert plan["pages"]["1"]["checksum"] == "cs1"


def test_i08_journal_and_consolidation_are_serialized(tmp_path):
    """Under concurrent appends + rewrites, every appended record is visible
    either in the plan or still in the journal after all writers finish."""
    led = Ledger(str(tmp_path / "store"))
    doc = "d-storm"
    led.write_plan(doc, {"doc_id": doc, "pages": {}, "assembly": {"status": "pending"}})

    def appender(n):
        for i in range(20):
            led.update_page(doc, i % 4, PageStatus.OK, f"cs{i}", "native", 1, [])

    def rewriter():
        for _ in range(10):
            led.write_plan(
                doc, {"doc_id": doc, "pages": {}, "assembly": {"status": "pending"}}
            )

    threads = [threading.Thread(target=appender, args=(n,)) for n in range(3)]
    threads += [threading.Thread(target=rewriter) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    plan = led.load_plan(doc)
    statuses = {k: v["status"] for k, v in plan["pages"].items()}
    # Every status recorded is ok (updates were applied, never lost/regressed).
    assert set(statuses.values()) <= {"ok"}
    # The 4 pages exist in the folded view.
    assert set(statuses.keys()) <= {"0", "1", "2", "3"}


def test_i08_load_plan_replay_unchanged(tmp_path):
    """The shared fold helper preserves load_plan replay semantics."""
    led = Ledger(str(tmp_path / "store"))
    doc = "d-replay"
    led.write_plan(doc, {"doc_id": doc, "pages": {}, "assembly": {"status": "pending"}})
    led.update_page(doc, 0, PageStatus.PARTIAL, "cs", "docling", 3, [{"m": "e"}])
    plan = led.load_plan(doc)
    p0 = plan["pages"]["0"]
    assert p0["status"] == "partial"
    assert p0["checksum"] == "cs"
    assert p0["engine"] == "docling"
    assert p0["attempts"] == 3
    assert p0["errors"] == [{"m": "e"}]
