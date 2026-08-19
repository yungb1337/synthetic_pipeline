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


class PageStore:
    """Durable single-page results under `<root>/pages/<doc_id>/...`."""

    def __init__(self, root: str):
        self.root = Path(root)

    def _page_path(self, doc_id: str, page_index: int) -> Path:
        return self.root / "pages" / doc_id / f"p{page_index}" / f"page-{PAGE_SCHEMA_VERSION}.docJSON"

    def put_page(self, doc_id: str, page_index: int, result: PageResult) -> str:
        result.checksum = result.compute_checksum()
        p = self._page_path(doc_id, page_index)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(result.to_json(), encoding="utf-8")
        return f"pages/{doc_id}/p{page_index}/page-{PAGE_SCHEMA_VERSION}.docJSON"

    def get_page(self, doc_id: str, page_index: int) -> PageResult | None:
        p = self._page_path(doc_id, page_index)
        if not p.exists():
            return None
        try:
            return PageResult.from_json(p.read_text(encoding="utf-8"))
        except Exception:
            return None

    def page_exists(self, doc_id: str, page_index: int) -> bool:
        return self._page_path(doc_id, page_index).exists()


class Ledger:
    """Per-document execution plan + status record under `<root>/manifest/`."""

    def __init__(self, root: str):
        self.root = Path(root)

    # --- plan ---------------------------------------------------------------
    def write_plan(self, doc_id: str, plan: dict) -> str:
        p = self.root / "manifest" / doc_id / "plan.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(plan, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        return f"manifest/{doc_id}/plan.json"

    def load_plan(self, doc_id: str) -> dict | None:
        p = self.root / "manifest" / doc_id / "plan.json"
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None

    def update_page(self, doc_id: str, page_index: int, status, checksum: str,
                    engine: str | None, attempt: int, errors: list) -> None:
        plan = self.load_plan(doc_id)
        if plan is None:
            return
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
        plan = self.load_plan(doc_id) or {}
        plan.setdefault("pages", {})
        plan["assembly"] = {
            "status": status.value if isinstance(status, PageStatus) else str(status),
            "assembled_page_set": list(assembled_set),
            "report": report,
        }
        self.write_plan(doc_id, plan)
