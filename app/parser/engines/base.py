"""Page engine protocol (ADR-013, Phase B).

A `PageEngine` transforms ONE `PageWorkItem` (carrying a `src_path`, NOT raw
bytes) into ONE `PageResult`. Engines are pure transforms — they do NOT touch
the DOM, the ledger, or the store. This keeps them unit-testable and preserves
the loader→builder contract (`Recovered*` parts).

`PageWorkItem` is defined here (forward-importable) so engines and the planner
share one type. The route band one engine serves is advertised via
`route_band` (one of `native|enrichment|docling|image|simple`).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from ..page_result import PageResult, PageStatus

if TYPE_CHECKING:
    from ..config import ParserConfig
    from ..parts import RoutingDecision


# Bands a page engine can serve.
NATIVE = "native"
ENRICHMENT = "enrichment"
DOCLING = "docling"
IMAGE = "image"
SIMPLE = "simple"


@dataclass
class PageWorkItem:
    """A single page's unit of work.

    Only str/int/bool fields cross the `ProcessPoolExecutor` boundary — engines
    are built INSIDE the worker process and never pickled.
    """

    doc_id: str = ""
    source_hash: str = ""
    src_path: str = ""
    page_index: int = 0
    route: str = ""              # the band this page belongs to
    # Optional routing decision (kept only for provenance / fallback routing);
    # not relied upon for dispatch.
    decision: "RoutingDecision | None" = None
    models_dir: str = ""
    ocr_enabled: bool = True
    attempt: int = 0


@runtime_checkable
class PageEngine(Protocol):
    route_band: str

    def process(self, item: PageWorkItem) -> PageResult: ...


__all__ = [
    "PageEngine",
    "PageWorkItem",
    "PageResult",
    "PageStatus",
    "NATIVE",
    "ENRICHMENT",
    "DOCLING",
    "IMAGE",
    "SIMPLE",
]