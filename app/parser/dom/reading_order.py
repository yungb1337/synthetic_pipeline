"""Recovery of document reading order as an in-memory directed graph.

SYN4: a Reading Order Graph is just a directed graph over text blocks
(block -> next block). It answers "which block should be read next?". We
materialize it as an ordered chain of block ids; each adjacent pair is an
edge. No external graph store is needed.

v0.1 heuristic: per page, top-to-bottom then left-to-right reading. This is
correct and deterministic for single-column documents and for each row of a
multi-column layout. A proper column/LayoutLM pass is a documented future
improvement (see docs/parser-module-spec.md §23). The heuristic is isolated
here so it can be swapped without touching the DOM or loaders.
"""
from __future__ import annotations

from .models import ReadingOrderEntry


def recover_per_page(blocks) -> list:
    """Return blocks ordered for reading on a single page."""
    has_geo = any(b.bbox is not None for b in blocks)
    if has_geo:
        # top-to-bottom, then left-to-right; cluster by vertical line so words
        # on the same baseline stay adjacent.
        return sorted(
            blocks,
            key=lambda b: (
                0 if b.bbox is None or b.bbox[1] is None else int(b.bbox[1] // 2.0),
                0 if b.bbox is None or b.bbox[0] is None else b.bbox[0],
                b.seq,
            ),
        )
    # Purely sequential source (html, markdown, spreadsheet glue).
    return sorted(blocks, key=lambda b: b.seq)


def recover_reading_order(blocks) -> list:
    """Globally order `blocks` across pages into a reading chain."""
    by_page: dict[int, list] = {}
    for b in blocks:
        by_page.setdefault(b.page, []).append(b)
    out: list = []
    for page in sorted(by_page.keys()):
        out.extend(recover_per_page(by_page[page]))
    return out


def build_reading_order_full(pages) -> list[ReadingOrderEntry]:
    """D4: complete, typed reading sequence over the canonical `Document.pages`.

    The existing `Document.reading_order` is a chain of block ids consumed by the
    chunker; this is the SUPERSET that also carries tables and images, in the
    correct per-page canonical order, so every semantic content unit appears
    exactly once. Order within a page: blocks (as already recovered), then tables,
    then images — matching reading flow for the fixture (tables/images follow the
    text that references them). Deterministic given the page list.
    """
    out: list[ReadingOrderEntry] = []
    for page in sorted(pages, key=lambda p: p.index):
        for b in page.blocks:
            out.append(ReadingOrderEntry(type="block", id=b.id))
        for t in page.tables:
            out.append(ReadingOrderEntry(type="table", id=t.id))
        for img in page.images:
            out.append(ReadingOrderEntry(type="image", id=img.id))
    return out