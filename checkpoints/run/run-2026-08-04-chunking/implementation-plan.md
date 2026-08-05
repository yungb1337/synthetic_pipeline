# Implementation Plan — run-2026-08-04-chunking (Module #3: Semantic Chunking + BGE-M3 wiring)

**Author:** Technical Planner · **Date:** 2026-08-05 · **Run:** `run-2026-08-04-chunking`
**Source:** `checkpoints/run/run-2026-08-04-chunking/architecture.md` (status: `ARCHITECTURE: APPROVED`), ADR-009, ADR-010, `project_memory/active_objective.md`.
**Claim labels:** Fact | Research | Inference | Recommendation — applied throughout.

---

## PLAN: READY

---

## 0. Executive summary

The approved architecture decomposes into **16 tasks** across three tracks:

- **Track A — Embedding follow-ups (non-blocking, architect-flagged, small):** tighten `SentenceTransformerEmbedder.name` to carry model identity + dtype + amend the `Embedder` protocol docstring per ADR-010 (A1); fix `tests/test_sbert_embedder.py` bge-small → bge-m3 and assert name identity (A2); lower `EmbeddingOptions.batch_size` 128→32 and `ProcessingConfig.embed_batch_size` 64→32 (A3).
- **Track B — New module `app/chunking/`:** package skeleton (B0), `config.py` (B1), `schema.py` (B2), `tokenize.py` (B3), `sentences.py` (B4), `chunker.py` (B5), `batching.py` (B6), `store.py` (B7), `pipeline.py` (B8), `cli.py` (B9), `__init__.py` exports (B10).
- **Track C — Real-BGE gated test + memory:** cosine-stable two-run equality on the real model (C1); `module_status.md` update (D1).

All tests are hermetic via `DummyEmbedder` except C1 (gated on model + tokenizer availability). No parser/normalizer/processing/docling redesign — the only edits outside `app/chunking/` are A1–A3 (the architect-flagged follow-ups in `app/embedding/` + `app/processing/config.py`).

---

## 1. Architecture → task map (coverage check)

| Architecture element | Plan task(s) |
|---|---|
| `app/chunking/` module layout (§2) | B0, B10 |
| `config.py` — `ChunkingConfig` (frozen, versioned, snapshot) | B1 |
| `schema.py` — `Chunk`, `ChunkProvenance`, `ChunksArtifact`, content-addressed `chunk_id` (§3.1) | B2 |
| `tokenize.py` — `TokenCounter` pinned BGE BPE + char/4 fallback, `tokenizer_ref_hash` (§3.4) | B3 |
| `sentences.py` — `split_sentences()`, `tail_sentences()` (§3.5) | B4 |
| `chunker.py` — `SemanticChunker.chunk()` DOM-anchored walk, cut/merge/oversized/anchor/overlap (§3.2, §3.3) | B5 |
| `batching.py` — `group_by_token_budget()` ≤16k tokens / ≤32 texts (§3.9) | B6 |
| `store.py` — `ChunkStore` ABC + `FilesystemChunkStore`, versioned keys, `_version_suffix` duplicate, `embedder_id` sanitize (§3.6, §3.7) | B7 |
| `pipeline.py` — `ChunkEmbedPipeline` chunk→persist→embed-only-missing→persist, validation stamp, event (§3.8) | B8 |
| `cli.py` — thin CLI (§2) | B9 |
| Tighten `SentenceTransformerEmbedder.name` (§3.10, ADR-009) + ADR-010 protocol docstring | A1 |
| Fix `tests/test_sbert_embedder.py` bge-small → bge-m3 (§8, tracked) | A2 |
| Lower `EmbeddingOptions.batch_size` / `ProcessingConfig.embed_batch_size` → 32 (§3.9, §8) | A3 |
| Real-BGE gated test: cosine-stable two-run equality + dim/dtype (§7) | C1 |
| `module_status.md` (Knowledge-Curator-owned, updated by implementer) | D1 |

---

## 2. Task list (dependency-ordered)

### Track A — Embedding follow-ups (architect-flagged, non-blocking for B1–B7; A1 required before B8/C1 for correct keys)

### A1. Tighten `SentenceTransformerEmbedder.name` + amend `Embedder` protocol docstring (ADR-010)

- **Files:** `app/embedding/sbert.py`, `app/embedding/embedder.py`
- **Dependencies:** none. **Blocks:** A2, B8 (correct `emb-` keys with real BGE), C1.
- **Expected behavior (Fact, architecture §3.10, ADR-009/010):**
  - `SentenceTransformerEmbedder.name` becomes a **property** returning `f"{model_id}@{revision}-{dtype}"`, e.g. `BAAI/bge-m3@local-fp16`, where:
    - `model_id` = the resolved model identity (`BAAI/bge-m3`, or the local dir name when `_local_ref` resolves to `models/bge-m3` — use the **HF identifier**, not the filesystem path, so the name is canonical).
    - `revision` = resolved deterministically from the local model dir when present: prefer a pinned `revision.txt`/git commit if present, else `sha256(config.json bytes)[:8]` (deterministic, local, on-prem). When only the HF identifier is used (no local dir), `revision = "local"` is acceptable **only** if the model dir was the source; if truly remote, use the pinned HF revision string. **Decision to remove ambiguity:** use `sha256(config.json bytes)[:8]` when `models/<safename>/config.json` exists, else `"local"`. Do **not** read remote state.
    - `dtype` = `"fp16"` when `self.fp16` else `"fp32"`.
  - The generic class attribute `name = "sentence-transformers"` is **removed** (no test may depend on it).
  - `app/embedding/embedder.py` `Embedder` protocol docstring amended per ADR-010: determinism is **per-path** — bit-exact for CPU/`DummyEmbedder`; **cosine-stable** (L2-normalized, cosine ≥ 0.9999 vs a canonical re-embed) for GPU-fp16. State that `name` must carry model identity + dtype.
  - `DummyEmbedder.name` (`"dummy-feature-hash"`) is unchanged (already identity-bearing, Fact).
- **Tests:** add `tests/test_sbert_embedder.py::test_name_identity` — `"bge-m3" in embed.name` and `("fp16" in embed.name or "fp32" in embed.name)` (model may or may not run fp16; accept either dtype tag). Assert `embed.name != "sentence-transformers"`.
- **DoD:** `name` carries model identity + dtype + revision; old generic name is gone; protocol docstring documents cosine-stable policy; `pytest tests/test_sbert_embedder.py -q` passes (or skips when torch absent).

### A2. Fix `tests/test_sbert_embedder.py` fixture model (bge-small → bge-m3)

- **Files:** `tests/test_sbert_embedder.py`
- **Dependencies:** A1 (assertion reads `embed.name`). **Blocks:** nothing (parallel with Track B).
- **Expected behavior (Fact, tracked in architecture §8/§7):** the module fixture loads `BAAI/bge-small-en-v1.5`; the production embedder is BGE-M3. Change the fixture to `SentenceTransformerEmbedder(model="BAAI/bge-m3")`. Keep the skip pattern (`_available()` checks sentence_transformers + torch import). Keep existing assertions (`test_dims`, `test_deterministic`, `test_similar_to_cosine_positive`).
- **DoD:** fixture uses `BAAI/bge-m3`; suite green or skipped on model-less machines.

### A3. Lower batch defaults: 128/64 → 32

- **Files:** `app/embedding/factory.py` (`EmbeddingOptions.batch_size`), `app/processing/config.py` (`ProcessingConfig.embed_batch_size`), comments updated to explain the 4 GB fp16 rationale.
- **Dependencies:** none. **Blocks:** nothing. **Parallel:** Track B.
- **Expected behavior (Fact, architecture §3.9 decision):** `EmbeddingOptions().batch_size == 32`, `ProcessingConfig().embed_batch_size == 32`. The chunk pipeline never inherits these (it passes its own token-budget caps) — this change only stops the generic seams advertising OOM-risky defaults.
- **Note (flagged, not a design change):** `app/embedding/runner.py batch_embed`'s default (`64`) is **not** in the architect's required-follow-up list; the chunk pipeline always passes an explicit `batch_size`, so the default is never hit on the chunk path. Do **not** change it unless the engineer confirms no other caller relies on it — out of scope, flagged for the architect.
- **Tests:** none required (verified: no existing test asserts the old defaults — Fact from grep of `tests/`). Run the embedding + processing suites to confirm green.
- **DoD:** defaults lowered; `tests/test_embedding.py tests/test_processing.py -q` green.

---

### Track B — `app/chunking/` module

> Style rules for every file (Fact from existing modules): config = frozen `dataclass` with `snapshot()` (`app/parser/config.py`, `app/normalizer/config.py`); schema = pydantic `BaseModel` (`app/parser/dom/models.py`); storage = ABC + `Filesystem*` impl returning keys (`app/parser/storage.py`); events via `app/parser.events.EventPublisher`; module docstring explaining the trust boundary (idempotent/deterministic/faithful/on-prem).

### B0. Package skeleton

- **Files:** `app/chunking/__init__.py`
- **Dependencies:** none. **Blocks:** B1–B9.
- **Expected behavior:** `app/chunking/` imports as a package; `__init__.py` carries the module docstring (projection, consumer of DOM + `Embedder` protocol + `EventPublisher`; no dependency on normalizer/processing internals) and `__version__ = "0.1.0"`. Imports/exports are added in B10.
- **Tests:** none (package import is exercised by every later test).
- **DoD:** `import app.chunking` succeeds.

### B1. `app/chunking/config.py` — `ChunkingConfig`

- **Files:** `app/chunking/config.py`
- **Dependencies:** B0. **Blocks:** B5, B8.
- **Expected behavior (Fact, architecture §3.3 table + §3.9):**
  ```python
  @dataclass(frozen=True)
  class ChunkingConfig:
      chunker_version: str = "chunker-v0.1.0"
      dom_schema_version: str = "dom-schema-v0.1.0"
      target_tokens: int = 400
      min_band_tokens: int = 256
      soft_max_tokens: int = 768
      hard_max_tokens: int = 2048
      overlap_tokens: int = 48
      overlap_at_heading_seams: bool = True
      max_tokens_per_call: int = 16384
      max_texts_per_call: int = 32
      tokenizer_mode: str = "bge-m3"          # preference; actual mode recorded when resolved
      allow_char4_fallback: bool = True
      tokenizer_path: str = "models/bge-m3/tokenizer.json"
      def snapshot(self) -> dict: ...         # all non-underscore fields, JSON-safe
  ```
- **Tests (`tests/test_chunking.py`):** `test_config_defaults` (values match the architecture table), `test_config_snapshot` (round-trips to JSON, includes chunker_version).
- **DoD:** defaults exactly as above; `snapshot()` returns the full field set.

### B2. `app/chunking/schema.py` — models + content-addressed `chunk_id`

- **Files:** `app/chunking/schema.py`
- **Dependencies:** B0. **Blocks:** B5, B6, B7, B8.
- **Expected behavior (Fact, architecture §3.1):**
  - `ChunkProvenance(BaseModel)`: `chunker_version: str`, `chunker_params: dict`, `dom_schema_version: str`, `normalizer_version: str | None = None`, `dom_storage_key: str = ""`, `tokenizer: str`, `tokenizer_ref_hash: str | None = None`, `forced_split: bool = False`.
  - `Chunk(BaseModel)`: `chunk_id: str`, `doc_id: str`, `seq: int`, `kind: str` (documented set: `paragraph|heading|list_item|code|formula|caption|mixed` + **reserved** `table_atomic|figure_caption`), `text: str`, `source_block_ids: list[str]`, `overlap_source_chunk_id: str | None = None`, `page: int = 0`, `pages: list[int]`, `heading_anchor: str = ""` (metadata only, never embedded), `parent_chunk_id: str | None = None` (reserved), `token_count: int = 0`, `char_count: int = 0`, `tokenizer: str`, `order_source: str = "reading_order"`, `provenance: ChunkProvenance`, `embedding_ref: str = ""`, and **reserved** `source_table_ids: list[str] | None = None`, `source_image_ids: list[str] | None = None` (reserved for the atomic-table next step; must exist so the step lands without a schema bump).
  - `ChunksArtifact(BaseModel)`: `schema: str = "chunks-v1"`, `doc_id: str`, `chunker_version: str`, `dom_storage_key: str = ""`, `chunks: list[Chunk]`, `report: dict`.
  - Module-level `compute_chunk_id(doc_id: str, text: str, source_block_ids: list[str]) -> str`:
    ```python
    payload = json.dumps(
        {"doc_id": doc_id, "text": text, "source_block_ids": source_block_ids},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
    ```
    Canonical JSON = `sort_keys=True`, UTF-8, no whitespace → identical bytes every run (**Fact**). `chunk_id` **excludes** `seq`, `heading_anchor`, `chunker_version`, `embedding_ref`; **includes** `source_block_ids` (lineage pinned to source bytes).
- **Tests (`tests/test_chunking.py`):** `test_chunk_id_stable` (same inputs → same id; `seq`/`heading_anchor`/`chunker_version` do not affect it), `test_chunk_id_changes_with_text_or_blocks` (text change → id change; block-list change → id change), `test_schema_roundtrip` (model_dump_json → validate → equal), `test_reserved_fields_present` (`parent_chunk_id`, `source_table_ids`, `source_image_ids` are valid fields; reserved kinds serialize).
- **DoD:** models match §3.1 field-for-field; `compute_chunk_id` is the exact sha256-over-canonical-JSON rule; tests prove stability/exclusion.

### B3. `app/chunking/tokenize.py` — `TokenCounter`

- **Files:** `app/chunking/tokenize.py`
- **Dependencies:** B0. **Blocks:** B4, B5, B6, B8.
- **Expected behavior (Fact, architecture §3.4):**
  - `class TokenCounter` with constructor `(mode: str = "bge-m3", tokenizer_path: str = "models/bge-m3/tokenizer.json", allow_char4_fallback: bool = True)`.
  - **Primary `bge-m3`:** load the pinned tokenizer once, lazily, via the `tokenizers` library (`Tokenizer.from_file(tokenizer_path)` — already a sentence-transformers dependency, **Fact**). `count(text) = len(encode(text).ids)`. `tokenizer_ref_hash = sha256(tokenizer.json bytes)`.
  - **Fallback `char4`:** `count(text) = max(1, len(text) // 4)` — deterministic, dependency-free. Used when the tokenizer file is absent **or** load fails and fallback is allowed.
  - Expose `tokenizer: str` property (`"bge-m3" | "char4"` — the **actual** mode) and `tokenizer_ref_hash: str | None`; both go into `ChunkProvenance` so counts are never silently mixed.
  - Resolved once per pipeline run — one shared instance for all chunking + batching decisions (architecture invariant).
- **Tests (`tests/test_chunking.py`):** `test_char4_deterministic` (forced `char4` mode: same text → same count; `max(1, len//4)`; provenance mode `"char4"`), `test_char4_fallback_on_missing_file` (nonexistent path + fallback=True → mode `"char4"`, no exception), `test_bge_mode_when_available` (skip-if `models/bge-m3/tokenizer.json` missing: mode `"bge-m3"`, `tokenizer_ref_hash` is 64-hex, monotonic-ish count for a longer string), `test_count_deterministic` (two counts equal).
- **DoD:** `count()` deterministic in both modes; actual mode + ref hash exposed; no model/torch import required for `char4` (hermetic).

### B4. `app/chunking/sentences.py` — deterministic sentence splitter

- **Files:** `app/chunking/sentences.py`
- **Dependencies:** B3 (`tail_sentences` needs a `TokenCounter`). **Blocks:** B5.
- **Expected behavior (Fact, architecture §3.5):**
  - `split_sentences(text: str) -> tuple[list[str], int]` — returns `(sentences, split_ambiguous)`:
    - Split on sentence-final punctuation (`.`, `!`, `?`, `。`, `！`, `？`, `…`) followed by whitespace and a capital/digit (or start).
    - Abbreviation/initial guard: do **not** split after `Dr.`, `Mr.`, `Mrs.`, `Ms.`, `St.`, `vs.`, `e.g.`, `i.e.`, `etc.`, `Inc.`, `Jr.`, `Sr.`, `U.S.`, `U.K.`, and single initial-capped tokens (`J. Smith`). Any boundary the guard cannot resolve is left unsplit (conservative — fewer, larger sentences).
    - `split_ambiguous` = count of suppressed boundary candidates; recorded in the report.
  - `tail_sentences(text: str, counter: TokenCounter, budget_tokens: int) -> list[str]` — the **final complete sentence(s)** of `text`, accumulated from the end while the running token sum stays ≤ `budget_tokens`; always returns ≥1 sentence when `text` has sentences; order preserved (chronological). If `text` has no sentence-final punctuation, treat the whole text as one sentence.
- **Tests (`tests/test_chunking.py`):** `test_split_basic` (two sentences split), `test_abbreviation_guard` (`"Dr. Smith"`, `"e.g. aspirin"`, `"U.S."` not split; `split_ambiguous > 0`), `test_split_cjk_and_ellipsis` (`。`/`！`/`…` boundaries), `test_deterministic` (two calls identical), `test_tail_sentences_budget` (returns final sentences up to budget; ≥1 sentence; deterministic).
- **DoD:** deterministic, no-ML, no imports beyond stdlib; `split_ambiguous` surfaced; `tail_sentences` bounded by budget with order preserved.

### B5. `app/chunking/chunker.py` — `SemanticChunker` (the core)

- **Files:** `app/chunking/chunker.py`
- **Dependencies:** B1, B2, B3, B4. **Blocks:** B8, B10.
- **Expected behavior (Fact, architecture §3.2 + §3.3):**
  - `@dataclass ChunkResult: chunks: list[Chunk]; report: dict; dom_storage_key: str`.
  - `class SemanticChunker(config: ChunkingConfig, counter: TokenCounter)` with `chunk(self, doc: Document, dom_storage_key: str = "") -> ChunkResult` — a **pure function** of `(Document, ChunkingConfig, TokenCounter)`, no I/O, no RNG, no embedder dependency.
  - **Order resolution:**
    1. Build `id → Block` across all pages (first instance wins on duplicate id; duplicate count → warning in report).
    2. Walk `doc.reading_order`. If empty → fall back to page order (pages sorted by index, then in-list order) with `order_source="page_order"`.
    3. Chain id missing from pages → skip + warning (**never fabricate**).
    4. Block present in pages but absent from chain → append at the end in deterministic `(page, index)` order, `order_source="orphan"`, counted in report.
  - **Cut rules (strong boundaries — cuts only between blocks, never inside):**
    1. Skip empty/whitespace-only/`None`-text blocks; count `blocks_skipped_empty` in report.
    2. A `heading` block (block.kind == "heading") **starts a new chunk** (closes the open one; it is the anchor of its section, never the tail of the previous).
    3. Otherwise, merge next block into the open chunk when (token counts from `counter` on the joined `"\n".join` texts):
       ```
       current + next <= target_tokens
          OR ( current < min_band_tokens  AND  current + next <= soft_max_tokens )
       ```
       else close the chunk and open a new one at the next block. **Firm block-boundary cut** at budget — never mid-sentence/mid-block.
    4. Oversized block (single block `counter.count(text) > hard_max_tokens` = 2048): sentence-split via `split_sentences`; re-accumulate sentences into ≤ `target_tokens` sub-chunks (cap is `target_tokens`, not `soft_max`), each inheriting the current `heading_anchor`, `source_block_ids=[block.id]`, `forced_split=False`. If a **single sentence** exceeds `hard_max_tokens` (pathological), split at the last sentence-final punctuation ≤ `hard_max`, set `forced_split=True` (recorded, never silent); recursive separator fallback (double-newline → newline → punctuation) applies only to that degenerate tail. Close the open chunk before emitting oversized sub-chunks; resume with the next block opening a fresh chunk.
  - **Heading anchor:** nearest preceding heading's `text` (walking reading order), inherited by every chunk until the next heading; the chunk opened at a heading uses that heading's own text as its anchor; `""` when none. **Metadata only** — never prepended to `chunk.text`, never embedded.
  - **Overlap (architecture §3.3, precise reading):** applied **only at heading seams** — when a new chunk starts at a heading and `overlap_at_heading_seams=True` and the preceding chunk is non-empty: prepend `"\n".join(tail_sentences(prev_chunk.text, counter, overlap_tokens))` to the new chunk's text (heading is its first block), and set `overlap_source_chunk_id = prev_chunk.chunk_id` (explicit attribution; repeated span is not in `source_block_ids`). Ordinary budget cuts get **no** overlap.
  - **Emit:** `seq` 0-based in document order; `kind` = single block's `kind`, or `"mixed"` for merges (oversized sub-chunks keep the block's kind); `token_count = counter.count(text)`, `char_count = len(text)`; `page` = first page touched, `pages` = sorted unique; `provenance` from config snapshot + `doc.provenance.normalizer_version` (may be `None`) + `counter.tokenizer`/`tokenizer_ref_hash` + `dom_storage_key`; `embedding_ref=""` initially.
  - **Report contract:** `blocks_seen`, `blocks_orphaned`, `blocks_skipped_empty`, `blocks_missing`, `chunks_created`, `forced_splits`, `overlap_chunks`, `split_ambiguous`, `tokens_total`, `order_source_used`, `warnings` (list). `order_source_used` = `"reading_order"` if any chunk came from the chain walk, elif `"page_order"` if the fallback produced chunks, else `"orphan"`.
  - **Determinism (Fact):** all steps total and order-stable; no hashing of unordered containers → same DOM + config + tokenizer ⇒ byte-identical chunk JSON.
- **Tests (`tests/test_chunking.py`, synthetic DOMs in the `test_normalizer.py` style — `_block(seq, text, kind=..., page=0)` and `_doc(blocks)` helpers; use `TokenCounter(mode="char4")` for hermetic counts):**
  - `test_heading_starts_chunk`; `test_merge_to_target` (small blocks merge into one ~400-token chunk, `kind="mixed"`, text is `"\n".join`); `test_band_merge` (current < min_band + next ≤ soft_max merges; beyond → closes); `test_empty_blocks_skipped_and_reported`; `test_orphan_appended_and_flagged`; `test_reading_order_empty_falls_back_page_order`; `test_oversized_block_sentence_split` (each sub-chunk ≤ target, `source_block_ids=[id]`, anchor inherited, `forced_split=False`); `test_forced_split_single_huge_sentence`; `test_overlap_only_at_heading_seams` (`overlap_source_chunk_id` set, sentences aligned, budget-bounded); `test_overlap_disabled_via_config`; `test_determinism_two_runs_identical` (same `ChunkResult.model_dump_json()`); `test_provenance_fields` (normalizer_version from DOM, tokenizer recorded, params snapshot); `test_tables_untouched` (`Page.tables`/`Page.images` never appear in chunks — read only `blocks`).
- **DoD:** every architecture rule (§3.2/§3.3) implemented and covered; chunker is a pure function (no I/O/embedder imports); deterministic-identical JSON across runs.

### B6. `app/chunking/batching.py` — token-budget batching

- **Files:** `app/chunking/batching.py`
- **Dependencies:** B2, B3. **Blocks:** B8.
- **Expected behavior (Fact, architecture §3.9):**
  - `group_by_token_budget(chunks: list[Chunk], counter: TokenCounter, max_tokens_per_call: int = 16384, max_texts_per_call: int = 32) -> list[list[Chunk]]`.
  - Greedy, **order-preserving** accumulation (embed row order = chunk_id order = doc order). A group closes when adding the next chunk would exceed `max_tokens_per_call` **or** the group would exceed `max_texts_per_call`. A chunk that alone exceeds a cap still gets its own group (never dropped). Empty input → `[]`.
  - Token sum uses `chunk.token_count` (recorded by the **same** `TokenCounter` instance at chunk time — architecture invariant that budget and recorded counts agree); `counter` is retained for API shape per the architecture and is used only as a fallback if a chunk lacks a recorded count. (Implementation note — no design change.)
- **Tests (`tests/test_batching.py`):** `test_respects_token_cap`, `test_respects_text_cap`, `test_order_preserved` (flatten(group) == input order), `test_single_over_budget_chunk_own_group`, `test_exact_boundary` (adding exactly to the cap stays in-group; +1 closes), `test_empty_input`.
- **DoD:** both caps enforced, order preserved, over-budget chunk isolated, deterministic.

### B7. `app/chunking/store.py` — `ChunkStore` ABC + `FilesystemChunkStore`

- **Files:** `app/chunking/store.py`
- **Dependencies:** B2. **Blocks:** B8.
- **Expected behavior (Fact, architecture §3.6 + §3.7):**
  - `class ChunkStore(ABC)` with exactly the §3.7 method set: `put_chunks`, `get_chunks`, `latest_chunks`, `iter_all_chunks`, `put_embeddings`, `get_embeddings`, `get_embedding`, `iter_embeddings` (signatures per §3.7).
  - `class FilesystemChunkStore(ChunkStore)` under `root/`:
    - `chunks/{doc_id}/chunks-v{version}.json` — version = `_version_suffix(chunker_version)` (`"chunker-v0.1.0"` → `"v0.1.0"`); deterministic overwrite; prior versions retained (ADR #8 semantics).
    - `embeddings/{doc_id}/emb-v{version}-{embedder_id}.npy` (float32 matrix, N×dim) + `.json` sidecar (deterministic `sort_keys=True` JSON: `chunk_ids` row order, `embedder_id`, `chunker_version`, `dim`, `dtype`, `normalize`, `device`, token-budget caps, `validation`, storage metadata).
    - `embedder_id = _sanitize_embedder_id(embedder.name)` — replace any char not in `[A-Za-z0-9._-]` with `_` (declared charset in architecture §3.6; see Flag #2). Deterministic.
    - `latest_chunks`: glob `chunks/{doc_id}/chunks-v*.json`, numeric-version sort (`vX.Y.Z` parsed to `(X, Y, Z)` tuple; string fallback), latest wins.
    - `get_embedding(doc_id, chunk_id, ...)`: read sidecar `chunk_ids` → row index, load `.npy`, return row as `list[float]` (naive read-row-by-id; vector store replaces this behind the seam later).
    - `iter_all_chunks` / `iter_embeddings`: deterministic traversal (sorted doc_id, then sorted version key).
    - `_version_suffix(version)` **duplicated locally** (mirrors `app/parser/storage._version_suffix`; not imported — chunking is a decoupled consumer, architecture §3.6).
  - `put_chunks` / `put_embeddings` return the storage key (mirror `FilesystemStore.put_dom`). `put_embeddings` returns the `.json` sidecar key (this is what `Chunk.embedding_ref` points at).
- **Tests (`tests/test_chunk_store.py`, use `tmp_path`):** `test_chunks_roundtrip` (put→get equals artifact), `test_versioned_keys` (`chunks-v0.1.0.json`, `emb-v0.1.0-<id>.json|.npy`), `test_latest_chunks_numeric_sort` (v0.1.0 vs v0.10.0 vs v1.2.3 → v1.2.3), `test_deterministic_overwrite` (same artifact twice → identical bytes; still readable), `test_versions_retained` (old chunker version readable after newer write), `test_embeddings_roundtrip` (chunk_ids ↔ matrix rows aligned; `get_embedding` returns the right row), `test_iter_traversal`, `test_sanitize_embedder_id` (`"BAAI/bge-m3@local-fp16"` → `"BAAI__bge-m3_local-fp16"`; `"dummy-feature-hash"` unchanged).
- **DoD:** all ABC methods implemented; keys match §3.6 exactly; round-trip + idempotency + traversal tests green.

### B8. `app/chunking/pipeline.py` — `ChunkEmbedPipeline` (the projection stage)

- **Files:** `app/chunking/pipeline.py`
- **Dependencies:** B3, B5, B6, B7, A1 (correct `emb-` keys when real BGE present). **Blocks:** B9, C1.
- **Expected behavior (Fact, architecture §3.8, ADR-010):**
  - `@dataclass ChunkEmbedResult`: `doc_id`, `status: str = "ok"` (`ok | failed`), `error: str = ""`, `dom_storage_key`, `chunks_created`, `embedded`, `skipped`, `dim`, `dtype`, `ms`.
  - `class ChunkEmbedPipeline(store_root: str, chunk_store: ChunkStore | None = None, embedder: Embedder | None = None, config: ChunkingConfig | None = None, events: EventPublisher | None = None)`. Defaults: `chunk_store = FilesystemChunkStore(store_root)`, `embedder = factory.default_embedder()` (real BGE-M3 when available, `DummyEmbedder` otherwise — **Fact**), `events = EventPublisher(sink=silent_sink())`, `config = ChunkingConfig()`. A single `TokenCounter` resolved once (per pipeline instance) from `config` — all chunking + batching share it.
  - `run(doc_id: str, dom_storage_key: str | None = None) -> ChunkEmbedResult`:
    1. Resolve latest normalized DOM: glob `dom/{doc_id}/norm-v*.docJSON` off `store_root` (same convention as `app/processing/cli._embed_pass`, **Fact**), numeric-version sort, parse `Document`. `dom_storage_key` override skips the glob. Missing DOM → `status="failed"`, no writes (never crash, mirroring `DocResult`).
    2. `chunks, report, dom_key = SemanticChunker(config, counter).chunk(doc, dom_key)`.
    3. `put_chunks(doc_id, artifact)` — deterministic overwrite of `chunks-v{ver}.json`.
    4. `groups = group_by_token_budget(chunks, counter, config.max_tokens_per_call, config.max_texts_per_call)`.
    5. `embedder_id = _sanitize_embedder_id(embedder.name)`; `existing = chunk_store.get_embeddings(doc_id, ver, embedder_id)`; `missing = [c for c in chunks if c.chunk_id not in existing_chunk_ids]` (**never embed twice** — presence keyed on content-addressed `chunk_id`).
    6. For each group, embed only the group's missing subset: `batch_embed(embedder.embed, [c.text for c in missing_group], batch_size=len(missing_group))` (shape-guard via `batch_embed`; one model call per group).
    7. Merge existing + new vectors so **row order = chunk_id order of the chunks artifact** (doc order), store `float32` `.npy` + `.json` sidecar with `validation` stamp: re-embed `chunks[0].text`, `cosine ≥ 0.9999` when GPU-fp16 (ADR-010); record `{"sample_chunk_id", "cosine", "ok"}` in sidecar meta. On CPU/Dummy (bit-exact) the sample is trivially identical — still recorded.
    8. Rewrite the chunks artifact with `embedding_ref = sidecar_key` filled (deterministic overwrite; `chunk_id` unchanged).
    9. Emit `chunk_embedded.v1` `{doc_id, chunker_version, embedder_id, chunks, embedded, skipped, dim, dtype, ms}` via `events` (silent sink in batch contexts).
  - **Never embed twice is structural (Fact):** `chunk_id` has no embedder dependence → swapping embedder versions writes a new emb key (`-{embedder_id}`) over the same chunks without re-embedding; a re-run embeds only newly-appeared `chunk_id`s.
  - **Never `embed_document_blocks` for chunks** (architecture §3.8) — the block-level helper stays legacy-only.
- **Tests (`tests/test_chunk_embed_pipeline.py`, `DummyEmbedder` only — hermetic; build a `dom/{doc_id}/norm-v0.1.0.docJSON` in `tmp_path`):**
  - `test_embeds_all_on_first_run` (embedded == chunks, skipped == 0, emb files written).
  - `test_never_embeds_twice` (re-run → embedded == 0, skipped == n, sidecar bytes unchanged).
  - `test_new_chunk_only_embedded` (edit the DOM to add one block → only the new `chunk_id` embedded).
  - `test_artifact_shapes` (sidecar `chunk_ids` order == chunks artifact order == matrix rows).
  - `test_embedding_ref_populated` (chunks artifact rewritten with `embedding_ref` set; `chunk_id` unchanged).
  - `test_event_emitted` (capturing sink receives `chunk_embedded.v1` with the §3.8 payload fields).
  - `test_validation_stamp_in_sidecar` (meta has `validation.sample_chunk_id`, `ok`).
  - `test_latest_norm_dom_resolved` (two norm versions → highest used, recorded in provenance/artifact `dom_storage_key`).
  - `test_missing_dom_fails_gracefully` (no norm file → `status="failed"`, no chunk files written).
- **DoD:** §3.8 steps 1–9 implemented; never-embed-twice proven by test; validation stamp + event emitted; hermetic (no torch import on the Dummy path).

### B9. `app/chunking/cli.py` — thin CLI

- **Files:** `app/chunking/cli.py`
- **Dependencies:** B8. **Blocks:** B10.
- **Expected behavior (Fact, architecture §2):** `python -m app.chunking.cli --doc <doc_id> --store <root> [--embed] [--dom-key <key>]`. Mirrors `app/parser/cli.py` style (`argparse`, `main(argv=None) -> int`, `__main__` guard). Without `--embed`: chunk-only (put_chunks), print the report summary. With `--embed`: full `ChunkEmbedPipeline` run, print `{chunks, embedded, skipped, dim, dtype, embedder}`. Missing DOM → error line, exit 1.
- **Tests (`tests/test_chunk_embed_pipeline.py` or a small `test_chunking_cli.py`):** `test_cli_chunk_only` and `test_cli_with_embed` — write a `dom/{doc_id}/norm-v0.1.0.docJSON` into `tmp_path`, call `app.chunking.cli.main(["--doc", did, "--store", root, "--embed"])`, assert rc 0, `chunks/{did}/chunks-v0.1.0.json` exists and emb sidecar/npy exist (Dummy embedder is the hermetic default).
- **DoD:** CLI runs both modes hermetically and exits 0; error path exits 1.

### B10. `app/chunking/__init__.py` — final exports

- **Files:** `app/chunking/__init__.py`
- **Dependencies:** B1–B9. **Blocks:** nothing.
- **Expected behavior (Fact, architecture §2):** exports `SemanticChunker`, `Chunk`, `ChunkStore`, `ChunkEmbedPipeline`, `ChunkingConfig` (plus `ChunkResult`, `ChunkProvenance`, `ChunksArtifact`, `TokenCounter`, `group_by_token_budget`, `FilesystemChunkStore`, `ChunkEmbedResult`), mirroring `app/embedding/__init__.py` (`__all__` + docstring).
- **Tests:** import smoke in `tests/test_chunking.py` (`from app.chunking import ...`).
- **DoD:** public surface matches §2; `from app.chunking import SemanticChunker, Chunk, ChunkStore, ChunkEmbedPipeline, ChunkingConfig` works.

---

### Track C — Real-model gating + memory

### C1. Real-BGE gated test — cosine-stable two-run equality

- **Files:** `tests/test_chunk_embed_pipeline_real.py`
- **Dependencies:** A1, B8, B9. **Blocks:** nothing (last verification).
- **Expected behavior (Fact, architecture §7, ADR-010):** follow the `test_sbert_embedder.py` skip pattern:
  ```python
  def _available() -> bool:
      try:
          import sentence_transformers, torch   # noqa
          return Path("models/bge-m3/tokenizer.json").exists() and Path("models/bge-m3/config.json").exists()
      except Exception:
          return False
  pytestmark = pytest.mark.skipif(not _available(), reason="sentence-transformers/torch/bge-m3 unavailable")
  ```
  - `test_cosine_stable_across_runs`: run `ChunkEmbedPipeline` twice on the same DOM with the real embedder (`factory.default_embedder(EmbeddingOptions(real_if_available=True))`); cosine between `chunks[0]` vectors of the two runs ≥ 0.9999 (L2-normalized already — Fact).
  - `test_dim_and_dtype`: `dim == 1024`; `.npy` loads as `float32`, shape `(N, 1024)`.
  - `test_name_identity`: `embedder.name` contains `"bge-m3"` and a dtype tag (`"fp16"`/`"fp32"`) — requires A1.
  - `test_never_embeds_twice_real`: re-run embeds 0 new (structural, matches the Dummy test but on the real path).
- **DoD:** skips cleanly when the model/torch is absent; passes on this box (`models/bge-m3` present — verified).

### D1. Knowledge-blackboard update

- **Files:** `project_memory/module_status.md`
- **Dependencies:** all of Track A + Track B (module complete). **Blocks:** nothing.
- **Expected behavior:** append a Module #3 row: `app/chunking/` implemented + tested, chunker version, token-budget caps, `ChunkStore` seam (interface-only), BGE-M3 wiring + ADR-009/010 applied, test count delta. **Append, never destroy** (guardrail).
- **DoD:** `module_status.md` reflects the completed run; no prior lines removed.

---

## 3. Test plan (consolidated)

| Test file | Covers | Hermetic? |
|---|---|---|
| `tests/test_chunking.py` | config defaults/snapshot; schema + `chunk_id`; tokenizer (both modes); sentences; chunker boundary/merge/oversized/overlap/determinism/provenance; tables untouched; package import | Yes (`TokenCounter(mode="char4")`; bge-mode test skips if tokenizer absent) |
| `tests/test_chunk_store.py` | versioned keys, round-trips, numeric latest, deterministic overwrite, versions retained, emb row alignment, `iter_*`, sanitize | Yes (`tmp_path`) |
| `tests/test_batching.py` | both caps, order, over-budget isolation, exact boundary, empty | Yes |
| `tests/test_chunk_embed_pipeline.py` | never-embed-twice, artifact shapes, `embedding_ref`, event, validation stamp, latest-DOM resolution, missing-DOM failure, CLI both modes | Yes (`DummyEmbedder` only) |
| `tests/test_chunk_embed_pipeline_real.py` | cosine-stable two-run ≥ 0.9999, dim 1024 / dtype float32, name identity, never-embed-twice on real | Gated (`skipif` on torch + `models/bge-m3`) |
| `tests/test_sbert_embedder.py` | **updated** fixture bge-m3 + name identity assertion (A1/A2) | Gated (existing pattern) |

**Run-level gate (Fact, active_objective DoD):** `.venv/Scripts/python.exe -m pytest tests/ -q` green — new tests + existing 31+35 baseline. Nothing in `app/parser/`, `app/normalizer/`, `app/processing/` behavior changes except the A3 default values (no test asserts them — verified by grep).

---

## 4. Ordering / parallelism

```
Wave 0 (fully parallel — no mutual dependencies):
  A1 (sbert name + protocol docstring)   A3 (defaults lowering)
  B0 (package skeleton)                  B1 (config)   B2 (schema)   B3 (tokenize)

Wave 1 (parallel):
  B4 (sentences: needs B3)               B6 (batching: needs B2+B3)   B7 (store: needs B2)
  A2 (test_sbert fix: needs A1)

Wave 2 (single — the core, needs B1+B2+B3+B4):
  B5 (chunker)

Wave 3 (parallel, each needs B5 + its own deps; A1 already in):
  B8 (pipeline: needs B5+B6+B7+B3+A1)    (B6/B7 landed in Wave 1)

Wave 4 (parallel):
  B9 (cli: needs B8)   C1 (real gated test: needs A1+B8)   B10 (exports: needs B1–B9)

Wave 5:
  D1 (memory update: needs everything)
```

**Blocking path:** B0 → B1/B2/B3 → B4 → B5 → B8 → B9 → B10 (C1 can join at B8). A1 is on the critical path **only** for B8's real-BGE key correctness and C1; it does not block B1–B7 (DummyEmbedder name is already identity-bearing). A3, A2, B6, B7 run fully in parallel with the core.

---

## 5. Flags for the architect (real gaps / ambiguities surfaced — NOT decided here)

1. **DOM resolution seam (Inference, clarification, not a gap):** `app/parser/storage.Store` has no list/latest method; the architecture's "resolve latest normalized DOM" requires a glob. Plan follows the existing convention (`app/processing/cli._embed_pass` globs `store.root/dom/<doc_id>/norm-v*.docJSON`), keeping all resolution inside `app/chunking/pipeline.py`. No `Store` ABC change (would violate "no parser edits").
2. **`embedder_id` example vs declared charset (Fact mismatch in architecture §3.6):** the example `BAAI__bge-m3@local-fp16` retains `@`, but the declared sanitize rule is `[A-Za-z0-9._-]` which excludes `@`. Plan treats the declared charset as authoritative → `BAAI__bge-m3_local-fp16`. If the architect prefers `@` kept, the charset must be amended (one line); otherwise the plan's rendering stands.
3. **`batch_embed` default (64) not in the architect's follow-up list (Fact):** §3.9 lists only `EmbeddingOptions.batch_size` and `ProcessingConfig.embed_batch_size`. The chunk path always passes an explicit `batch_size`, so the `batch_embed` default is never hit there. Left unchanged; flagged in case the "stop advertising OOM-risky defaults" intent (§3.9) should extend to `batch_embed`.
4. **Overlap placement (Inference, flagged so the implementer cannot guess):** per §3.3's literal wording, at a heading seam the *heading chunk itself* (first block = heading) receives the previous chunk's tail sentences prefixed to its text. This is implemented exactly so, with `overlap_source_chunk_id` set on the heading chunk and the repeated span excluded from `source_block_ids`.
5. **Revision resolution for `name` (Recommendation, architecture §3.10 is open on *how*):** plan fixes it to `sha256(config.json bytes)[:8]` when the local model dir exists, else `"local"` — deterministic, on-prem, no remote reads. Architect should confirm this satisfies the key-uniqueness intent.

---

## 6. Out of scope (from architecture §8 — must NOT be built)

Atomic table/figure-caption chunks (schema reserved only), parent-child retrieval/context injection (`parent_chunk_id`/`heading_anchor` reserved only), vector store/hybrid retrieval (`iter_*` is the seam), splitting `app/processing/cli._embed_pass` (legacy, left untouched), bit-exact GPU-fp16 enforcement (ADR-010 says cosine-stable; strict mode is a future opt-in).

---

## 7. Verdict

**PLAN: READY**

- Every architecture element (§2–§3, ADR-009/010, and the three flagged follow-ups) maps to a task with files, behavior, tests, and DoD.
- Hermetic-by-construction: all core tests use `TokenCounter(mode="char4")` + `DummyEmbedder`; the only real-model test is gated on torch + `models/bge-m3` presence.
- Ordering/parallelism explicit; Track A runs concurrently with Track B and unblocks only the real-key/cosine verification paths.
- Existing suite stays green; the only edits outside `app/chunking/` are the architect-flagged A1–A3.
- Five real gaps/ambiguities surfaced for the architect (none blocking; #1–#4 have a faithful default, #5 needs architect confirmation).
