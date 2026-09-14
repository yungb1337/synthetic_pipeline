# Parser Throughput Report — Post-Fix Measurement, Tradeoffs & Architecture Comparison

**Date:** 2026-09-14 · **Corpus:** 100 PMC PDFs (`checkpoints/run/run-2026-09-04-parser-reliability/sources/pdf`, `--limit 100`) — the exact document set of the pre-fix baseline. **Machine:** 16 CPUs, 15.4 GB RAM. **Route mix (verified identical in all runs):** 93 docling-band docs (1,170 pages) + 7 enrichment-band docs (127 pages) = **1,297 pages**.

**Method note:** Run A is a like-for-like reproduction of the baseline's exact invocation (`--heavy-concurrency 1`, fresh store). Run B uses the post-fix auto-derived heavy concurrency (ResourceGovernor → 4 on this box). Dependency versions are frozen since 2026-09-04 (verified via `importlib.metadata`), so the pre-fix baseline was measured on the same venv.

---

## 1. Headline: measured throughput gains

| Metric | Baseline (pre-fix, 09-08) | Run A (post-fix, heavy=1) | Run B (post-fix, auto F=4) |
|---|---|---|---|
| Wall time (100 docs) | 2,841.7 s (47.4 min) | **1,699.1 s (28.3 min)** | **1,613.4 s (26.9 min)** |
| Throughput (pages/s) | 0.456 | **0.763 (1.67×)** | **0.804 (1.76×)** |
| Throughput (docs/s) | 0.0352 | 0.0588 (1.67×) | 0.0620 (1.76×) |
| Mean time / page | 2,191 ms | 1,303 ms | 1,238 ms |
| Peak tree RSS | 2,600 MB | 3,498 MB (+34%) | 4,476 MB (+72% vs baseline, +28% vs A) |
| Yield: blocks / tables / refs | 19,231 / 317 / 61 | 19,226 / 306 / 61 | 19,226 / 306 / 61 |
| Failures / dead / unparsed | 0 / 0 / 0 | 0 / 0 / 0 | 0 / 0 / 0 |

**Per-band attribution** (computed from per-doc rows of each report):

| Band | Pages | Baseline ms/page | Run A | Run B | Speedup A | Speedup B |
|---|---|---|---|---|---|---|
| Enrichment (native path) | 127 | 399.6 | 254.8 | 243.9 | **1.57×** | **1.64×** |
| Docling (heavy path) | 1,170 | 2,385 | 1,425 | 1,353 | **1.67×** | **1.76×** |

**Fidelity of the yield deltas (important):** the −11 tables vs baseline (317 → 306) and ±5 blocks are **not regressions** — they were verified at source level during this review cycle. Ground-truth sequential PyMuPDF passes showed the *pre-fix* table counts were race-contaminated: pre-fix code ran `find_tables`/`get_text` concurrently on a shared, unsynchronized fitz handle, producing non-deterministic counts (same document yielded 0/3/4/7/11 tables across fresh pre-fix runs) including junk tables (None headers, 59-row garbage, 0-row fragments). Post-fix output is deterministic across runs and matches ground truth on 3 of 4 previously-suspect documents; the one delta (PMC12136301, 10→9 tables on page 7) is the D2 continuation-merge receiving clean input for the first time — cell-level inspection confirmed **zero unique content loss** (the "missing" cells are header labels stored in the `header` field by design). Refs are byte-identical (61 = 61).

---

## 2. Where each gain comes from (fix → measured effect)

**Code-attributable, measured on this corpus:**
- **I-01 + I-04 (enrichment band, 1.57–1.64×):** per-document native locking unlocked cross-document parallelism, and engine reuse removed the O(N²) per-page PDF reopen + document-wide median re-scan. Every one of the 7 enrichment docs got faster (e.g. PMC12233009: 983 → 469 ms/page; PMC12309142: 777 → 422).
- **I-06, I-07, I-09 (all 1,297 pages):** one hash + one detection per document (was 2 hashes / 2–3 detects), no read-before-write in `PageStore.put_page`, and blob release after durable persist. Individually small per page; over 1,297 pages they compound.
- **I-02 (Run B):** honest RAM-derived heavy concurrency (auto → 4 on this box) delivered +5% wall on this corpus and is the mechanism that would deliver ~4× on a native/enrichment-heavy corpus (it is capped by the docling-heavy mix here).

**Environment-attributable (flagged honestly):** a large share of the docling-band gain at identical `--heavy-concurrency 1` (1.67×) is likely box-condition drift — the baseline started with 3.67 GB pagefile committed and 5.55 GB free; Run A started with 2.82 GB committed / 3.87 GB free. Per-doc wins on the docling band are broad but not uniform (some docs slower: PMC10088797 1,859 → 2,759 ms/page), which is the signature of environment noise rather than a uniform code effect. The defensible claim: **≥1.57× (enrichment band, purely code-attributable) real gain; overall 1.67–1.76× measured**.

**No throughput change expected or measured (and why that's correct):** I-03 (retry containment — zero retries fired in any run), I-05 (converter cache — no per-item overrides in this corpus), I-08 (journal race — single-writer patterns here), I-10 (OCR call lock — OCR contention not exercised), I-13 (metrics — pure observability), I-14 (dead-code removal). These fixes are reliability/observability value, not speed.

---

## 3. Tradeoffs introduced by the fixes

| Fix | Benefit (measured/expected) | Cost / risk | Mitigation in place |
|---|---|---|---|
| **I-01** per-document native locks | Cross-document native parallelism; enrichment band 1.57× | Same-document pages still serialize (correctness preserved); more fitz handles open concurrently across batch threads; **peak RSS +34% at heavy=1** (2,600 → 3,498 MB) — parallelism converts idle CPU into resident memory | Handle eviction/close under per-doc lock; zero-silent-loss gate unchanged; determinism verified (pre-fix was racy, post-fix is not) |
| **I-02** RAM-derived heavy concurrency | Auto F=4 on 16 GB; heavy band scales by hardware instead of being pinned at 1 | +28% RSS vs Run A (4,476 MB); governor is deliberately conservative (downward-only recheck, floor 1 without psutil) | 2.5 GiB/worker budget; explicit `--heavy-concurrency` override wins; OOM hazard contained by max_tasks_per_child recycling |
| **I-03** retry containment in pools | Eliminates concurrent in-process Docling (the `std::bad_alloc` exposure) | Retries pay queue-wait latency; adds `scheduler` coupling to `Assembler` | Backward-compatible constructor (`scheduler=None` → legacy path); B2 engine-unavailable skip applied before dispatch |
| **I-04** enrichment engine reuse | O(N²) → O(N); 13.5 s redundant native work eliminated on a 30-page doc | Long-lived per-document handle state (was fresh-handle-per-page) | Handle is per-document and closed with the engine; ground-truth table fidelity verified post-change |
| **I-05** keyed converter cache | Overrides actually honored; no rebuild per variant | Cache keyed on `(ocr, table_mode, gpi)` — a 4th override dimension would need a key change; memory per cached variant | Tests cover key-vs-build resolution; module-level bounded by the 3-tuple space |
| **I-06** dedupe hash/detect | 1 hash + 1 detect per extract (was 2 hashes, 2–3 detects) | `SourceScan.scan` now trusts caller-supplied results — a caller passing wrong values bypasses re-verification | Batch executor passes the sha it computed itself; standalone CLI path still self-computes |
| **I-07** status probe instead of read-before-write | One regex probe vs full JSON read+parse per page write | Probe depends on the `to_json` status serialization format | Format is covered by existing serialization tests |
| **I-08** journal lock (`_jl`) | No lost appends / wiped consolidation under duplicate-content races | One mutex on the journal hot path (negligible at observed rates) | Shared `_fold_page_record` keeps fold semantics byte-equivalent (asserted by tests) |
| **I-09** blob strip after persist | Bounded per-document RSS during extraction | Image-bearing pages are re-read from the page store at fold (extra I/O only for image pages) | `put_image` receives identical bytes via reload (proven by test); images are a minority of pages |
| **I-10** OCR `_call_lock` | Thread-safe RapidOCR under the widened native pool | OCR calls serialize per process — fine, OCR is inherently per-process state | Lock scope is one call, not the engine lifetime |
| **I-12** psutil-missing floor | No fabricated 16 GiB assumption on unknown boxes | Under-utilizes on boxes where psutil is absent | Documented; explicit override available |
| **I-13** metrics events | Percentiles, band mix, RSS, pool utilization per run — future changes become measurable | Small event volume per plan run; percentile buffers retained in memory | Bridge sink is fail-open (`except Exception: pass`) — metrics can never break parsing |

**Net tradeoff, one line:** the parser traded ~0.9–1.9 GB of peak RSS for 1.67–1.76× throughput and — more importantly — for determinism (pre-fix output was race-contaminated; post-fix is reproducible), with every reliability gate (zero-silent-loss, per-page isolation, dead-letter, manifest resume) untouched.

**Watch item (observed, not yet actionable):** Run A's per-doc `peak_worker` climbs monotonically across the run (2,407 → 3,469 MB). On very long runs (10⁵–10⁶ docs) this pattern warrants periodic worker-process recycling; the new I-13 `rss_mb` metric is the hook that would trigger it. No change made now.

---

## 4. Production architecture comparison

Current throughput baseline for comparison: **0.76–0.80 pages/s on one 16-CPU/16 GB box**, bottlenecked ~93% by the docling band (1,170 of 1,297 pages), with the heavy pool RAM-capped at ~4 workers here (2.5 GiB/worker budget).

| # | Architecture | Expected throughput (this corpus class) | Scaling behavior | RAM cost | Ops complexity | Failure isolation | Verdict |
|---|---|---|---|---|---|---|---|
| A | **Current:** threaded batch pool + per-doc native locks + RAM-governed heavy process pool | 0.76–0.80 pages/s (measured) | Scales with cores until heavy-pool RAM cap (~4 workers/16 GB, ~7/32 GB) | 3.5–4.5 GB peak | None (single process) | Per-page isolation + dead-letter + resume | ✅ Right default; cheapest path to the measured 1.76× |
| B | **Multi-process sharding, one box** (2–3 × `parse_folder` on manifest shards) | ~1.5–2.2 pages/s (near-linear for native band; heavy band must be partitioned: 2 procs × F/2) | Linear until RAM: each shard re-pays model memory | ~2× of A | Low — manifest already makes runs incremental and overlap-safe; no code change | Same as A per shard | ✅ **Best next step** for CPU-bound growth; zero code |
| C | **Multi-box sharding** (shared/NFS corpus, one parser per box) | ~N × 0.8 pages/s (4 boxes ≈ 3.2 pages/s ≈ 7 min for this corpus) | Linear in boxes; corpus distribution is the friction | N × 4 GB | Medium — orchestration, corpus sync, result merge | Strong (box-level) | ✅ Obvious horizontal path; idempotent sha256 manifest makes overlap-free distribution trivial |
| D | **Broker queue + stateless workers** (Redis/RabbitMQ/SQS + object store) | Same per-worker as A/C; elasticity is the win | Elastic to millions of docs; workers auto-scale | Per worker | **High** — new infra, at-least-once semantics, observability stack | Best (visibility, retries, backpressure, independent worker versions) | ⏸ Correct at 10⁵–10⁷ docs/day; premature today. Compatible: the parser is already idempotent (sha256 manifest, resume ledger) |
| E | **Docling microservice / model server** (heavy band split out; native band stays in-process) | Native band no longer competes with heavy pool; docling workers scale independently — the 93% bottleneck gets its own fleet | Heavy band scales independently of native band; version pinning per service | Docling RAM isolated from batch workers | Medium-high — IPC, PDF transfer, service SLOs | Best for the heavy band (a docling crash/recycle never takes down batch workers) | ⏸ Highest-leverage *split* if heavy-band scaling or recycling (monotonic RSS watch item) becomes a problem |
| F | **GPU-accelerated docling** (same topology, GPU workers for the heavy pool) | 2–5× per heavy worker (vendor/model dependent) → single box potentially ~10–15 min for this corpus | Per-worker multiplier on the dominant band | GPU VRAM budget replaces the 2.5 GiB/worker RAM budget | Medium (drivers, device pinning) | Same as A | ⏸ **Must be gated on golden-output parity**: layout models can differ slightly per device — the zero-silent-loss gate and D2 checks are the gate |

**Reading the table:** on this corpus the binding constraint is the docling band, so throughput grows fastest in order: B (free) → C (linear, ops cost) → E (structural fix for the bottleneck) → F (per-worker multiplier). D is an orchestration layer that becomes worth its cost only at fleet scale, and it composes with any of B–F.

## 5. Non-negotiables (kept intact through all 14 fixes)

- **Zero-silent-loss gate:** a document is `parsed` only if the assembled page set equals the expected page set. Not traded for throughput in any fix.
- **Per-page isolation:** one bad page can dead-letter, never poison a document or the run (0 dead / 0 failed across 2,594 pages parsed in the two post-fix runs).
- **Determinism:** post-fix output is reproducible run-to-run (verified: two fresh runs produced identical per-doc yields), where pre-fix was not.
- **Idempotent resume:** sha256 manifest + page ledger mean every architecture above (B–D) inherits checkpoint/resume for free.

## 6. Bottom line

- **Measured:** 1.67× overall at identical settings (1.57× on the band where the gain is purely code-attributable), 1.76× with the I-02 auto-governor enabled — for a documented, bounded RSS increase.
- **Cost paid:** RSS (+34% / +72% vs baseline), retry queue-wait, a mutex on two hot paths, and caller-trust in two seams (I-06) — each with a test or gate covering the risk.
- **Quality dividend not visible in pages/s:** the pre-fix baseline's table counts were race artifacts; the post-fix parser is deterministic and ground-truth-accurate.
- **Recommended scaling path:** shard processes on the box (B, zero code) → shard across boxes (C) → split docling into its own service (E) when heavy-band independence or the RSS-recycling watch item demands it → GPU (F) only behind golden-output parity. Queue-based orchestration (D) waits for fleet scale.
