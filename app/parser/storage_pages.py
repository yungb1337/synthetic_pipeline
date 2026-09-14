"""Page store + per-document ledger (ADR-013 T11).

Additive to the existing `FilesystemStore`: under the SAME store root we add
  * `pages/<doc_id>/p<page_index>/page-<ver>.docJSON`
  * `manifest/<doc_id>/plan.json`

The `raw/`, `dom/`, `images/` layout of `FilesystemStore` is untouched — these
are additional directories on the same root (additive, per hard constraint #3).
The page store persists one `PageResult` per page (durable unit); the ledger
records the per-page status + the assembly outcome so resume/retry/dead-letter
are idempotent.
"""
from __future__ import annotations

import json
import re
import threading
from pathlib import Path

from .page_result import PAGE_SCHEMA_VERSION, PageResult, PageStatus
from .utils import write_atomic, get_logger, LedgerCorruptionError

logger = get_logger(__name__)

# I-07: the persisted page JSON has exactly ONE top-level `"status": "<x>"`
# field (no nested part carries a `status` key), so a regex probe extracts it
# without a full JSON parse + Pydantic part construction.
_STATUS_RE = re.compile(r'"status"\s*:\s*"([a-z]+)"')


def _fold_page_record(plan: dict, rec: dict) -> None:
    """I-08: apply one journal `op=page` record onto a plan dict (shared by
    `load_plan` replay and `write_plan` consolidation — one set of semantics)."""
    pages = plan.setdefault("pages", {})
    key = str(rec.get("page_index"))
    prev = pages.get(key, {})
    prev_attempts = prev.get("attempts", 0) if isinstance(prev, dict) else 0
    pages[key] = {
        "status": rec.get("status", "pending"),
        "checksum": rec.get("checksum", ""),
        "engine": rec.get("engine"),
        "attempts": prev_attempts + (rec.get("attempt") or 1),
        "errors": rec.get("errors", []),
    }


class PageStore:
    """Durable single-page results under `<root>/pages/<doc_id>/...`."""

    def __init__(self, root: str):
        self.root = Path(root)

    def _page_path(self, doc_id: str, page_index: int) -> Path:
        return self.root / "pages" / doc_id / f"p{page_index}" / f"page-{PAGE_SCHEMA_VERSION}.docJSON"

    def page_status(self, doc_id: str, page_index: int) -> str | None:
        """I-07: cheap status probe — extracts ONLY the top-level `status`
        field from the persisted artifact without building a `PageResult`
        (no JSON-object walk, no Pydantic part construction). Disk remains
        the source of truth, so this is safe across processes.

        Returns the status string (e.g. "ok"), or None when the page does not
        exist or cannot be read (corrupt = absent, matching `page_exists`'s
        safety semantics).
        """
        p = self._page_path(doc_id, page_index)
        if not p.exists():
            return None
        try:
            m = _STATUS_RE.search(p.read_text(encoding="utf-8"))
            return m.group(1) if m else None
        except Exception:
            return None

    def put_page(self, doc_id: str, page_index: int, result: PageResult) -> str:
        """Persist one page result atomically.

        B1 (append, never destroy): a FAILED/DEAD record never OVERWRITES an
        already-durable OK page. If a prior OK page exists on disk we keep it
        (return its path) instead of replacing it with a failure state — a
        transient engine outage or a degraded re-parse must not destroy
        previously-achieved content. OK/PARTIAL results still replace stale
        earlier results (that is a real, intended refresh).

        I-07: the prior-state check uses the cheap `page_status` probe (a
        regex over the raw file) instead of a full `get_page` read+parse on
        EVERY write. The B1 decision is byte-for-byte equivalent: a corrupt
        prior file probes as None and is overwritten, exactly as before.
        """
        prior_status = self.page_status(doc_id, page_index)
        if result.status in (PageStatus.FAILED, PageStatus.DEAD) \
                and prior_status == PageStatus.OK.value:
            return self._page_path(doc_id, page_index)
        result.checksum = result.compute_checksum()
        p = self._page_path(doc_id, page_index)
        write_atomic(p, result.to_json(), encoding="utf-8")
        return f"pages/{doc_id}/p{page_index}/page-{PAGE_SCHEMA_VERSION}.docJSON"

    def get_page(self, doc_id: str, page_index: int) -> PageResult | None:
        p = self._page_path(doc_id, page_index)
        if not p.exists():
            return None
        try:
            return PageResult.from_json(p.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"Failed to load page {doc_id}/p{page_index}: {e}")
            return None

    def page_exists(self, doc_id: str, page_index: int) -> bool:
        p = self._page_path(doc_id, page_index)
        if not p.exists():
            return False
        try:
            result = PageResult.from_json(p.read_text(encoding="utf-8"))
            return result.status not in (PageStatus.FAILED, PageStatus.DEAD)
        except Exception:
            return False  # corrupt file → treat as not existing (safety)


class Ledger:
    """Per-document execution plan + status record under `<root>/manifest/`."""

    def __init__(self, root: str):
        self.root = Path(root)
        # I-08: journal appends and journal consolidation (write_plan) are
        # mutually exclusive, so a concurrent appender's records either land
        # in the consolidated plan or remain in the journal for the next
        # replay — they are never wiped unseen. (Documents with identical
        # bytes share a doc_id, so two batch threads CAN hit one ledger.)
        self._jl = threading.Lock()

    def _plan_path(self, doc_id: str) -> Path:
        return self.root / "manifest" / doc_id / "plan.json"

    def _journal_path(self, doc_id: str) -> Path:
        return self.root / "manifest" / doc_id / "journal.jsonl"

    # --- plan ---------------------------------------------------------------
    def write_plan(self, doc_id: str, plan: dict) -> str:
        p = self._plan_path(doc_id)
        jp = self._journal_path(doc_id)
        with self._jl:
            # I-08: consolidate any journal records into the plan being written
            # (same accumulate semantics as the load_plan replay, via the shared
            # fold helper) BEFORE the atomic write, and unlink the journal under
            # the same lock. An appender blocked on `_jl` then appends to a FRESH
            # journal file — nothing is lost. Known cosmetic tradeoff: on a
            # concurrent replan the same record can be folded once via
            # load_plan's prior-pages replay and once here, inflating `attempts`
            # by one; the critical state (status/checksum/errors) is replace-safe.
            if jp.exists():
                try:
                    for line in jp.read_text(encoding="utf-8").splitlines():
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            rec = json.loads(line)
                        except Exception:
                            continue
                        if rec.get("op") == "page":
                            _fold_page_record(plan, rec)
                except Exception as e:
                    logger.warning(f"Journal fold skipped for {doc_id}: {e}")
            write_atomic(p, json.dumps(plan, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")
            if jp.exists():
                try:
                    jp.unlink()
                except Exception:
                    pass
        return f"manifest/{doc_id}/plan.json"

    def load_plan(self, doc_id: str) -> dict | None:
        p = self._plan_path(doc_id)
        jp = self._journal_path(doc_id)
        if not p.exists() and not jp.exists():
            return None

        plan: dict | None = None
        if p.exists():
            # Try loading the main file
            try:
                plan = json.loads(p.read_text(encoding="utf-8"))
            except json.JSONDecodeError as e:
                logger.warning(f"Ledger corrupted for {doc_id}, attempting .tmp recovery: {e}")
                # Attempt recovery from .tmp file (from interrupted atomic write)
                tmp_candidates = list(p.parent.glob(f"{p.name}.tmp*"))
                for tmp in tmp_candidates:
                    try:
                        plan = json.loads(tmp.read_text(encoding="utf-8"))
                        logger.info(f"Recovered {doc_id} plan from {tmp.name}")
                        # Restore the main file atomically
                        write_atomic(p, json.dumps(plan, indent=2, ensure_ascii=False, sort_keys=True))
                        break
                    except Exception:
                        continue
                if plan is None:
                    # No recovery possible
                    logger.error(f"Ledger corruption for {doc_id}: no valid .tmp recovery found")
                    raise LedgerCorruptionError(f"plan.json corrupt for {doc_id}, no .tmp recovery: {e}")
            except Exception as e:
                logger.error(f"Failed to load plan for {doc_id}: {e}")
                raise LedgerCorruptionError(f"plan.json unreadable for {doc_id}: {e}")

        if plan is None:
            plan = {"doc_id": doc_id, "pages": {}, "assembly": {"status": "pending"}}

        if jp.exists():
            try:
                for line in jp.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    rec = json.loads(line)
                    op = rec.get("op")
                    if op == "page":
                        _fold_page_record(plan, rec)
                    elif op == "assembly":
                        plan["assembly"] = {
                            "status": rec["status"],
                            "assembled_page_set": rec.get("assembled_page_set", []),
                            "report": rec.get("report", {}),
                        }
            except Exception as e:
                logger.warning(f"Error replaying journal for {doc_id}: {e}")

        return plan

    def update_page(self, doc_id: str, page_index: int, status, checksum: str,
                    engine: str | None, attempt: int, errors: list) -> None:
        """Record one page's status in the ledger.

        Uses an append-only journal (journal.jsonl) to ensure O(1) time per page
        update regardless of document size (F-04 fix). The journal is consolidated
        into plan.json during write_plan() or update_assembly().
        """
        jp = self._journal_path(doc_id)
        jp.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "op": "page",
            "page_index": page_index,
            "status": status.value if isinstance(status, PageStatus) else str(status),
            "checksum": checksum,
            "engine": engine,
            "attempt": attempt or 1,
            "errors": errors or [],
        }
        # I-08: append under the journal lock (see write_plan).
        with self._jl:
            with jp.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def update_pages_batch(self, doc_id: str, updates: list[dict]) -> None:
        """A6: Record multiple page statuses in the journal in a single batch write."""
        if not updates:
            return
        jp = self._journal_path(doc_id)
        jp.parent.mkdir(parents=True, exist_ok=True)
        lines = []
        for u in updates:
            st = u["status"]
            entry = {
                "op": "page",
                "page_index": u["page_index"],
                "status": st.value if isinstance(st, PageStatus) else str(st),
                "checksum": u.get("checksum", ""),
                "engine": u.get("engine"),
                "attempt": u.get("attempt") or 1,
                "errors": u.get("errors") or [],
            }
            lines.append(json.dumps(entry, ensure_ascii=False) + "\n")
        with self._jl:
            with jp.open("a", encoding="utf-8") as f:
                f.writelines(lines)

    def update_assembly(self, doc_id: str, status, assembled_set: list, report: dict) -> None:
        try:
            plan = self.load_plan(doc_id)
        except LedgerCorruptionError:
            logger.error(f"Cannot update assembly for {doc_id}: ledger corrupted")
            raise
        if plan is None:
            plan = {"doc_id": doc_id, "pages": {}}
        plan.setdefault("pages", {})
        plan["assembly"] = {
            "status": status.value if isinstance(status, PageStatus) else str(status),
            "assembled_page_set": list(assembled_set),
            "report": report,
        }
        self.write_plan(doc_id, plan)
