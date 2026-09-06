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

    # --- plan ---------------------------------------------------------------
    def write_plan(self, doc_id: str, plan: dict) -> str:
        p = self.root / "manifest" / doc_id / "plan.json"
        write_atomic(p, json.dumps(plan, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        return f"manifest/{doc_id}/plan.json"

    def load_plan(self, doc_id: str) -> dict | None:
        p = self.root / "manifest" / doc_id / "plan.json"
        if not p.exists():
            return None

        # Try loading the main file
        try:
            return json.loads(p.read_text(encoding="utf-8"))
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
                    return plan
                except Exception:
                    continue

            # No recovery possible
            logger.error(f"Ledger corruption for {doc_id}: no valid .tmp recovery found")
            raise LedgerCorruptionError(f"plan.json corrupt for {doc_id}, no .tmp recovery: {e}")
        except Exception as e:
            logger.error(f"Failed to load plan for {doc_id}: {e}")
            raise LedgerCorruptionError(f"plan.json unreadable for {doc_id}: {e}")

    def update_page(self, doc_id: str, page_index: int, status, checksum: str,
                    engine: str | None, attempt: int, errors: list) -> None:
        # F-04 fix: never silently drop updates when ledger corrupt
        try:
            plan = self.load_plan(doc_id)
        except LedgerCorruptionError:
            logger.error(f"Cannot update page {page_index} for {doc_id}: ledger corrupted")
            raise  # propagate — do not silently ignore
        if plan is None:
            raise LedgerCorruptionError(f"plan is None for {doc_id} — corruption before update")
        pages = plan.setdefault("pages", {})
        key = str(page_index)
        prev = pages.get(key, {})
        # G3: ACCUMULATE attempts (prev + this attempt) instead of taking the
        # max. Each persistence of a page result is one more attempt; the ledger
        # must reflect the true number of tries across resumes/retries.
        pages[key] = {
            "status": status.value if isinstance(status, PageStatus) else str(status),
            "checksum": checksum,
            "engine": engine,
            "attempts": (prev.get("attempts", 0) if isinstance(prev, dict) else 0) + (attempt or 1),
            "errors": errors,
        }
        self.write_plan(doc_id, plan)

    def update_assembly(self, doc_id: str, status, assembled_set: list, report: dict) -> None:
        try:
            plan = self.load_plan(doc_id)
        except LedgerCorruptionError:
            logger.error(f"Cannot update assembly for {doc_id}: ledger corrupted")
            raise
        if plan is None:
            plan = {}
        plan.setdefault("pages", {})
        plan["assembly"] = {
            "status": status.value if isinstance(status, PageStatus) else str(status),
            "assembled_page_set": list(assembled_set),
            "report": report,
        }
        self.write_plan(doc_id, plan)
