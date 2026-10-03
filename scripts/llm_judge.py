#!/usr/bin/env python
"""llm_judge.py — score a parsed DOM against its source PDF using a light LLM.

Compares the page-centric parser's canonical DOM (Document JSON) with the
source document (text extracted from the PDF via PyMuPDF) and asks a lightweight
Gemini model to return a structured multi-metric verdict:

  metrics: {completeness, fidelity, structure, tables, references, scans_ocr}
  verdict: PASS | PASS_WITH_ISSUES | FAIL
  issues:  [{severity: critical|major|minor, surface, detail, suggestion}]

Every metric is derived from real DOM fields (num_blocks, tables, references,
citation_index, reading_order_full, page/block presence) — the model judges the
SOURCE-vs-DOM correspondence, not the DOM's absolute size.

Usage:
    .venv/Scripts/python.exe scripts/llm_judge.py \
        --pdf  <source.pdf> --dom <document.parsed.v1.docJSON> --out <verdict.json>
    optional: --model gemini-2.0-flash-lite --max-chars 6000 --api-key <KEY>

Key resolution: --api-key > $GEMINI_API_KEY env > key.py at repo root (gitignored).
Missing key => exit 3 with a clear message (judge is additive, never fatal).
Dependencies: google.generativeai 0.8.6, PyMuPDF, requests (all installed)."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import fitz

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

DEFAULT_MODEL = "gemini-3.5-flash-lite"
FALLBACK_MODELS = (
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-2.5-flash",
    "gemini-3.7-flash",
    "gemini-3.8-flash",
    "gemini-3.5-flash",
    "gemini-3.6-flash",
)

# Gemini free-tier can return transient 429/resource-exhausted. We honor the
# retry_delay the API reports and retry a bounded number of times, then exit 4
# ("transient rate limit") so the batch driver can skip this doc and continue
# instead of aborting the whole run (exit 3 stays reserved for fatal: key/auth).
_MAX_ATTEMPTS = 2
_RATE_LIMIT_MARKERS = (
    "429",
    "RESOURCE_EXHAUSTED",
    "rate limit",
    "quota",
    "TooManyRequests",
)

_RETRY_SLEEP_SECONDS = 20.0  # fallback if the API does not report a delay

# Table preview bounds for summarize_dom. The judge's "tables" metric is only
# evaluable when the DOM summary carries real cell content; a dims-only preview
# scored "not evaluable" -> 0 on any doc that HAS tables (see run-2026-09-06
# triage). These caps keep the preview bounded AND tell the model that anything
# beyond the shown text is a preview limit, not a parser defect.
_CELL_CHARS = 24  # truncate each cell / header string to this many chars
_TABLE_BUDGET = 1200  # total characters across the whole tables_preview
_TABLE_PREVIEW_ROWS = 2  # first data rows per table


def _rate_limited(exc: BaseException) -> bool:
    """True when the exception looks like a transient Gemini rate-limit/quota error.

    Distinguishes retryable quota exhaustion from fatal errors (bad key, model
    name, network) so the retry loop never burns time on unrecoverable inputs."""
    name = type(exc).__name__
    msg = str(exc)
    return any(
        m.lower() in name.lower() or m.lower() in msg.lower()
        for m in _RATE_LIMIT_MARKERS
    )


def _retry_delay(exc: BaseException) -> float:
    """Best-effort retry delay (seconds): prefer the API's reported value.

    Gemini's resource-exhausted response carries a human-readable "retry in
    Ns"; some SDK error objects expose a structured retry_delay. Falls back to
    _RETRY_SLEEP_SECONDS when neither is available."""
    structured = getattr(exc, "retry_delay", None)
    if structured is not None:
        try:
            seconds = getattr(structured, "seconds", None)
            if seconds is None:
                seconds = float(structured.total_seconds())
            if seconds and seconds > 0:
                return float(seconds)
        except Exception:  # noqa: BLE001 — malformed; fall through to regex
            pass
    m = re.search(r"retry in\s+([0-9.]+)\s*s", str(exc), re.IGNORECASE)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            pass
    return _RETRY_SLEEP_SECONDS


def resolve_key(cli_key: str | None) -> str | None:
    all_keys = resolve_all_keys(cli_key)
    return all_keys[0] if all_keys else None


def resolve_all_keys(cli_key: str | None = None) -> list[str]:
    keys: list[str] = []
    if cli_key and cli_key not in keys:
        keys.append(cli_key)
    env = os.environ.get("GEMINI_API_KEY")
    if env and env not in keys:
        keys.append(env)
    k = Path(__file__).resolve().parents[1] / "key.py"
    if k.is_file():
        try:
            ns: dict = {}
            exec(k.read_text(encoding="utf-8"), ns)  # noqa: S102 — local, gitignored
            for name in ("key", "key1", "key2", "GEMINI_API_KEY", "API_KEY", "KEY"):
                val = ns.get(name)
                if isinstance(val, str) and val and val not in keys:
                    keys.append(val)
        except Exception as exc:  # noqa: BLE001
            print(f"  [judge] key.py unreadable: {exc}", file=sys.stderr)
    return keys


def _pick_preview_pages(total_pages: int, budget_pages: int) -> list[int]:
    """Choose page indices (0-based) to include in the judge source preview.

    Always includes the first and last pages (title/abstract + references) and
    fills the rest evenly across the document, so a multi-page doc is judged on
    structure + tail, not just the head. Returns [] for empty docs."""
    if total_pages <= 0:
        return []
    if total_pages <= budget_pages:
        return list(range(total_pages))
    n = max(2, budget_pages)
    idx = {0, total_pages - 1}
    idx.update(round(i * (total_pages - 1) / (n - 1)) for i in range(1, n - 1))
    return sorted(idx)


def extract_source_text(pdf_path: Path, max_chars: int) -> dict:
    """Extract reference text from the source PDF as a document-wide preview.

    Samples pages across the WHOLE document (first + evenly spaced + last) so the
    judge sees abstract, body, and references — capped per page so the prompt stays
    small. Returns {pages, page_count, preview_page_count, total_chars}."""
    pages: list[str] = []
    total = 0
    real_count = 0
    try:
        with fitz.open(pdf_path) as doc:
            real_count = doc.page_count
            budget = max(2, min(8, real_count))  # preview at most 8 pages
            for i in _pick_preview_pages(real_count, budget):
                t = doc[i].get_text("text").strip()
                if not t:
                    t = f"[page {i + 1}: NO EXTRACTABLE TEXT — likely scanned/image]"
                # per-page cap so total stays bounded (~5000 chars max)
                t = t[: max(400, max_chars // budget)]
                pages.append(t)
                total += len(t)
    except Exception as exc:  # noqa: BLE001
        return {
            "error": f"fitz open failed: {exc}",
            "pages": pages,
            "page_count": real_count,
            "preview_page_count": len(pages),
            "total_chars": total,
        }
    return {
        "pages": pages,
        "page_count": real_count,
        "preview_page_count": len(pages),
        "total_chars": total,
    }


def summarize_dom(dom: dict, max_chars: int = 4000) -> dict:
    """Compress a DOM into the shape the judge prompt needs (all real fields)."""
    meta = dom.get("metadata", {})
    pages = dom.get("pages", [])
    nblocks = sum(len(p.get("blocks", [])) for p in pages)
    ntables = sum(len(p.get("tables", [])) for p in pages)
    nimages = sum(len(p.get("images", [])) for p in pages)
    refs = dom.get("references", [])
    citation_index = dom.get("citation_index", {})
    ro_full = dom.get("reading_order_full", [])

    # Sample of text blocks across pages for fidelity spot check
    total_text_chars = sum(
        len(b.get("text", "")) for p in pages for b in p.get("blocks", [])
    )
    sample_blocks: list[str] = []
    used_chars = 0
    for p in sorted(pages, key=lambda x: x.get("index", 0)):
        p_idx = p.get("index", 0)
        p_blocks = p.get("blocks", [])
        # Prefer sampling body content blocks rather than repetitive margin headers/footers
        body_blocks = [b for b in p_blocks if b.get("kind") not in ("header", "footer")]
        sample_candidates = body_blocks if body_blocks else p_blocks
        for b in sample_candidates[:3]:
            txt = (b.get("text") or "").strip()
            if txt:
                snippet = f"[p{p_idx}:{b.get('kind', 'block')}] {txt[:200]}"
                sample_blocks.append(snippet)
                used_chars += len(snippet)
                if used_chars > 2000:
                    break
        if used_chars > 2000:
            break

    blocks_preview_note = (
        f"SAMPLE BLOCKS IS A BOUNDED PREVIEW ({len(sample_blocks)} excerpts shown). "
        f"The full DOM carries {nblocks} blocks with {total_text_chars} total characters "
        f"across all {len(pages)} pages. Truncation or omission beyond these preview excerpts "
        "is a PREVIEW LIMIT, never a parser defect. Judge text completeness from `blocks_total`, "
        "`text_chars_total`, and `page_count_dom` vs the source."
    )
    # First reference entries (D3) — labels + short text
    sample_refs = [
        {"label": r.get("label", ""), "text": (r.get("text") or "")[:120]}
        for r in refs[:6]
    ]

    # Table preview: bounded real-content sample so the "tables" metric is
    # actually evaluable. Dims alone score 0 "not evaluable" on any doc that
    # HAS tables (the judge has nothing to verify). Global sequential index
    # (per-page enumerate would re-label T1 across every page); header + first
    # two data rows, cell text truncated, total char-budgeted.
    def _cell_text(cell) -> str:
        if isinstance(cell, dict):
            return (cell.get("text") or "")[:_CELL_CHARS]
        return str(cell or "")[:_CELL_CHARS]

    table_preview: list[str] = []
    gi = 0
    used = 0
    for p in sorted(pages, key=lambda x: x.get("index", 0)):
        for t in p.get("tables", []):
            gi += 1
            header = t.get("header") or []
            rows = t.get("rows") or []
            hdr_txt = " | ".join(_cell_text(h) for h in header)
            row_txts = []
            for r in rows[:_TABLE_PREVIEW_ROWS]:
                cells = r.get("cells") if isinstance(r, dict) else r
                row_txts.append(" | ".join(_cell_text(c) for c in cells))
            seg = f"T{gi}:{len(rows)}r x {len(header)}c | {hdr_txt} | " + " ; ".join(
                row_txts
            )
            if used + len(seg) > _TABLE_BUDGET:
                break
            table_preview.append(seg)
            used += len(seg)
    tables_preview_note = (
        "TABLE PREVIEW IS A BOUNDED SAMPLE: cell text truncated to "
        f"{_CELL_CHARS} chars, at most {_TABLE_PREVIEW_ROWS} data rows/table, "
        f"{_TABLE_BUDGET} total chars. Only the tables/cells shown exist in the "
        "preview; truncation or absence beyond the shown text is a PREVIEW LIMIT, "
        "never a parser defect. Score 'tables' by judging ONLY the content shown "
        "against the SOURCE pages that contain them; do not penalize truncation."
    )
    return {
        "document_id": dom.get("document_id"),
        "source_hash": (dom.get("source_hash") or "")[:12],
        "metadata": {k: meta.get(k) for k in ("title", "page_count", "detected_type")},
        "page_count_dom": len(pages),
        "blocks_total": nblocks,
        "text_chars_total": total_text_chars,
        "tables_total": ntables,
        "images_total": nimages,
        "references_total": len(refs),
        "citation_index_size": len(citation_index),
        "reading_order_full_size": len(ro_full),
        "sample_page1_blocks": sample_blocks[:3],
        "sample_blocks": sample_blocks,
        "blocks_preview_note": blocks_preview_note,
        "sample_references": sample_refs,
        "tables_preview": table_preview,
        "tables_preview_note": tables_preview_note,
        "route": (dom.get("provenance") or {}).get("routing"),
    }


PROMPT_TEMPLATE = """You are a rigorous document-parsing quality assessor. Compare the SOURCE PDF text
with the PARSED DOM of the same document. The DOM was produced by an automated page-centric parser.
Judge SOURCE-vs-DOM correspondence only; never penalize the DOM for being shorter than source when the
shortness reflects genuine loss.

SOURCE PDF (partial preview: a subset of pages sampled across the document —
  missing pages are NOT errors, judge only the shown pages against the DOM):
{source_json}

PARSED DOM SUMMARY (real fields from the parser's canonical DOM):
{dom_json}

Return a JSON object with EXACTLY these keys:
- "metrics": {{"completeness": 0..1, "fidelity": 0..1, "structure": 0..1,
  "tables": 0..1 (use 0 when not evaluable), "references": 0..1, "scans_ocr": 0..1}}
  Where: completeness = how much of the source content is present in the DOM;
  fidelity = how faithfully text/numbers match the source (no fabrication, no loss);
  structure = reading order + sections + headings preserved;
  tables = table rows/headers/cells correct vs source;
  references = reference list + citation markers preserved;
  scans_ocr = OCR recovery quality on scanned pages.
- "verdict": "PASS" | "PASS_WITH_ISSUES" | "FAIL"
- "issues": [{{"severity": "critical"|"major"|"minor", "surface": "text"|"table"|"reference"|"structure"|"ocr",
     "detail": "specific concrete defect observed", "suggestion": "how to fix"}}]
- "notes": free text, max 3 sentences.

Be concrete and evidence-based. Only report issues you can point to from the SOURCE vs DOM
discrepancy. Output ONLY the JSON object (no markdown fences, no commentary)."""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pdf", required=True, help="source PDF path")
    ap.add_argument(
        "--dom",
        required=True,
        help="parsed DOM file (document.parsed.v1.docJSON) or any Document JSON",
    )
    ap.add_argument("--out", required=True, help="output verdict JSON path")
    ap.add_argument("--model", default=DEFAULT_MODEL, help="Gemini model name (light)")
    ap.add_argument(
        "--max-chars", type=int, default=12000, help="source text char budget"
    )
    ap.add_argument(
        "--api-key", default=None, help="Gemini API key (overrides env/key.py)"
    )
    args = ap.parse_args()

    key = resolve_key(args.api_key)
    if not key:
        print(
            "ERROR: no Gemini API key found. Set GEMINI_API_KEY env or create "
            "gitignored key.py with GEMINI_API_KEY='...'. Judge skipped (exit 3).",
            file=sys.stderr,
        )
        return 3

    pdf = Path(args.pdf)
    dom_file = Path(args.dom)
    if not pdf.is_file() or not dom_file.is_file():
        print(
            f"ERROR: need both --pdf and --dom files (pdf={pdf.is_file()} dom={dom_file.is_file()})",
            file=sys.stderr,
        )
        return 2

    dom = json.loads(dom_file.read_text(encoding="utf-8"))
    dom_sum = summarize_dom(dom)
    src = extract_source_text(pdf, args.max_chars)

    # Build a compact source preview (page-wide sampling already done upstream)
    src_budget = int(args.max_chars)
    src_parts: list[str] = []
    for idx in range(len(src["pages"])):
        chunk = src["pages"][idx][: src_budget // max(1, len(src["pages"]))]
        src_parts.append(f"[page {idx + 1} of {src['page_count']}] {chunk}")
    source_json = json.dumps(
        {
            "source_page_count": src.get("page_count") or len(src_parts),
            "preview_pages": [i + 1 for i in range(len(src_parts))],
            "pages": src_parts,
            "preview_note": "PARTIAL PREVIEW: only pages listed in preview_pages are shown, sampled across the document. Do NOT flag page-count differences as defects; judge fidelity/structure/tables/references ONLY on shown pages.",
        },
        ensure_ascii=False,
    )
    dom_json = json.dumps(dom_sum, ensure_ascii=False)

    prompt = PROMPT_TEMPLATE.format(source_json=source_json, dom_json=dom_json)

    try:
        import google.generativeai as genai

        genai.configure(api_key=key)

        # Build candidate model list starting with requested model then fallbacks
        candidate_models = [args.model]
        for fm in FALLBACK_MODELS:
            if fm not in candidate_models:
                candidate_models.append(fm)

        resp = None
        used_model = args.model
        last_exc: BaseException | None = None

        for current_model_name in candidate_models:
            model = genai.GenerativeModel(current_model_name)
            for attempt in range(1, _MAX_ATTEMPTS + 1):
                try:
                    resp = model.generate_content(prompt)
                    used_model = current_model_name
                    break
                except Exception as exc:  # noqa: BLE001 — classify rate-limit vs fatal
                    last_exc = exc
                    err_msg = str(exc)
                    # If daily quota is exhausted (PerDay), switch to next model immediately
                    if "PerDay" in err_msg or "per day" in err_msg.lower():
                        print(
                            f"  [judge] model {current_model_name} daily quota exhausted -> trying next model...",
                            file=sys.stderr,
                        )
                        break
                    if not _rate_limited(exc):
                        print(
                            f"  [judge] model {current_model_name} call failed: {exc}",
                            file=sys.stderr,
                        )
                        break  # try next fallback model
                    delay = min(25.0, max(3.0, _retry_delay(exc)))
                    if attempt < _MAX_ATTEMPTS:
                        print(
                            f"  [judge] {current_model_name} rate-limited (retry {attempt}/{_MAX_ATTEMPTS}); "
                            f"waiting {delay:.1f}s",
                            file=sys.stderr,
                        )
                        time.sleep(delay)
            if resp is not None:
                break
    except Exception as exc:  # noqa: BLE001 — import/configure fatal
        print(f"ERROR: Gemini setup failed: {exc}", file=sys.stderr)
        return 3

    if resp is None:
        # All retries exhausted on the same transient rate limit → tell the batch
        # driver this is a per-doc skip (exit 4), not a fatal key error (exit 3).
        print(
            f"ERROR: Gemini rate-limited after {_MAX_ATTEMPTS} attempts: {last_exc}",
            file=sys.stderr,
        )
        return 4

    text = resp.text.strip()
    if "```" in text:
        match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if match:
            text = match.group(1)
        else:
            match_start = re.search(r"```(?:json)?\s*(\{.*)", text, re.DOTALL)
            if match_start:
                text = match_start.group(1).rstrip("`")
    if not text.startswith("{"):
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            text = match.group(0)
    try:
        verdict = json.loads(text)
    except json.JSONDecodeError:
        print(f"ERROR: judge returned non-JSON:\n{text[:500]}", file=sys.stderr)
        return 1

    record = {
        "doc_id": dom.get("document_id"),
        "pdf": str(pdf),
        "model": used_model,
        "judged_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "dom_summary": dom_sum,
        "verdict": verdict,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"OK: verdict written to {out} — {verdict.get('verdict')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
