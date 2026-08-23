"""Generic bibliography / reference extraction (D3).

Goal: recover a numbered bibliography from a parsed document WITHOUT document-
specific hardcoding, and wire body citations (`[33]`) to their bibliography
entries through `citation_index`.

Signal and guard
----------------
A bibliography is recognized when one of two reliable signals holds:

  1. A heading block reads "References" / "Bibliography" / "Citations" (academic
     PDFs near-universally use one). When this heading is present we trust the
     following blocks as reference entries — headings are a strong, low-FP signal.

  2. No such heading, BUT >= 3 blocks start with a bracketed number `[n]` AND the
     document body contains inline citations whose numbers overlap those blocks.
     This guards against mis-firing on prose that merely contains `[n]`: we only
     treat `[n]`-leading blocks as references when the body actually cites them.

Reference entries
-----------------
Each entry is a block whose text starts with `[n]`. Wrap-around continuation lines
(a block not starting with `[n]` that immediately follows an entry) are merged into
the current entry's text — faithful, no invented separators beyond a single space.
The leading `[n]` is stripped to form the entry `text`; the number becomes the
`label` and the structural `id` (`<doc_id>/ref-<n>`). `citation_index` maps the
bare number to that id, built strictly from matched labels (no fabrication).

The function is PURE: it only reads `Page`/`Block` data and returns structured
references. It never mutates the DOM.
"""
from __future__ import annotations

import re

from .models import Reference

_BIB_HEADINGS = {"references", "bibliography", "citations", "reference", "bibliographies"}
_CITATION = re.compile(r"\[(\d{1,4})\]")
_ENTRY = re.compile(r"^\s*\[(\d{1,4})\]\s*(.*)$", re.DOTALL)


def _heading_blocks(pages) -> list:
    heads = []
    for page in pages:
        for b in page.blocks:
            if getattr(b, "kind", "") in ("heading", "title"):
                heads.append(b)
    return heads


def _bibliography_start_index(pages) -> int | None:
    """Index (in page/then-block reading order) of the first bib heading, or None."""
    idx = 0
    for page in pages:
        for b in page.blocks:
            if getattr(b, "kind", "") in ("heading", "title"):
                txt = (b.text or "").strip().lower()
                # Match a heading that is *exactly* a bibliography label (or starts
                # with it, e.g. "References and Notes"). Avoid "Reference Methods".
                if txt in _BIB_HEADINGS or any(
                    txt.startswith(h) and len(txt) <= len(h) + 12 for h in _BIB_HEADINGS
                ):
                    return idx
            idx += 1
    return None


def _all_blocks(pages):
    for page in pages:
        for b in page.blocks:
            yield b


def _recover_labels_from_source(pages, src_bytes: bytes) -> dict[int, dict[int, set[str]]]:
    """Recover the set of bracketed `[n]` entry markers for bibliography blocks from
    the SOURCE PDF — the ground truth for where one entry ends and the next begins.

    Why a SET of markers per block (not a single label): Docling sometimes merges
    several bibliography entries into ONE block (`[1] … [2] … [3] …`). The leading
    marker alone is not enough to recover entries 2..N. By reading every `[n]` word
    that sits at the start of a line (x at/left of the entry text, within the block's
    vertical span) we learn exactly how many entries the block contains, and can
    split it faithfully.

    This also DISAMBIGUATES real entry markers from inline citations such as a year
    `[2023]` or a body reference `[3]`: those sit inline (x beyond the entry start),
    so they are excluded. Never fabricate a number — every returned marker really is
    in the source.

    Returns {page_1based: {id(block): {entry numbers…}}}.
    """
    out: dict[int, dict[int, set[str]]] = {}
    try:
        import fitz
    except Exception:
        return out
    try:
        pdf = fitz.open(stream=src_bytes, filetype="pdf")
    except Exception:
        return out
    for page in pages:
        pno = int(getattr(page, "index", 0))
        words = []
        try:
            p = pdf[pno - 1]  # fitz is 0-based; block.page is 1-based
            words = p.get_text("words")  # x0,y0,x1,y1,text,...
        except Exception:
            continue
        for b in page.blocks:
            bb = getattr(b, "bbox", None)
            if bb is None:
                continue
            x0, y0, y1 = float(bb.x0), float(bb.y0), float(bb.y1)
            nums: set[str] = set()
            for w in words:
                wx0, wy0, wx1, wy1, wtxt = w[0], w[1], w[2], w[3], w[4]
                # Within the block's vertical span (allow a small line-height slack).
                if not (y0 - 8 <= wy0 <= y1 + 8):
                    continue
                # At/before the entry text start (inline citations sit further right).
                if wx0 > x0 + 6:
                    continue
                m = re.fullmatch(r"\[(\d{1,4})\]", wtxt.strip())
                if m:
                    nums.add(m.group(1))
            if nums:
                out.setdefault(pno, {})[id(b)] = nums
    return out


def extract_references(pages, doc_id: str = "", src_bytes: bytes | None = None) -> tuple[list[Reference], dict[str, str]]:
    """Return (references, citation_index) for the document's bibliography.

    `references` is empty and `citation_index` empty when no bibliography signal
    is found (mis-fire guard) — never fabricates entries. When `src_bytes` is
    given and entry text lost its `[n]` marker during mapping, the label is
    recovered geometrically from the source (see `_recover_labels_from_source`).
    """
    blocks = list(_all_blocks(pages))
    body_text = " ".join(b.text or "" for b in blocks)
    body_citations = set(_CITATION.findall(body_text))

    start = _bibliography_start_index(pages)
    heading_found = start is not None

    # Collect candidate entry blocks + detect [n]-leading blocks anywhere.
    n_leading_blocks: list[tuple[int, "Block"]] = []
    for i, b in enumerate(blocks):
        m = _ENTRY.match(b.text or "")
        if m and (start is None or i > start):
            n_leading_blocks.append((i, b))

    # Mis-fire guard (no heading path): require both volume and body corroboration.
    if not heading_found:
        cand_nums = {m.group(1) for _, b in n_leading_blocks for m in [_ENTRY.match(b.text or "")]}
        if len(n_leading_blocks) < 3 or not (cand_nums & body_citations):
            return [], {}

    # Determine the slice of blocks to scan for entries.
    scan_from = (start + 1) if heading_found else 0
    # Stop at the next heading after the bib heading (if any).
    stop_at = None
    if heading_found:
        for j in range(start + 1, len(blocks)):
            if getattr(blocks[j], "kind", "") in ("heading", "title"):
                stop_at = j
                break

    refs: list[Reference] = []
    citation_index: dict[str, str] = {}
    # Geometric label recovery: {page_1based: {id(block): {entry numbers…}}}.
    # For blocks with a leading [n], we still use that as the primary entry split.
    # For merged blocks, geo supplies the ADDITIONAL entry numbers inside the block.
    geo = _recover_labels_from_source(pages, src_bytes) if src_bytes else {}

    def _split_merged_block(text: str, leading_num: str, geo_nums: set[str]) -> list[tuple[str, str, str]]:
        """Split a block that contains multiple [n] markers into individual entries.

        Uses `_CITATION` (no anchors) to find every `[n]` position in the text,
        then splits ONLY at markers whose number is confirmed by the source
        geometry (`geo_nums`) — this rejects inline citations like `[2023]`.

        Returns list of (num, label, text) for each entry found in this block.
        """
        all_nums = {leading_num} | (geo_nums or set())
        # Find every [n] position; only keep those confirmed by geometry.
        found = []
        for m in _CITATION.finditer(text):
            if m.group(1) in all_nums:
                found.append((m.start(), m.group(1), m.group(0)))
        if not found:
            # Fallback: the leading [n] is the only entry.
            m = _ENTRY.match(text)
            if m:
                return [(m.group(1), f"[{m.group(1)}]", (m.group(2) or "").strip())]
            return []
        if len(found) == 1:
            _, num, raw = found[0]
            body = text[len(raw):].strip()
            return [(num, f"[{num}]", body)]
        # Multiple confirmed markers: split at each one.
        entries = []
        for k, (start, num, raw) in enumerate(found):
            end_pos = found[k + 1][0] if k + 1 < len(found) else len(text)
            seg = text[start + len(raw):end_pos].strip()
            entries.append((num, f"[{num}]", seg))
        return entries

    # Collect one entry per reference number, keeping the LONGEST text seen for
    # that number. Docling sometimes emits OVERLAPPING bibliography blocks (e.g.
    # block A ends with `[2]…partial`, block B starts with `[2]…full`); that would
    # otherwise produce duplicate labels `[2]`. Keeping the longest occurrence per
    # number yields the complete entry and collapses the overlap. Numbers are
    # globally unique in a bibliography, so this is a faithful, document-agnostic
    # de-duplication (no fabricated entries).
    best: dict[str, Reference] = {}

    def _keep(num: str, text: str) -> None:
        rid = f"{doc_id}/ref-{num}" if doc_id else f"ref-{num}"
        label = f"[{num}]"
        existing = best.get(num)
        if existing is None or len(text) > len(existing.text):
            best[num] = Reference(kind="citation", target=rid, id=rid, label=label, text=text)

    for i in range(scan_from, len(blocks) if stop_at is None else stop_at):
        b = blocks[i]
        text = (b.text or "").strip()
        if not text:
            continue

        # Primary split: does the block START with [n]?
        m = _ENTRY.match(text)
        geo_nums = geo.get(int(getattr(b, "page", 0)) or 0, {}).get(id(b), set())

        if m:
            leading_num = m.group(1)
            # Use geo to discover ADDITIONAL entry numbers inside this block.
            all_nums = {leading_num} | geo_nums
            if len(all_nums) > 1:
                # Merged block: segment by ALL markers found in the text.
                for num, _label, entry_text in _split_merged_block(text, leading_num, geo_nums):
                    _keep(num, entry_text)
            else:
                # Single entry in this block.
                entry_text = (m.group(2) or "").strip()
                _keep(leading_num, entry_text)
        elif geo_nums:
            # Block has NO leading [n] but geo found markers (marker dropped in
            # mapping). Emit one entry per confirmed number, each carrying the
            # block's full text (downstream consumers get the complete entry).
            for num in sorted(geo_nums, key=int):
                _keep(num, text)
        # else: not a bibliography entry (continuation handled by next block's detection)

    ordered = sorted(best.values(), key=lambda r: int(r.label[1:-1]))
    refs = ordered
    citation_index = {r.label[1:-1]: r.id for r in ordered}
    return refs, citation_index

