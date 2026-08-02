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