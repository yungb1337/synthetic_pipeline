"""I-07 regression tests — cheap status probe replaces read-before-write.

`put_page` used to do a full `get_page` (disk read + JSON parse + Pydantic
part construction) on EVERY page write just to apply the B1 rule; the
scheduler restored prior OK pages via another full read. These tests pin:
1. The probe reads the file but never constructs a PageResult.
2. The B1 decision is unchanged (failed never clobbers a durable OK page).
3. The scheduler's restore path probes first, reads only when needed.
"""
from __future__ import annotations

import pytest

from app.parser.page_result import PageResult, PageStatus
from app.parser.storage_pages import PageStore


def _ok(idx=0) -> PageResult:
    return PageResult(doc_id="d-p", page_index=idx, route="native",
                      status=PageStatus.OK,
                      blocks=[__import__("app.parser.parts", fromlist=["RecoveredBlock"]).RecoveredBlock(
                          page=idx, text="content", source="text")])


def _failed(idx=0) -> PageResult:
    return PageResult(doc_id="d-p", page_index=idx, route="native",
                      status=PageStatus.FAILED,
                      errors=[{"page_no": idx + 1, "category": "x", "message": "boom"}])


def test_i07_probe_reads_status_without_building_pageresult(monkeypatch, tmp_path):
    ps = PageStore(str(tmp_path / "store"))
    ps.put_page("d-p", 0, _ok())

    built = {"n": 0}
    real_from_json = PageResult.from_json

    @classmethod
    def counting_from_json(cls, s):
        built["n"] += 1
        return real_from_json(s)

    monkeypatch.setattr(PageResult, "from_json", counting_from_json)

    st = ps.page_status("d-p", 0)
    assert st == "ok"
    assert built["n"] == 0, "page_status must not construct PageResult objects"

    # Nonexistent page -> None
    assert ps.page_status("d-p", 999) is None


def test_i07_b1_rule_unchanged(tmp_path):
    ps = PageStore(str(tmp_path / "store"))
    # Durable OK first...
    ps.put_page("d-p", 0, _ok())
    # ...a later FAILED write must NOT clobber it (B1: append, never destroy)
    path = ps.put_page("d-p", 0, _failed())
    prior = ps.get_page("d-p", 0)
    assert prior.status == PageStatus.OK
    # OK/PARTIAL still refresh stale results (intended overwrite)
    ps.put_page("d-p", 1, _failed(1))
    ps.put_page("d-p", 1, _ok(1))
    assert ps.page_status("d-p", 1) == "ok"


def test_i07_corrupt_prior_file_probes_none_and_overwrites(tmp_path):
    ps = PageStore(str(tmp_path / "store"))
    ps.put_page("d-p", 0, _ok())
    p = ps._page_path("d-p", 0)
    p.write_text("{corrupt json", encoding="utf-8")
    # Corrupt = absent (safety semantics) -> failed result may overwrite
    assert ps.page_status("d-p", 0) is None
    ps.put_page("d-p", 0, _failed())
    assert ps.page_status("d-p", 0) == "failed"


def test_i07_restore_path_probes_before_full_read(monkeypatch, tmp_path):
    """Scheduler restore: probe first; full read ONLY when a prior OK exists."""
    from app.parser.scheduler import Scheduler
    from app.parser.engines.base import PageWorkItem
    from app.parser.config import ParserConfig

    sched = Scheduler(ParserConfig())
    ps = PageStore(str(tmp_path / "store"))
    sched.page_store = ps
    from app.parser.storage_pages import Ledger
    sched.ledger = Ledger(str(tmp_path / "store"))

    item = PageWorkItem(doc_id="d-r", source_hash="sha", src_path="/tmp/x.pdf", page_index=0)

    class BrokenFuture:
        def result(self, timeout=None):
            raise RuntimeError("worker exploded")

    built = {"n": 0}
    real_from_json = PageResult.from_json

    @classmethod
    def counting_from_json(cls, s):
        built["n"] += 1
        return real_from_json(s)

    monkeypatch.setattr(PageResult, "from_json", counting_from_json)

    # Case 1: no prior page -> probe says None -> NO full read
    res = sched._collect(item, BrokenFuture())
    assert res.status == PageStatus.FAILED
    reads_case1 = built["n"]
    assert reads_case1 == 0, "restore path read the full page without a prior OK"

    # Case 2: durable OK prior -> probe hits -> exactly ONE full read to restore
    ps.put_page("d-r", 0, _ok())
    res2 = sched._collect(item, BrokenFuture())
    assert res2.status == PageStatus.OK, "prior OK page must be restored over failure"
    assert built["n"] == 1, f"expected exactly 1 full read for restore, got {built['n']}"
    sched.close()
