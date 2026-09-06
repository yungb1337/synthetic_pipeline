# Benchmark A/B — b01 (pre-B4) vs b02 (post-B4)

Same 36-PDF snapshot (`run-2026-09-04-parser-reliability/sources/pdf_snapshot_b01`), both run with
`--heavy-concurrency 1`, fresh out dirs (b02 → `parsed-b02`, no resume contamination).

| metric            | b01          | b02          | Δ               |
|-------------------|--------------|--------------|-----------------|
| files             | 36           | 36           | —               |
| assembly ok       | 16           | **36**       | +20             |
| failed            | 0            | 0            | —               |
| dead              | 0            | 0            | —               |
| partial           | 20           | 0            | −20             |
| unparsed          | 0            | 0            | —               |
| wall              | 504.8 s      | 599.5 s      | +94.7 s (retries)|
| peak RSS          | 4239 MB      | 4504 MB      | +265 MB         |
| blocks            | 2603         | **7287**     | +4679           |
| tables            | 27           | **82**       | +55             |
| refs              | 0 on partials| populated    | +               |
| ro_full           | 0 on partials| populated    | +               |

## What changed between the runs
**B4** (`DoclingConvertError` + distinct retryable category) plus the already-landed B1 (never
let a FAILED re-parse clobber a durable OK page) and B2 (skip futile retry only on genuine
`engine_unavailable`). Full detail: `docs/parser-fixes-implementation-summary.md` Wave 5.

## Why b02 succeeded where b01 lost 20 docs
Both runs recorded the SAME transient memory-failure class under paging-file pressure:
- 67× `std::bad_alloc` (docling preprocess), 30× `bad allocation` (onnxruntime), ONNX Sigmoid/
  Concat node OOM, a numpy "Unable to allocate 14.0 MiB".

In b01 these were swallowed by `convert_path` → surfaced as `engine_unavailable` → the B2 skip
did not retry them → 67+ pages dead-lettered → 20 docs `partial`.

In b02 the same failures are raised as typed `DoclingConvertError` → categorized `docling_convert`
(retryable) → the assembler's retry pass re-runs the page once memory pressure has eased → success.
**Every recoverable page recollected; every doc assembled OK.**

## Production takeaways
1. B4 does not remove OOM events — it makes the pipeline **self-healing**: a transient page-level
   failure is retried, not confused with an engine outage. On this 16.5 GB-RAM box, transient
   `bad_alloc` under paging-file pressure is the NORM, so self-healing is the correct posture.
2. Recovered content is significant: +4679 blocks, +55 tables vs b01 — i.e. b01's 20 partial docs
   were NOT "low-content docs", they were docs whose pages we silently failed to map.
3. Memory remains the single biggest production risk for the heavy (docling) route on low-RAM hosts;
   heavy concurrency must stay 1 here. Options to revisit later: per-page convert already bounds the
   C++ heap; a RAM fsync/quiesce before each heavy page, or processing docling pages strictly
   serially with a synchronized memory guard between pages.
4. Both runs emitted identical error classes — b02's errors.md is NOT quieter; its DUT is better
   (retry semantics). Do not judge pipeline health by error-signal count in isolation; judge by
   final assembly status + content recovered.