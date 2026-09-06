# Knowledge Step Notes — b01 → b02 benchmark campaign

Per-step learnings captured while diagnosing/verifying the parser's real-corpus failure
modes (run-2026-09-06-parser-b1b2-fixes). Append, never delete.

---

## Step 1 — Measure before trusting "engine unavailable"

Symptom: `run-2026-09-04` showed mass `FAILED engine_unavailable` pages, and b01 (heavy=1)
dead-lettered 20/36 docs to `partial`. Everyone's instinct was "the docling engine is broken".

**Check:** re-run one of the "dead" pages in-process with RAM free → it converted **SUCCESS** in
~17s. The engine was fine; the LABEL was lying.

**Knowledge:** `engine_unavailable` is a *diagnosis*, not a raw observation. It was being set
for two unrelated causes (engine None vs per-page convert exception) because `convert_path`
collapsed both to `return None`. If a failure class conflates "outage" with "one bad page",
your retry logic can't distinguish "retry useful" from "retry futile". Split the signals.

## Step 2 — B1 (durability): never let a FAILED re-parse clobber a durable OK page

A transient page failure during a later batch must rescue the prior durable OK `PageResult`,
not overwrite it. Page ledger = source of truth; FAILED/DEAD over OK is data loss (ADR-013:
zero silent page loss).

## Step 3 — B2: skip futile retry only on genuine engine_unavailable

Retrying 67 pages whose engine can't build is pure I/O+CPU spin. `_retry_pages` skips only the
`engine_unavailable` category. This is only CORRECT once B4 makes that category strict.

## Step 4 — B4 (root cause): typed `DoclingConvertError`; None == engine_down only

See `docs/parser-fixes-implementation-summary.md` Wave 5. The observability principle: a failed
CALL is different from a failed ENGINE, and both are different from a failed PAGE. Collapsing
them hides where recovery can happen.

## Step 5 — A/B result (the empirical proof)

Same 36-PDF snapshot, both at heavy=1:

| metric | b01 (pre-B4) | b02 (post-B4) |
|--------|-------------|---------------|
| ok     | 16/36        | **36/36**      |
| partial| 20           | 0             |
| blocks | 2603         | **7287**      |
| tables | 27           | **82**        |
| error-signal stream | 130+ bad_alloc | same class 153 entries |

**Both runs hit the same transient `std::bad_alloc` / `bad allocation` class under paging-file
pressure.** b01 let it kill pages; b02 retried and recovered them. *The error stream did not get
quieter — the pipeline got self-healing.* Judge health by final assembly + content, not by raw
error-signal count.

**Memory note:** 16.5 GB-RAM Windows box, paging-file exhaustion under load. heavy concurrency
must stay 1; parse / judge / download strictly sequential.

## Step 6 — What I noticed vs what the judge says (placeholder↺)

Append per-doc judge findings; watch the known false-negative: references present as text blocks
but absent from the structured `references` field → judge scores references low — a *metric*
artifact, not necessarily a parser bug.
## Step 7 — The tables metric was lying, and the judge told us *how*

The b02 judge summary showed `tables mean 0.631`, the only red metric. Digging in:
- 12 docs scored `tables=0.0`. Cross-referencing the parser's own store proved the
  parser was fine: one doc had **7** parsed tables, scored 0.
- Splitting the 12: **8** truly had `tables_total=0` in the DOM (correct 0 =
  "not evaluable"), but **4** had `tables_total>0` (1..7 tables) and still scored 0.

**Root cause (judge input, not parser):** `summarize_dom` gave the model only a
dims string (`T1:6r x 3c`) — no cell content. The prompt says `tables: 0..1 (use 0
when not evaluable)`, so with nothing to verify, the model defaulted to 0. Latent
bug: the dims lambda used **per-page `enumerate`** → every page's first table was
labeled T1.

**Fix:** `summarize_dom` now sends `tables_preview` = real header cells + first two
data rows per table (24-char cell truncation, 1200-char budget), a **global**
sequential T-index, and an explicit `tables_preview_note` that truncation is a
preview limit, never a defect.

**Empirical proof (4 re-judged docs):** tables went 0.0 → 1.0 / 1.0 / 0.9 / 0.95.
The metric now isolates parser quality. The judge's own notes made the artifact
visible — after the fix the only remaining "table issue" text was the model echoing
the truncation, which the note eliminates.

**Knowledge:** a metric that can only say "0 when not evaluable" will read as a
product defect on every table-bearing doc unless the measurement input actually
contains the thing being measured. Before chasing the parser, always check what
the *judge's input* (the DOM summary) actually exposes — I verified this by reading
a stored judgment's `dom_summary` and seeing `tables_preview_note` vs the old
`table_dims_preview`. Metric artifacts look identical to real failures until you
inspect the input; the discrimination is cheap and worth doing first.

## Step 6.5 — Judge tooling: Gemini free-tier quota is real, key was fine

16/36 docs judged PASS/PASS_WITH_ISSUES (fidelity 0.95–0.99), then `429 generate_content_free_tier_requests limit=15` per minute on
gemini-3.5-flash-lite. Two tooling defects surfaced (fixed):
1. `llm_judge.py` returned exit 3 on a 429 → batch driver mislabeled it "API key missing" and aborted the remaining 20.
2. No pacing → burst past the per-minute ceiling.
Fix: retry-on-429 honoring the API's `retry in Ns` (bounded, exit 4 = rate-limited), batch exit-4 skip-and-continue, --pacing 4s.
Lesson: **the API key and quota are separate failure axes**; a 429 is per-doc transient, a key error is per-run fatal. Exit codes must say which.
