"""Recovery of document reading order as an in-memory directed graph.

SYN4: a Reading Order Graph is just a directed graph over text blocks
(block -> next block). It answers "which block should be read next?". We
materialize it as an ordered chain of block ids; each adjacent pair is an
edge. No external graph store is needed.

v0.2 heuristic: per page, geometric column-aware reading order.
- Detects full-width spanning blocks (e.g. titles, section headers, horizontal rules)
  that divide the page vertically into bands.
- Within each vertical band, detects multi-column layouts using geometric gutter
  analysis and vertical overlap between column regions.
- Sorts columns left-to-right, and sorts blocks top-to-bottom within each column.
- Single-column pages and un-columned bands naturally preserve standard top-to-bottom
  flow with baseline clustering.
- Pure and deterministic given the block list; operates on both intermediate loader
  blocks (tuple bbox) and canonical DOM Block objects (BBox model).
"""
from __future__ import annotations

from typing import Any, Optional
from .models import BBox, ReadingOrderEntry


def _get_coords(bbox: Any) -> tuple[float, float, float, float] | None:
    """Extract (x0, y0, x1, y1) from either a BBox object or a tuple/list."""
    if bbox is None:
        return None
    if hasattr(bbox, "x0") and getattr(bbox, "x0") is not None:
        return (float(bbox.x0), float(bbox.y0), float(bbox.x1), float(bbox.y1))
    if isinstance(bbox, (list, tuple)) and len(bbox) >= 4:
        if bbox[0] is not None and bbox[1] is not None and bbox[2] is not None and bbox[3] is not None:
            return (float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3]))
    return None


def _partition_columns(blocks_with_coords: list[tuple[Any, tuple[float, float, float, float]]]) -> list[Any]:
    """Recursively partition a list of (block, coords) into natural reading order."""
    if len(blocks_with_coords) <= 1:
        return [b for b, _ in blocks_with_coords]

    min_x = min(c[0] for _, c in blocks_with_coords)
    max_x = max(c[2] for _, c in blocks_with_coords)
    w = max_x - min_x
    if w < 100:
        # Narrow region: sort top-to-bottom, clustering nearby baselines
        return [b for b, _ in sorted(blocks_with_coords, key=lambda it: (int(it[1][1] // 3.0), it[1][0], getattr(it[0], "seq", 0)))]

    # Check for full-width spanning blocks (e.g. titles, section headers) that divide vertical bands
    spanning = [it for it in blocks_with_coords if (it[1][2] - it[1][0]) >= 0.70 * w and w > 200]
    if spanning and len(spanning) < len(blocks_with_coords):
        sorted_by_y = sorted(blocks_with_coords, key=lambda it: it[1][1])
        bands: list[tuple[str, list[tuple[Any, tuple[float, float, float, float]]]]] = []
        cur_band: list[tuple[Any, tuple[float, float, float, float]]] = []
        for it in sorted_by_y:
            is_span = (it[1][2] - it[1][0]) >= 0.70 * w and w > 200
            if is_span:
                if cur_band:
                    bands.append(("normal", cur_band))
                    cur_band = []
                bands.append(("span", [it]))
            else:
                cur_band.append(it)
        if cur_band:
            bands.append(("normal", cur_band))

        out: list[Any] = []
        for kind, band in bands:
            if kind == "span":
                out.extend([b for b, _ in band])
            else:
                out.extend(_partition_columns(band))
        return out

    # Column partition within a band: find best vertical gutter separating left/right columns
    centers = sorted([(c[0] + c[2]) / 2 for _, c in blocks_with_coords])
    best_split = None
    best_score = 0.0

    for i in range(len(centers) - 1):
        x_cut = (centers[i] + centers[i + 1]) / 2
        left = [it for it in blocks_with_coords if (it[1][0] + it[1][2]) / 2 < x_cut]
        right = [it for it in blocks_with_coords if (it[1][0] + it[1][2]) / 2 >= x_cut]
        if not left or not right:
            continue

        max_left_x1 = max(it[1][2] for it in left)
        min_right_x0 = min(it[1][0] for it in right)
        gutter = min_right_x0 - max_left_x1

        min_left_y = min(it[1][1] for it in left)
        max_left_y = max(it[1][3] for it in left)
        min_right_y = min(it[1][1] for it in right)
        max_right_y = max(it[1][3] for it in right)

        overlap_y = max(0.0, min(max_left_y, max_right_y) - max(min_left_y, min_right_y))
        span_y = max(max_left_y, max_right_y) - min(min_left_y, min_right_y)

        # Significant vertical overlap indicates parallel columns rather than sequential vertical blocks
        if span_y > 0 and (overlap_y / span_y) >= 0.20:
            score = (100.0 if gutter >= 0 else -abs(gutter)) + (overlap_y / span_y) * 50.0
            if score > best_score and gutter >= -15.0:
                best_score = score
                best_split = (x_cut, left, right)

    if best_split:
        _x_cut, left, right = best_split
        # Check if there are top header items that sit strictly above the two-column region
        min_left_y = min(it[1][1] for it in left)
        min_right_y = min(it[1][1] for it in right)
        top_cutoff = min(min_left_y, min_right_y)
        top_items = [it for it in blocks_with_coords if it[1][3] <= top_cutoff + 2.0]
        body_items = [it for it in blocks_with_coords if it[1][3] > top_cutoff + 2.0]
        if top_items and body_items and len(top_items) < len(blocks_with_coords):
            return _partition_columns(top_items) + _partition_columns(body_items)

        return _partition_columns(left) + _partition_columns(right)

    # No multi-column partition found: sort top-to-bottom, clustering nearby baselines
    return [b for b, _ in sorted(blocks_with_coords, key=lambda it: (int(it[1][1] // 3.0), it[1][0], getattr(it[0], "seq", 0)))]


def recover_per_page(blocks) -> list:
    """Return blocks ordered for reading on a single page."""
    if not blocks:
        return []

    geo_items = []
    non_geo = []
    for b in blocks:
        coords = _get_coords(getattr(b, "bbox", None))
        if coords is not None:
            geo_items.append((b, coords))
        else:
            non_geo.append(b)

    if geo_items:
        ordered_geo = _partition_columns(geo_items)
        if non_geo:
            ordered_non_geo = sorted(non_geo, key=lambda b: getattr(b, "seq", 0))
            return ordered_geo + ordered_non_geo
        return ordered_geo

    # Purely sequential source (html, markdown, spreadsheet glue).
    return sorted(blocks, key=lambda b: getattr(b, "seq", 0))


def recover_reading_order(blocks) -> list:
    """Globally order `blocks` across pages into a reading chain."""
    by_page: dict[int, list] = {}
    for b in blocks:
        by_page.setdefault(getattr(b, "page", 0), []).append(b)
    out: list = []
    for page in sorted(by_page.keys()):
        out.extend(recover_per_page(by_page[page]))
    return out


def build_regions(page) -> list[tuple[str, list[str], tuple]]:
    """Partition a page's blocks into geometric regions.

    Returns a list of ``(kind, block_ids, bbox)`` tuples where:

    * ``kind`` — ``"column"``, ``"sidebar"``, ``"footnote"``, or ``"header"``
    * ``block_ids`` — the ids of the blocks belonging to this region
    * ``bbox`` — the union bbox of all blocks in the region

    The algorithm reuses the existing column-partition logic
    (``_partition_columns``) and adds footnotes / header detection
    based on y-position and width heuristics.  Additive — consumers
    that don't need regions simply ignore this field.
    """
    if not page.blocks:
        return []

    blocks = page.blocks
    page_h = page.height or 1000.0
    page_w = page.width or 1000.0

    # --- footnotes: bottom 15% of page, narrow width ---
    footnote_zone_y = page_h * 0.85
    footnotes: list = []
    main_blocks: list = []
    for b in blocks:
        if b.bbox and b.bbox.y0 >= footnote_zone_y and (b.bbox.x1 - b.bbox.x0) < page_h * 0.6:
            footnotes.append(b)
        else:
            main_blocks.append(b)

    # --- headers: top 10% of page, full or near-full width ---
    header_zone_y = page_h * 0.10
    headers: list = []
    body_blocks: list = []
    for b in main_blocks:
        if b.bbox and b.bbox.y1 <= header_zone_y and (b.bbox.x1 - b.bbox.x0) >= page_w * 0.5:
            headers.append(b)
        else:
            body_blocks.append(b)

    # --- columns / sidebar: reuse _partition_columns on body ---
    regions: list[tuple[str, list[str], tuple]] = []

    if headers:
        regions.append((
            "header",
            [b.id for b in headers],
            _union_bbox([b.bbox for b in headers if b.bbox]),
        ))

    if body_blocks:
        geo_items = []
        for b in body_blocks:
            coords = _get_coords(b.bbox)
            if coords:
                geo_items.append((b, coords))
            else:
                # non-geo blocks: treat as a single column
                regions.append((
                    "column",
                    [b.id],
                    b.bbox,  # already a BBox
                ))
        if geo_items:
            ordered = _partition_columns(geo_items)
            # group contiguous same-column blocks
            current_col: list = []
            current_bbox: list = []
            prev_center_x: float | None = None
            for b in ordered:
                coords = _get_coords(b.bbox)
                if coords is None:
                    if current_col:
                        regions.append((
                            "column",
                            [x.id for x in current_col],
                            _union_bbox(current_bbox),
                        ))
                        current_col = []
                        current_bbox = []
                        prev_center_x = None
                    continue
                cx = (coords[0] + coords[2]) / 2
                if prev_center_x is not None and abs(cx - prev_center_x) > 50:
                    # new column detected
                    regions.append((
                        "column",
                        [x.id for x in current_col],
                        _union_bbox(current_bbox),
                    ))
                    current_col = []
                    current_bbox = []
                current_col.append(b)
                current_bbox.append(coords)
                prev_center_x = cx
            if current_col:
                regions.append((
                    "column",
                    [x.id for x in current_col],
                    _union_bbox(current_bbox),
                ))

    if footnotes:
        regions.append((
            "footnote",
            [b.id for b in footnotes],
            _union_bbox([b.bbox for b in footnotes if b.bbox]),
        ))

    return regions


def _union_bbox(bboxes: list) -> BBox | None:
    """Union of a list of BBox objects / tuples / None."""
    valid = []
    for bb in bboxes:
        if bb is None:
            continue
        if hasattr(bb, "x0"):
            valid.append((bb.x0, bb.y0, bb.x1, bb.y1))
        elif isinstance(bb, (list, tuple)) and len(bb) >= 4:
            valid.append(tuple(bb[:4]))
    if not valid:
        return None
    x0 = min(v[0] for v in valid)
    y0 = min(v[1] for v in valid)
    x1 = max(v[2] for v in valid)
    y1 = max(v[3] for v in valid)
    return BBox(x0=x0, y0=y0, x1=x1, y1=y1)


def _bbox_to_tuple(bbox) -> BBox | None:
    if bbox is None:
        return None
    if hasattr(bbox, "x0"):
        return BBox(x0=bbox.x0, y0=bbox.y0, x1=bbox.x1, y1=bbox.y1)
    return BBox(x0=bbox[0], y0=bbox[1], x1=bbox[2], y1=bbox[3])


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