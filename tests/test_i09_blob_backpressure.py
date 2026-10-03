"""I-09 regression tests — bounded per-document blob accumulation.

The scheduler now releases in-RAM image blobs once the page is durably
persisted; the assembler reloads blobs from the page store for the pages it
folds, so `put_image` still sees identical bytes.
"""

from __future__ import annotations

from app.parser.config import ParserConfig
from app.parser.engines.base import PageWorkItem
from app.parser.page_result import PageResult, PageStatus
from app.parser.parts import RecoveredImage
from app.parser.planner import ExecutionPlan
from app.parser.storage import FilesystemStore
from app.parser.storage_pages import Ledger, PageStore


def _img(page: int, tag: str) -> RecoveredImage:
    return RecoveredImage(
        page=page, mime="image/png", checksum=tag, blob=f"PNGDATA-{tag}".encode()
    )


def _page_with_image(doc_id: str, idx: int) -> PageResult:
    r = PageResult(
        doc_id=doc_id,
        page_index=idx,
        route="native",
        status=PageStatus.OK,
        images=[_img(idx, f"img{idx}")],
    )
    return r


def _plan(doc_id: str, pages: list[int]) -> ExecutionPlan:
    items = [
        PageWorkItem(
            doc_id=doc_id,
            source_hash="sha",
            src_path="/tmp/x.pdf",
            page_index=i,
            route="native",
        )
        for i in pages
    ]
    return ExecutionPlan(
        doc_id=doc_id,
        source_hash="sha",
        sha="sha",
        route="native",
        decision=None,
        detected_type="pdf",
        mime="application/pdf",
        declared_extension="pdf",
        probe="magic",
        expected_page_set=pages,
        page_count=len(pages),
        page_sizes={},
        metadata={},
        config_snapshot={},
        work_items=items,
    )


def test_i09_collect_strips_blobs_after_durable_persist(monkeypatch, tmp_path):
    """After _collect persists a page, its in-RAM blobs are released."""
    from app.parser.scheduler import Scheduler

    sched = Scheduler(ParserConfig())
    root = tmp_path / "store"
    sched.page_store = PageStore(str(root))
    sched.ledger = Ledger(str(root))

    item = PageWorkItem(
        doc_id="d-b", source_hash="sha", src_path="/tmp/x.pdf", page_index=0
    )
    res = _page_with_image("d-b", 0)
    assert res.images[0].blob  # blob present pre-persist

    class DoneFuture:
        def result(self, timeout=None):
            return res

    out = sched._collect(item, DoneFuture())
    assert out.status == PageStatus.OK
    # I-09 contract: durable copy keeps the blob, the in-RAM copy releases it.
    durable = sched.page_store.get_page("d-b", 0)
    assert durable.images[0].blob == b"PNGDATA-img0"
    assert out.images[0].blob == b""
    sched.close()


def test_i09_assembler_reloads_blobs_for_folded_pages(tmp_path):
    """The assembler restores blob bytes from the page store before fold."""
    from app.parser.assembler import Assembler

    root = tmp_path / "store"
    store = FilesystemStore(str(root))
    ps = PageStore(str(root))
    ledger = Ledger(str(root))

    doc_id = "d-rel"
    plan = _plan(doc_id, [0])
    r = _page_with_image(doc_id, 0)
    r.images[0].blob = b""  # simulate the post-persist strip
    ps.put_page(doc_id, 0, _page_with_image(doc_id, 0))  # durable copy has blob

    a = Assembler(ParserConfig(), store, ledger)
    a.page_store = ps
    results = a._reload_page_blobs(plan, [r])
    assert results[0].images[0].blob == b"PNGDATA-img0"


def test_i09_reload_is_noop_when_blobs_present(tmp_path):
    from app.parser.assembler import Assembler

    root = tmp_path / "store"
    store = FilesystemStore(str(root))
    ps = PageStore(str(root))
    a = Assembler(ParserConfig(), store, None)
    a.page_store = ps

    plan = _plan("d-noop", [0])
    r = _page_with_image("d-noop", 0)  # blob present — nothing stripped

    results = a._reload_page_blobs(plan, [r])
    assert results is r or results[0] is r  # same objects returned untouched


def test_i09_reload_degrades_faithfully_without_durable_copy(tmp_path):
    """Missing durable page → stripped blobs stand (never crash the assemble)."""
    from app.parser.assembler import Assembler

    root = tmp_path / "store"
    store = FilesystemStore(str(root))
    ps = PageStore(str(root))
    a = Assembler(ParserConfig(), store, None)
    a.page_store = ps

    plan = _plan("d-missing", [0])
    r = _page_with_image("d-missing", 0)
    r.images[0].blob = b""  # stripped, no durable copy

    results = a._reload_page_blobs(plan, [r])
    assert results[0].images[0].blob == b""  # faithful: no invented bytes
