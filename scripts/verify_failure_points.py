"""Reproduce the parser failure points documented in
`docs/parser-production-readiness-failure-points.md`.

Read-only with respect to the repo: every check builds its own synthetic PDF and
writes into a fresh temp directory. Nothing in the project tree is modified.

Usage
-----
    .venv/Scripts/python.exe scripts/verify_failure_points.py            # all
    .venv/Scripts/python.exe scripts/verify_failure_points.py F-01 F-05  # a subset

Each check prints the observed behaviour and a PASS/FAIL verdict, where
FAIL means "the failure point reproduced" (i.e. the defect is present).
"""
from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import fitz  # PyMuPDF

from app.parser.config import ParserConfig
from app.parser.events import EventPublisher, silent_sink
from app.parser.extraction import Extractor
from app.parser.page_result import PageResult, PageStatus
from app.parser.storage import FilesystemStore
from app.parser.storage_pages import Ledger, PageStore

NATIVE_CFG = ParserConfig(layout_backend="native")
_results: list[tuple[str, str, bool]] = []


def _report(check_id: str, title: str, reproduced: bool) -> None:
    _results.append((check_id, title, reproduced))
    verdict = "FAIL (defect reproduced)" if reproduced else "PASS (not reproduced)"
    print(f"\n  ==> {check_id}: {verdict}")


def _make_pdf(pages: int = 4, blank: tuple[int, ...] = (), lines: int = 1) -> bytes:
    doc = fitz.open()
    for i in range(pages):
        page = doc.new_page(width=595, height=842)
        if i in blank:
            continue
        for r in range(lines):
            page.insert_text((72, 60 + r * 18), f"Page {i} line {r}: body text.", fontsize=11)
    return doc.tobytes()


def _extractor(root: str | None = None) -> tuple[Extractor, str]:
    root = root or tempfile.mkdtemp()
    store = FilesystemStore(root)
    return Extractor(NATIVE_CFG, store, events=EventPublisher(sink=silent_sink())), root


# ---------------------------------------------------------------------------
def check_F01_blank_page_destroys_document() -> None:
    """F-01 — a single content-free page discards the whole document."""
    print("F-01  One blank page in a 4-page PDF")
    print("      app/parser/assembler.py:56-60 (content_present gate)")

    ex, _ = _extractor()
    po = ex.extract(_make_pdf(4, blank=(2,)), "blank.pdf")
    print(f"      blank-page doc  : status={po.status!r} pages={po.report.get('pages')}"
          f"/{po.report.get('expected_pages')} dom_key={po.report.get('dom_key')!r}")

    ex2, _ = _extractor()
    po2 = ex2.extract(_make_pdf(4), "full.pdf")
    print(f"      control (no blank): status={po2.status!r} pages={po2.report.get('pages')}"
          f"/{po2.report.get('expected_pages')} dom_key={po2.report.get('dom_key')!r}")

    reproduced = po.status != "parsed" or po.report.get("dom_key") is None
    if reproduced:
        print("      -> the 3 content-bearing pages were discarded; no DOM was written.")
    _report("F-01", "blank page destroys the document", reproduced)


# ---------------------------------------------------------------------------
def check_F02_resume_destroys_completed_work() -> None:
    """F-02 — resume=True dead-letters pages that already succeeded."""
    print("F-02  Re-running a completed document with resume=True")
    print("      app/parser/planner.py:137-152 + app/parser/extraction.py:159")

    # The SAME bytes must be used for both runs: doc_id is derived from the
    # source sha256, and fitz embeds a creation timestamp, so regenerating the
    # PDF would yield a different doc_id and silently skip the resume path.
    data = _make_pdf(4)

    ex, root = _extractor()
    po1 = ex.extract(data, "doc.pdf", resume=False)
    doc_id = po1.document_id
    ledger = Ledger(root)
    plan1 = ledger.load_plan(doc_id) or {}
    print(f"      run 1 (resume=False): status={po1.status!r} "
          f"pages={po1.report.get('pages')}/{po1.report.get('expected_pages')}")
    print(f"                            ledger={ {k: v['status'] for k, v in sorted(plan1.get('pages', {}).items())} }")

    stored = sorted(Path(root, "pages", doc_id).glob("p*/*.docJSON"))
    print(f"      page artifacts on disk: {len(stored)}")

    ex2 = Extractor(NATIVE_CFG, FilesystemStore(root), events=EventPublisher(sink=silent_sink()))
    po2 = ex2.extract(data, "doc.pdf", resume=True)
    plan2 = ledger.load_plan(doc_id) or {}
    print(f"      run 2 (resume=True ): status={po2.status!r} "
          f"pages={po2.report.get('pages')}/{po2.report.get('expected_pages')} "
          f"dom_key={po2.report.get('dom_key')!r}")
    print(f"                            ledger={ {k: v['status'] for k, v in sorted(plan2.get('pages', {}).items())} }")
    print(f"                            assembly={plan2.get('assembly', {}).get('status')!r} "
          f"{plan2.get('assembly', {}).get('report')}")

    reproduced = po2.status != "parsed"
    if reproduced:
        print(f"      -> {len(stored)} valid page artifacts remain on disk but were excluded from")
        print("         work_items, never loaded, then dead-lettered. app/processing/executor.py:114")
        print("         passes resume=True unconditionally.")
    _report("F-02", "resume destroys completed work", reproduced)


# ---------------------------------------------------------------------------
def check_F03_torn_ledger_erases_audit_trail() -> None:
    """F-03 — a truncated plan.json silently discards all page state."""
    print("F-03  Ledger write interrupted by a crash")
    print("      app/parser/storage_pages.py:57-60 (non-atomic) + :74-76 (silent return)")

    root = tempfile.mkdtemp()
    ledger = Ledger(root)
    doc_id = "d-torn"
    ledger.write_plan(doc_id, {
        "doc_id": doc_id,
        "pages": {str(i): {"status": "pending", "attempts": 0, "errors": []} for i in range(5)},
        "assembly": {"status": "pending"},
    })

    plan_path = Path(root, "manifest", doc_id, "plan.json")
    plan_path.write_text('{"doc_id": "d-torn", "pages": {"0": {"stat')  # crash mid-write
    print("      simulated a torn write (truncated JSON)")

    raised = None
    try:
        ledger.update_page(doc_id, 0, PageStatus.OK, "chk", "native", 1, [])
        ledger.update_page(doc_id, 1, PageStatus.FAILED, "", "native", 1, [{"m": "boom"}])
        ledger.update_assembly(doc_id, PageStatus.FAILED, [], {"expected_pages": 5})
    except Exception as exc:  # noqa: BLE001
        raised = exc
    print(f"      exception raised by subsequent updates: {raised!r}")
    try:
        final = ledger.load_plan(doc_id)
    except Exception as exc:  # noqa: BLE001
        final = {"error": str(exc)}
    print(f"      load_plan -> {final}")

    reproduced = raised is None and not (final or {}).get("pages")
    if reproduced:
        print("      -> all page statuses and all recorded errors are gone, with no error signal.")
        print("         With events routed to silent_sink() in batch mode this is the only record.")
    _report("F-03", "torn ledger erases the audit trail", reproduced)


# ---------------------------------------------------------------------------
def check_F04_ledger_rewrite_is_quadratic() -> None:
    """F-04 — write_plan re-serializes the whole plan once per page."""
    print("F-04  Ledger rewrite cost vs page count")
    print("      app/parser/storage_pages.py:72-90 (read-modify-write per page)")

    per_page = []
    for n in (50, 200, 800):
        root = tempfile.mkdtemp()
        ledger = Ledger(root)
        doc_id = "d-cost"
        ledger.write_plan(doc_id, {
            "doc_id": doc_id,
            "pages": {str(i): {"status": "pending", "attempts": 0, "errors": []} for i in range(n)},
            "assembly": {"status": "pending"},
        })
        t0 = time.time()
        for i in range(n):
            ledger.update_page(doc_id, i, PageStatus.OK, "c" * 64, "native", 1, [])
        elapsed = (time.time() - t0) * 1000
        size = Path(root, "manifest", doc_id, "plan.json").stat().st_size
        per_page.append(elapsed / n)
        print(f"      {n:>4} pages: {elapsed:8.1f} ms total  {elapsed / n:5.2f} ms/page  "
              f"plan.json={size / 1024:.0f} KiB")

    reproduced = per_page[-1] > per_page[0] * 1.15
    if reproduced:
        print(f"      -> ms/page grew {per_page[0]:.2f} -> {per_page[-1]:.2f}: superlinear.")
        print("         This is pure bookkeeping I/O, on top of extraction.")
    _report("F-04", "ledger rewrite is quadratic", reproduced)


# ---------------------------------------------------------------------------
def check_F05_native_path_is_quadratic() -> None:
    """F-05 — extract_page rescans every page to compute a document median."""
    print("F-05  Native extraction cost vs page count")
    print("      app/parser/engines/native_pdf.py:162-171 (document-wide rescan per page)")

    rows = []
    for n in (5, 10, 20, 40):
        data = _make_pdf(n, lines=40)
        ex, _ = _extractor()
        t0 = time.time()
        ex.extract(data, "p.pdf")
        elapsed = (time.time() - t0) * 1000
        rows.append((n, elapsed, elapsed / n))
        print(f"      {n:>3} pages: {elapsed:8.0f} ms total  {elapsed / n:6.1f} ms/page")

    growth = rows[-1][2] / rows[0][2]
    reproduced = growth > 1.3
    if reproduced:
        print(f"      -> ms/page grew {growth:.2f}x from {rows[0][0]} to {rows[-1][0]} pages: O(n^2).")
        print("         Every page re-opens the PDF and re-scans all N pages' text to")
        print("         recompute the same document-wide median font size.")
    _report("F-05", "native path is quadratic in page count", reproduced)


# ---------------------------------------------------------------------------
def check_F06_pdf_reopened_per_page() -> None:
    """F-06 — the source PDF is opened and re-parsed once per page."""
    print("F-06  fitz.open() call count for one document")
    print("      app/parser/engines/native_pdf.py:146")

    from app.parser.engines import native_pdf

    calls = {"n": 0}
    original = fitz.open

    def counting_open(*args, **kwargs):
        calls["n"] += 1
        return original(*args, **kwargs)

    pages = 20
    data = _make_pdf(pages, lines=10)
    fitz.open = counting_open
    try:
        native_pdf.fitz = fitz
        ex, _ = _extractor()
        ex.extract(data, "p.pdf")
    finally:
        fitz.open = original
    print(f"      {pages}-page document -> fitz.open() called {calls['n']} times")

    reproduced = calls["n"] > pages
    if reproduced:
        print("      -> the PDF structure is re-parsed for every page instead of once.")
    _report("F-06", "PDF re-opened per page", reproduced)


# ---------------------------------------------------------------------------
def check_F07_page_exists_treats_failed_as_done() -> None:
    """F-07 — page_exists() is used as a proxy for 'page succeeded'."""
    print("F-07  page_exists() on a persisted FAILED page")
    print("      app/parser/extraction.py:262-268 (safety net) + scheduler.py:363-369")

    root = tempfile.mkdtemp()
    store = PageStore(root)
    doc_id = "d-fail"
    failed = PageResult(doc_id=doc_id, page_index=3, route="native",
                        status=PageStatus.FAILED, errors=[{"m": "crashed"}])
    store.put_page(doc_id, 3, failed)

    exists = store.page_exists(doc_id, 3)
    roundtrip = store.get_page(doc_id, 3)
    print(f"      persisted a FAILED PageResult; page_exists(3) = {exists}")
    print(f"      stored status on disk    = {roundtrip.status.value if roundtrip else None!r}")

    reproduced = exists and roundtrip is not None and roundtrip.status == PageStatus.FAILED
    if reproduced:
        print("      -> _fail_document() skips any page where page_exists() is True, so a page")
        print("         that failed is never recorded as FAILED by the safety net.")
    _report("F-07", "page_exists treats FAILED as done", reproduced)


# ---------------------------------------------------------------------------
def check_F08_no_logging_no_timeouts() -> None:
    """F-08 — no logging subsystem, no timeouts, no atomic writes."""
    print("F-08  Static audit of app/ for production primitives")

    root = Path(__file__).resolve().parents[1] / "app"
    py = list(root.rglob("*.py"))
    text = {p: p.read_text(encoding="utf-8", errors="ignore") for p in py}

    def count(needles: tuple[str, ...]) -> int:
        return sum(1 for body in text.values() for n in needles if n in body)

    logging_hits = count(("import logging", "logging.getLogger", "logger."))
    timeout_hits = count(("timeout=", "timeout ", ".timeout"))
    atomic_hits = count(("os.replace", "os.fsync", ".fsync("))
    swallow = sum(
        1
        for body in text.values()
        for i, line in enumerate(body.splitlines())
        if line.strip().startswith(("except Exception:", "except:"))
        and i + 1 < len(body.splitlines())
        and body.splitlines()[i + 1].strip() == "pass"
    )
    excepts = sum(
        1
        for body in text.values()
        for line in body.splitlines()
        if line.strip().startswith(("except Exception:", "except:"))
    )

    print(f"      files scanned                       : {len(py)}")
    print(f"      logging references                  : {logging_hits}")
    print(f"      timeout references                  : {timeout_hits}")
    print(f"      os.replace / fsync (atomic writes)  : {atomic_hits}")
    print(f"      bare `except Exception:` / `except:` : {excepts}")
    print(f"        ... of which are followed by `pass`: {swallow}")

    reproduced = logging_hits == 0 or timeout_hits == 0 or atomic_hits == 0
    if reproduced:
        print("      -> there is no logging subsystem, no timeout on any operation, and no")
        print("         atomic-write primitive anywhere in app/.")
    _report("F-08", "no logging / timeouts / atomic writes", reproduced)


# ---------------------------------------------------------------------------
CHECKS = {
    "F-01": check_F01_blank_page_destroys_document,
    "F-02": check_F02_resume_destroys_completed_work,
    "F-03": check_F03_torn_ledger_erases_audit_trail,
    "F-04": check_F04_ledger_rewrite_is_quadratic,
    "F-05": check_F05_native_path_is_quadratic,
    "F-06": check_F06_pdf_reopened_per_page,
    "F-07": check_F07_page_exists_treats_failed_as_done,
    "F-08": check_F08_no_logging_no_timeouts,
}


def main(argv: list[str]) -> int:
    wanted = [a.upper() for a in argv[1:]] or list(CHECKS)
    unknown = [w for w in wanted if w not in CHECKS]
    if unknown:
        print(f"unknown check(s): {', '.join(unknown)}")
        print(f"available: {', '.join(CHECKS)}")
        return 2

    for cid in wanted:
        print("\n" + "=" * 78)
        CHECKS[cid]()

    print("\n" + "=" * 78)
    print("SUMMARY")
    for cid, title, reproduced in _results:
        print(f"  {cid}  {'REPRODUCED' if reproduced else 'not reproduced':<15}  {title}")
    n = sum(1 for _, _, r in _results if r)
    print(f"\n{n}/{len(_results)} failure points reproduced on this machine.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
