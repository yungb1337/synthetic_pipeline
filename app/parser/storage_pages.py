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
from pathlib import Path

from .page_result import PAGE_SCHEMA_VERSION, PageResult, PageStatus
from .utils import write_atomic, get_logger, LedgerCorruptionError

logger = get_logger(__name__)


class PageStore:
    """Durable single-page results under `<root>/pages/<doc_id>/...`."""

    def __init__(self, root: str):
        self.root = Path(root)

    def _page_path(self, doc_id: str, page_index: int) -> Path:
        return self.root / "pages" / doc_id / f"p{page_index}" / f"page-{PAGE_SCHEMA_VERSION}.docJSON"

    def put_page(self, doc_id: str, page_index: int, result: PageResult) -> str:
        """Persist one page result atomically.

        B1 (append, never destroy): a FAILED/DEAD record never OVERWRITES an
        already-durable OK page. If a prior OK page exists on disk we keep it
        (return its path) instead of replacing it with a failure state — a
        transient engine outage or a degraded re-parse must not destroy
        previously-achieved content. OK/PARTIAL results still replace stale
        earlier results (that is a real, intended refresh)."""
        prior = self.get_page(doc_id, page_index)
        if result.status in (PageStatus.FAILED, PageStatus.DEAD) and prior is not None \
                and prior.status == PageStatus.OK:
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

    def _plan_path(self, doc_id: str) -> Path:
        return self.root / "manifest" / doc_id / "plan.json"

    def _journal_path(self, doc_id: str) -> Path:
        return self.root / "manifest" / doc_id / "journal.jsonl"

    # --- plan ---------------------------------------------------------------
    def write_plan(self, doc_id: str, plan: dict) -> str:
        p = self._plan_path(doc_id)
        write_atomic(p, json.dumps(plan, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        jp = self._journal_path(doc_id)
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
                        pages = plan.setdefault("pages", {})
                        key = str(rec["page_index"])
                        prev = pages.get(key, {})
                        prev_attempts = prev.get("attempts", 0) if isinstance(prev, dict) else 0
                        pages[key] = {
                            "status": rec["status"],
                            "checksum": rec.get("checksum", ""),
                            "engine": rec.get("engine"),
                            "attempts": prev_attempts + rec.get("attempt", 1),
                            "errors": rec.get("errors", []),
                        }
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
        with jp.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

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
