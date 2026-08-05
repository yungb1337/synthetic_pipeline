# Architecture — run-2026-08-04-chunking (Module #3: Semantic Chunking + BGE-M3 wiring)

**Author:** Chief Architect · **Date:** 2026-08-05 · **Run:** `run-2026-08-04-chunking`
**Inputs:** `project_memory/active_objective.md`, `checkpoints/run/run-2026-08-04-chunking/research.md`, `app/parser/dom/models.py`, `app/parser/storage.py`, `app/normalizer/`, `app/embedding/`, `app/processing/executor.py`, `docs/universal-document-understanding-engine.md` §8/§11, `project_memory/architecture_decisions.md`.
**Claim labels:** Fact | Research | Inference | Recommendation — applied throughout.

---

## 0. One-paragraph summary

A new, decoupled module `app/chunking/` turns a **normalized DOM** into **content-addressed, lineage-carrying chunks** — the retrieval atom — and projects them to embeddings through the existing `Embedder` protocol. Chunking is a *consumer of the DOM*, never a parser/normalizer stage (universal-engine §0, §8: "the DOM is the single source of truth; everything downstream is a projection"). Boundaries are **DOM-anchored**: walk `Document.reading_order`, cut at `Block` boundaries, merge small blocks to a ~400-token budget, sentence-split oversized blocks under a heading anchor. Storage mirrors `app/parser/storage.py` (versioned `chunks/{doc_id}/chunks-v{ver}.json`, `embeddings/{doc_id}/emb-v{chunker}-{embedder}.{json|npy}`). A `ChunkStore` seam is the retrieval interface (interface-only this run — no vector search). `ChunkEmbedPipeline` embeds only missing `chunk_id`s (never embed twice) under a **token-budget batching policy** (≤16k tokens/call, ≤32 texts/call) sized for fp16 on the 4 GB RTX 3050. The open research risks are resolved: fp16 determinism is defined as **cosine-stable equality** (ADR-010), tables/figures stay out of this run (documented next step), token counting is **tokenizer-pinned**, and batch defaults drop below the current OOM-risky values.

**Verdict: `ARCHITECTURE: APPROVED`** (reasons at the end).

---

## 1. Context and inputs (Fact)

- **DOM contract (Fact, `app/parser/dom/models.py`):** `Document.reading_order` is a `list[str]` of block ids covering *every* block in the document; blocks live in `Page.blocks` (one canonical instance per id); `Block` = `{id, kind, text, bbox, page, confidence, source, ocr_engine}`. `Page.tables` and `Page.images` are separate top-level lists and are **not** in `reading_order`.
- **Normalizer (Fact, `app/normalizer/`):** a pure projection; only `Block.text` changes; `reading_order`, page structure, tables/images, and provenance are preserved; `provenance.normalizer_version` + `normalization_report` are attached.
- **Embedder seam (Fact, `app/embedding/`):** `Embedder` Protocol = `{name, embed(texts, batch_size=None) -> list[list[float]]}`, docstring requires determinism/idempotency for a given model version and batched calls. `batch_embed` slices by *count* and shape-guards. `SentenceTransformerEmbedder` = BGE-M3 1024-dim, fp16 when CUDA, `normalize_embeddings=True`, `name` is currently generic `"sentence-transformers"`. `DummyEmbedder` is bit-exact deterministic (feature-hash).
- **Storage pattern (Fact, `app/parser/storage.py`):** raw + images are content-addressed immutables (`raw/<sha256>`, `images/{doc_id}/{sha256}.{ext}`); DOM outputs are versioned per `doc_id × version` (`dom/{doc_id}/dom-v{ver}.docJSON`, `norm-v{ver}.docJSON`); same-version re-write is a deterministic overwrite, prior versions retained (ADR #8).
- **Processing seam (Fact, `app/processing/`):** `ParseNormalizePipeline` = parse → normalize → `put_normalized`. `ProcessingConfig.embed_batch_size = 64`; the CLI `_embed_pass` embeds normalized *blocks* (legacy; superseded, left untouched). `app/parser/events.py` gives `EventPublisher` + `silent_sink`.
- **Defaults that are the OOM trap (Research + Fact, research.md Q2):** `EmbeddingOptions.batch_size = 128` (factory), `batch_embed` default 64, `ProcessingConfig.embed_batch_size = 64`. On fp16 RTX 3050 4 GB: B≈16 at L=512 fits, B≈32 at L=1024 near-OOM (**Inference** — arithmetic estimates; to validate on the box).
- **BGE-M3 ceiling (Fact from `models/bge-m3/config.json`, cited in research.md):** xlm-roberta, hidden 1024, 24 layers, `max_pos = 8194`. A 2048-token hard cap is well under the ceiling → no silent truncation.
- **Universal-engine §8 (Fact, docs):** recommended "DOM-anchored, hierarchical chunks with parent-child retrieval"; cut only at Heading/Paragraph/Table/ListItem boundaries; sentence-split overflow keeping the parent anchor; don't split tables or figure+caption pairs.

---

## 2. Module layout — `app/chunking/`

```
app/chunking/
  __init__.py        exports SemanticChunker, Chunk, ChunkStore, ChunkEmbedPipeline, ChunkingConfig
  config.py          ChunkingConfig (frozen dataclass, versioned, snapshot())
  schema.py          Chunk, ChunkProvenance, ChunksArtifact (pydantic)
  tokenize.py        TokenCounter — pinned BGE BPE or char/4 heuristic (deterministic)
  sentences.py       split_sentences(), tail_sentences() — deterministic, no-ML sentence splitter
  chunker.py         SemanticChunker.chunk(Document) -> ChunkResult{chunks, report, dom_storage_key}
  batching.py        group_by_token_budget(chunks, counter, caps) -> list[list[Chunk]]  (greedy, order-preserving)
  store.py           ChunkStore ABC + FilesystemChunkStore (chunks + embeddings, versioned keys)
  pipeline.py        ChunkEmbedPipeline.run(doc_id)  — chunk → persist → embed-only-missing → persist emb + refs
  cli.py             optional thin CLI: python -m app.chunking.cli --doc <doc_id> --store <root> [--embed]
```

Dependencies: `app/parser.dom` (Document + `Store` for reading the normalized DOM), `app/embedding` (the `Embedder` protocol via `factory.default_embedder`), `app/parser.events` (`EventPublisher`). **No dependency on normalizer internals or processing internals.** The module is a projection, not a pipeline stage.

---

## 3. Core design

### 3.1 Chunk model and content-addressed `chunk_id` (Recommendation)

`app/chunking/schema.py`:

```python
class ChunkProvenance(BaseModel):
    chunker_version: str
    chunker_params: dict          # target_tokens, hard_max, band, overlap, order fallback, ...
    dom_schema_version: str
    normalizer_version: str | None
    dom_storage_key: str          # which norm-v{ver}.docJSON was consumed (traceability)
    tokenizer: str                # "bge-m3" | "char4" (exactly which token counts came from)
    tokenizer_ref_hash: str | None
    forced_split: bool            # True only when a single sentence exceeded hard_max (pathological)

class Chunk(BaseModel):
    chunk_id: str                 # content-addressed sha256 (see below) — NEVER position or embedder dependent
    doc_id: str
    seq: int                      # position in document order (stable for a given DOM version)
    kind: str                     # paragraph|heading|list_item|code|formula|caption|mixed
    text: str                     # faithful join of source Block.text; None is never fabricated
    source_block_ids: list[str]   # block ids (in reading order) fully covered by this chunk
    overlap_source_chunk_id: str | None = None   # set only when head repeats a prior chunk's tail (heading seams)
    page: int                     # first page touched
    pages: list[int]
    heading_anchor: str = ""      # nearest preceding heading text; "" when none (metadata only, NOT embedded)
    parent_chunk_id: str | None = None   # RESERVED for parent-child retrieval (not built this run)
    token_count: int
    char_count: int
    tokenizer: str
    order_source: str = "reading_order"   # "reading_order" | "page_order" | "orphan"
    provenance: ChunkProvenance
    embedding_ref: str = ""       # emb storage key; populated by the embed pass (deterministic overwrite)

class ChunksArtifact(BaseModel):
    schema: str = "chunks-v1"
    doc_id: str
    chunker_version: str
    dom_storage_key: str
    chunks: list[Chunk]
    report: dict                  # blocks_seen, blocks_orphaned, chunks_created, forced_splits,
                                  # overlap_chunks, tokens_total, order_source_used, warnings
```

**`chunk_id` definition (Recommendation):**

```
chunk_id = sha256_hex(canonical_json({"doc_id": doc_id, "text": text, "source_block_ids": source_block_ids}))[:64]
```

- Canonical JSON = `sort_keys=True`, UTF-8, no whitespace → the same bytes every run (**Fact**: this is deterministic).
- **Excludes** `seq` (inserting a paragraph earlier must not invalidate every later embedding), `heading_anchor` (metadata only, not part of the embedded text), `chunker_version` (a version bump that yields byte-identical chunks should skip re-embedding), and `embedding_ref` (a projection, never part of identity).
- **Includes** `source_block_ids` so lineage is pinned to source bytes (doc → DOM → block → chunk → embedding). Trade-off vs pure-text hash is scored in §5(c).

**Chunk `text` faithfulness (Recommendation):** `"\n".join(block.text for block in covered blocks)`. Whitespace-only or empty blocks are skipped (recorded in report) — a `None`/empty block is never fabricated into text.

### 3.2 The chunker — DOM-anchored walk (Recommendation, per research Q1 + universal §8)

`SemanticChunker.chunk(doc)` is a **pure function** of `(Document, ChunkingConfig, TokenCounter)` — no I/O, no RNG, no embedder dependency. Algorithm:

1. **Resolve order.** Build `id -> Block` across all pages, then walk `doc.reading_order`. Because the DOM builder puts *every* block in the chain (**Fact**), the normal path uses it fully. Defensive paths, recorded in the report:
   - `reading_order` empty → fall back to page order (sorted page index, then in-list order) with `order_source="page_order"`.
   - Block id in chain missing from pages → skip + record warning (**never fabricate**).
   - Block present in pages but absent from chain (hand-edited DOM) → append at the end in deterministic (page, index) order with `order_source="orphan"`, counted in report. Keeps text lossless while flagging the anomaly.
2. **Cut rules (strong boundaries).** Every block is atomic; cuts happen *between* blocks, never inside:
   - A `heading` block **starts a new chunk** (it is the anchor of its section, not the tail of the previous one).
   - Otherwise a chunk closes when its token count reaches the merge policy (§3.3).
   - Blocks are never split mid-block unless the single block exceeds `hard_max_tokens` (oversized-block path).
3. **Oversized block (single block > `hard_max_tokens = 2048`):** sentence-split via `sentences.split_sentences` (§3.4); re-accumulate sentences into ≤ `target_tokens` chunks, each inheriting the current `heading_anchor`, `source_block_ids = [block.id]`, `forced_split=False`. If a *single sentence* exceeds `hard_max_tokens` (pathological), split at the last sentence-final punctuation ≤ `hard_max`, set `forced_split=True` (recorded in provenance — never silent). **Recursive separator fallback** (split on double-newline → newline → punctuation) applies only to that degenerate tail, exactly as research Q1 reserves it.
4. **Heading anchor:** the nearest preceding heading block's `text` (walking reading order); inherited by every chunk until the next heading. Empty string when none. `heading_anchor` is **metadata only** this run — it is not prepended to `chunk.text` and not embedded (context-injection is the reserved `parent_chunk_id` mechanism, not built this run).
5. **Emit:** per chunk, compute `token_count`/`char_count`, `kind` (single-block kind, or `"mixed"` for merges), `seq` (0-based order), `page`/`pages`, and a `ChunkProvenance` snapshot.

Determinism is structural: the same DOM + config + tokenizer yields byte-identical chunk JSON (**Inference**: all steps are total and order-stable; no hashing of unordered containers).

### 3.3 Size, band, overlap policy (Recommendation, per research Q2)

| Param | Value | Rationale |
|---|---|---|
| `target_tokens` | 400 | retrieval atom size; balances precision vs embedding cost |
| `min_band_tokens` | 256 | floor — don't leave a too-thin chunk when more text follows |
| `soft_max_tokens` | 768 | upper band for merge acceptance |
| `hard_max_tokens` | 2048 | absolute cap; only oversized blocks get sentence-split (well under BGE-M3's 8194 ceiling) |
| `overlap_tokens` | 48 | ~10%, sentence-aligned |

**Merge rule (deterministic):** add the next block to the open chunk when
```
current + next <= target_tokens
   OR ( current < min_band_tokens  AND  current + next <= soft_max_tokens )
```
and the next block is not a heading (headings always cut). Otherwise close the chunk and start a new one at the next block. This is a **firm block-boundary cut** at budget — never a mid-sentence/mid-block cut.

**Overlap (precisely, since research Q2 is terse):** overlap is applied **only at heading seams**, i.e. when a new chunk starts at a heading. Its first block is then prefixed with the **final complete sentence(s)** of the immediately preceding chunk, accumulated up to `overlap_tokens = 48` (`tail_sentences`), and `overlap_source_chunk_id` is set on the new chunk. Ordinary budget cuts get **no** overlap. Properties: sentence-aligned by construction, bounded ~10%, deterministic, and the repeated span is *explicitly attributed* (faithful join preserved). Rationale and rejected alternatives are scored in §5(b). Config knob `overlap_at_heading_seams` flips it off; a future retrieval eval can tune it.

### 3.4 Deterministic tokenization (open risk #3 — resolved)

`app/chunking/tokenize.py` — `TokenCounter`:

- **Primary `mode="bge-m3"`:** load the **pinned BGE tokenizer** from the local `models/bge-m3/tokenizer.json` via the `tokenizers` library (already a sentence-transformers dependency — **Fact**), wrap as `tokenizers.Encoding` fast tokenizer. BPE is deterministic (**Fact**: same string → same token ids, no GPU/thread nondeterminism). `count(text) = len(encode(text).ids)`. Record `tokenizer_ref_hash = sha256(tokenizer.json bytes)` in provenance.
- **Fallback `mode="char4"`:** `max(1, len(text) // 4)` — deterministic, dependency-free, used when the local tokenizer file is absent (hermetic CI/tests). Recorded in provenance as `"char4"` so token counts are never silently mixed.
- The counter is resolved **once per pipeline run**; all chunks and all batching decisions use the same counter instance, so budget enforcement and recorded counts agree.

The char/4 fallback keeps hermetic tests model-free; production uses the exact model tokenizer so `token_count` matches the embedder's real context accounting (**Recommendation**).

### 3.5 Sentence splitter (Recommendation)

`app/chunking/sentences.py` — deterministic, no-ML: split on sentence-final punctuation (`.`, `!`, `?`, `。`, `！`, `？`, `…`) followed by whitespace and a capital/digit, with a small abbreviation guard (Dr./Mr./etc., U.S., initial-capped tokens) so "Dr. Smith" and "e.g. aspirin" do not split. Any boundary the guard cannot resolve is left unsplit (conservative — fewer, larger sentences). `split_ambiguous` counts recorded in the report. Used by: oversized-block sentence splitting and the heading-seam overlap tail.

### 3.6 Storage keys + versioning (Recommendation, per research Q3; mirrors `app/parser/storage.py`)

`FilesystemChunkStore` under `root/`:

```
chunks/{doc_id}/chunks-v{chunker_version}.json
embeddings/{doc_id}/emb-v{chunker_version}-{embedder_id}.json      # sidecar: meta + chunk_ids (row order)
embeddings/{doc_id}/emb-v{chunker_version}-{embedder_id}.npy       # float32 matrix, N x dim
```

- Version suffixes use the same rule as parser storage (`chunker-v0.1.0` → `v0.1.0`). A tiny local `_version_suffix` helper mirrors `app/parser/storage._version_suffix` (duplicated, not imported — chunking is a decoupled consumer).
- **`embedder_id`** = sanitized `f"{name}@{revision}-{dtype}"`, e.g. `BAAI__bge-m3@local-fp16`. Requires the ADR-009 change to `SentenceTransformerEmbedder.name` so the key encodes model identity + dtype (research Q4: "tighten `Embedder.name`"). `DummyEmbedder.name` (`"dummy-feature-hash"`) is already identity-bearing. Sanitize to `[A-Za-z0-9._-]` for filesystem safety.
- **Idempotency semantics (Fact-aligned with ADR #8):** a re-write of the *same* version is a deterministic overwrite; prior versions are never deleted. The chunks artifact is re-written on every run (cheap, same bytes). The emb artifact is written once per (doc_id, chunker_version, embedder_id) with only-missing rows added (§3.8).
- `.npy` (float32) is the primary vector payload (compact, deterministic bytes given the same array); the `.json` sidecar carries metadata + `chunk_ids` row order + `embedding_ref` provenance + validation result. JSON-only (no npy) is a config option for tiny setups but not the default.

### 3.7 `ChunkStore` seam — retrieval interface (interface-only this run) (Recommendation)

```python
class ChunkStore(ABC):
    # chunks
    def put_chunks(self, doc_id: str, artifact: ChunksArtifact) -> str: ...
    def get_chunks(self, doc_id: str, chunker_version: str) -> ChunksArtifact | None: ...
    def latest_chunks(self, doc_id: str) -> ChunksArtifact | None: ...
    def iter_all_chunks(self) -> Iterator[ChunksArtifact]: ...
    # embeddings
    def put_embeddings(self, doc_id, chunker_version, embedder_id,
                       chunk_ids: list[str], matrix: np.ndarray, meta: dict) -> str: ...
    def get_embeddings(self, doc_id, chunker_version, embedder_id
                       ) -> tuple[list[str], np.ndarray, dict] | None: ...
    def get_embedding(self, doc_id, chunk_id, chunker_version, embedder_id
                      ) -> list[float] | None: ...
    def iter_embeddings(self) -> Iterator[tuple[str, list[str], np.ndarray, dict]]: ...
```

`FilesystemChunkStore` implements all of it on the keys in §3.6. `iter_*` is what a future hybrid retrieval (BM25 + dense) will consume; **no vector index is built this run** (explicit DoD scope). `get_embedding` is a naive read-row-by-id for now; a vector store replaces it behind this seam (documented future ADR).

### 3.8 `ChunkEmbedPipeline` — the chunk → embed projection (Recommendation, per research Q4)

A standalone projection stage, parallel to parse→normalize, **not** inside `ParseNormalizePipeline` (no processing redesign).

```
run(doc_id):
  1. resolve latest normalized DOM: norm-v{ver}.docJSON for doc_id (numeric-version sort; explicit
     dom_storage_key optional override). Record key in ChunkProvenance.
  2. chunks, report = SemanticChunker.chunk(doc)
  3. put_chunks (deterministic overwrite of chunks-v{chunker_version}.json)
  4. groups = group_by_token_budget(chunks)                     # token-budget batching (§3.9)
  5. existing = chunk_store.get_embeddings(doc_id, ver, embedder_id)   # never embed twice
     missing = [c for c in chunks if c.chunk_id not in existing_chunk_ids]
  6. for each group, for the missing subset:
       batch_embed(embedder.embed, group_texts, batch_size=len(group_texts))   # shape-guard via batch_embed
  7. merge existing + new vectors (row order = chunk_id order), write emb .npy + .json sidecar
     (validation: sample re-embed of chunk[0], cosine >= 0.9999 for GPU-fp16 — ADR-010)
  8. rewrite chunks artifact with embedding_ref filled (deterministic overwrite; chunk_id unchanged)
  9. emit event:  chunk_embedded.v1 {doc_id, chunker_version, embedder_id, chunks, embedded, skipped,
     dim, dtype, ms}  via EventPublisher (silent_sink in batch contexts)
```

- **Never embed twice (Fact-verified design):** presence is keyed on the content-addressed `chunk_id`, so a re-run embeds only newly-appeared chunks; a same-version write is a deterministic overwrite. No embedder-dependence in `chunk_id` → swapping embedder versions does not re-embed (new emb key, same chunks).
- **Uses `factory.default_embedder`** — real BGE-M3 when `models/bge-m3` + torch + CUDA are available, `DummyEmbedder` otherwise. **Never `embed_document_blocks` for chunks** (research Q4).
- **Output shapes:** `embedder.embed` returns `list[list[float]]`; matrix is stored float32 (`np.asarray(rows, dtype=np.float32)`). Dummy → bit-exact; BGE fp16 → cosine-stable (ADR-010).

### 3.9 Token-budget batching policy (Recommendation, per research Q2 — resolves open risk #4)

`app/chunking/batching.py` — `group_by_token_budget(chunks, counter, max_tokens_per_call=16384, max_texts_per_call=32)`:

- Greedy, **order-preserving** accumulation over the chunk sequence (embed row order = chunk_id order = doc order). A group closes when adding the next chunk would exceed `max_tokens_per_call` **or** the group would exceed `max_texts_per_call`.
- Pipeline passes `batch_size=len(group_texts)` into `batch_embed`, so the embedder sees one model call per group with `count ≤ 32` and `tokens ≤ ~16k`.
- Why these caps (Research + Inference): B≈16 @ L=512 fits the 4 GB card in fp16; B≈32 @ L=1024 is near-OOM. 16k tokens ≈ 32×512 — comfortably inside that envelope. **Inference**: these are arithmetic estimates from research.md — the pipeline must expose the knobs and a first-run smoke on the box must confirm (DoD check).
- **Decision on existing defaults (research risk "batch defaults 64/128"):** the chunk pipeline does *not* inherit `EmbeddingOptions.batch_size=128` or `ProcessingConfig.embed_batch_size=64`; it passes its own token-budget caps. As a follow-up code change (tracked, non-blocking for this architecture), recommend lowering `EmbeddingOptions.batch_size` → 32 and `ProcessingConfig.embed_batch_size` → 32 so the generic seams stop advertising OOM-risky defaults.

### 3.10 Embedder identity tightening (Recommendation, research Q4)

`SentenceTransformerEmbedder.name` must carry model identity, e.g. `f"{model_id}@{revision}-{('fp16' if fp16 else 'fp32')}"` with `revision` resolved from the local model dir / pinned config. This is an implementation change to `app/embedding/sbert.py` + tests (flagged; tracked in ADR-009 and the run's test pass, incl. the tracked `test_sbert_embedder.py` bge-small→bge-m3 fix). Architecturally required so `emb-v{chunker}-{embedder}` keys are unambiguous across models/dtypes.

---

## 4. Open-risk resolutions (explicit decisions, from research.md)

1. **fp16 bit-level nondeterminism vs the Embedder Protocol's determinism wording → policy: cosine-stable determinism (ADR-010).** The protocol's "deterministic (idempotent for a given model version)" is re-interpreted, by ADR, as: **bit-exact** for CPU/Dummy paths; **cosine-stable** (cosine ≥ 0.9999 vs a canonical re-embed, and L2-normalized storage) for GPU-fp16 inference. The pipeline records a per-doc validation sample (§3.8 step 7) and stamps the result into the emb sidecar. Rejected: fp32 compute (2× VRAM breaks the 4 GB budget and is still not bit-exact on GPU reductions — **Fact**), and `torch.use_deterministic_algorithms` as a blanket guarantee (unsupported ops raise, perf cost, not guaranteed across sentence-transformers internals — **Inference**; kept as an opt-in "strict" mode for audits only). Full trade-off in §5(e).
2. **Tables/figures outside `Block.text` → chunk Blocks only this run; atomic table/figure-caption chunks = documented next step.** `Page.tables` and `Page.images` are not in `reading_order` and are out of scope. The schema **reserves** `kind ∈ {"table_atomic", "figure_caption"}` and optional `source_table_ids` / `source_image_ids` fields so the step lands without a schema bump. Note: `caption` blocks (Docling maps them) *are* ordinary blocks and are chunked normally this run; the "atomic figure-caption" step is about binding the image + caption into one retrieval atom.
3. **Tokenizer pinning → pinned BGE BPE (primary) + char/4 heuristic (hermetic fallback), both deterministic, recorded in provenance** (§3.4).
4. **Batch defaults → token-budget caps (16k tokens / 32 texts) + documented lowering of the module defaults (128/64 → 32)** (§3.9). VRAM figures remain arithmetic estimates — validated on the box in the run's smoke pass.

---

## 5. Trade-off review (Decision Challenger folded in — each choice attacked)

### (a) Chunk boundary strategy

| Option | Faithfulness | Determinism | Decoupling | Retrieval quality | Cost | Complexity |
|---|---|---|---|---|---|---|
| **A1 DOM-anchored semantic** (chosen) | high (cuts at real semantics) | full (pure fn of DOM+config) | full (no embedder in chunking) | high (research Q1: semantic units; parent-anchor) | low | medium |
| A2 fixed-size / sliding window | low (splits headings/sentences; Research: halves faithfulness) | full | full | low-medium | ~1.2–1.5× tokens (Research) | low |
| A3 embedding-change boundary | medium | breaks (boundary depends on embedder → re-chunk on model change) | **breaks lineage** (chunk→embed coupling) | medium | extra embed pass (Research) | high |
| A4 paragraph-per-chunk | medium (fine, but no merging) | full | full | low-medium (context starvation, many tiny vectors) | high (vector count) | low |

**Chosen A1.** It is the only option that keeps chunking a pure, embedder-independent projection (required by the run contract and universal §8) while producing retrieval-grade semantic units. **Challenge (attack):** A1 inherits the parser's reading-order quality; the native heuristic is page-top-to-bottom (documented weakness for complex layouts — Fact from `reading_order.py`), and heading seams can yield thin chunks; the band-merge can drift sizes toward 768 rather than 400. These are quality knobs, not structural flaws. **What would change my mind:** a retrieval eval on this corpus showing A2 or A4 beats A1 on precision@k (unexpected vs the cited research); or measurement showing reading-order errors corrupt enough boundaries to warrant a layout-model pass (that belongs to the parser module, not chunking).

### (b) Chunk size / overlap policy

| Option | Retrieval precision/recall | Embedding cost | Token waste | Faithfulness |
|---|---|---|---|---|
| **B1 target 400 / band 256–768 / cap 2048 / 48-token sentence-aligned overlap at heading seams** (chosen) | balanced | moderate (≈2.5 chunks per 1000 tokens) | ~10% at seams only | full (sentence-aligned, attributed) |
| B2 large (1000–2000) | coarse, lower precision (context mixing) | low | low | medium (still fine-grained cuts) |
| B3 small (100–200) | fine but context-starved | 2–4× vectors | higher | medium |
| B4 no overlap | fine for ordinary seams | lowest | zero | cleanest join |

**Chosen B1** — matches research Q2 and BGE-M3's 8194 ceiling (hard cap → no truncation). **Challenge:** overlap at heading seams is a *retrieval hedge*, not a necessity — BGE's 8k context makes cross-chunk context cheap at query time, and every repeated sentence is embedded twice; the "only at heading seams" reading of the research is my interpretation of "section-boundary merges" (flagged in the doc so the implementer cannot guess). **What would change my mind:** a chunk-target sweep (256/400/768) on the run's eval showing a different optimum; or a retrieval scenario proving seam-overlap hurts (then flip `overlap_at_heading_seams=False`).

### (c) Storage / versioning scheme

| Option | Lineage retention | Idempotency | Per-doc isolation | Scale | Consistency w/ ADR #8 |
|---|---|---|---|---|---|
| **C1 versioned per doc × chunker/embedder, content-addressed chunk_id** (chosen) | full (every version retained) | full (deterministic overwrite; dedup by chunk_id) | full | fine at million-scale (per-doc files) | full |
| C2 single-slot overwrite per doc | lost (old versions destroyed) | partial | full | fine | **violates ADR #8** |
| C3 one global chunks file per version | full | full | none (shared-file contention) | write amplification | partial |
| C4 DB/vector store now | full | full | full | best | ahead of scope |

**Chosen C1.** Mirrors the audited `app/parser/storage.py` layout (ADR #8), keeps lineage append-only, and gives `iter_*` a natural scan path. **Challenge:** `.npy`+`.json` pairing adds a join cost (row order sidecar), `emb-` keys get long with `model@revision-dtype`, and per-doc files mean thousands of small files — the very shape the audit accepted for DOMs. **What would change my mind:** measured retrieval volume that justifies pgvector/Qdrant behind the `ChunkStore` seam (future ADR, explicitly anticipated in §3.7); an S3 backend that makes per-doc keys cheaper.

### (d) Batching policy

| Option | VRAM safety (4 GB fp16) | Throughput | Determinism | Complexity |
|---|---|---|---|---|
| **D1 token-budget ≤16k / ≤32** (chosen) | safe envelope (Research figures) | good (32 texts/call, GPU-resident) | full (order-preserving greedy) | low |
| D2 count-only 64/128 (today's defaults) | **OOM risk** (Research: B≈32@1024 near-OOM) | good until OOM | full | zero |
| D3 batch = 1 | safe | abysmal | full | low |
| D4 runtime VRAM probing | safest | adaptive | partial | high |

**Chosen D1.** It is safe *and* fast, deterministic by construction, and needs only the tokenizer we already pin. **Challenge:** the fixed 16k budget may under-utilize the GPU on short-text corpora (tokenizer-bound), and my VRAM envelope is arithmetic (Inference), not measured. **What would change my mind:** a measured OOM or measured under-utilization on a real corpus → tune `max_tokens_per_call`/`max_texts_per_call` (knobs, not redesign).

### (e) fp16 determinism policy

| Option | VRAM fit | Determinism strength | Implementation risk | Auditability |
|---|---|---|---|---|
| **E1 cosine-stable (chunk rows L2-normalized; validation sample ≥ 0.9999)** (chosen) | full (fp16 stays) | retrieval-grade stable; not bit-exact | low | high (validation stamped in artifact) |
| E2 fp32 compute | breaks 4 GB budget | still not bit-exact on GPU reductions (Fact) | low | medium |
| E3 `torch.use_deterministic_algorithms` + cuBLAS workspace | full | bit-exact *when supported* | high (unsupported ops raise; not guaranteed across sentence-transformers; perf hit) | high |
| E4 no policy (honest) | full | none | none | none (violates protocol wording + trust boundary) |

**Chosen E1**, codified in ADR-010: determinism is defined per-path (bit-exact CPU/Dummy; cosine-stable GPU-fp16), the protocol docstring is amended accordingly, and every emb artifact carries a sample-validation result. **Challenge:** E1 is weaker than the literal "idempotent" wording; a paranoid downstream could demand bit-exactness. E3 stays available as an opt-in "strict" mode for audits where bit-exactness matters and perf/op-support allow. **What would change my mind:** an audit requirement for bit-exact reproducibility as product policy → switch default to E3/fp32 with the VRAM consequence, or upgrade the GPU.

---

## 6. Trust-boundary & guardrail compliance

- **Idempotent:** content-addressed `chunk_id`; deterministic overwrite of same-version artifacts; never-embed-twice keyed on `chunk_id`.
- **Deterministic:** chunker is a pure function; tokenizer pinned; batching order-preserving; fp16 handled under ADR-010 (cosine-stable, documented, validated).
- **Faithful (None, never fabricated):** `text` is a join of real `Block.text`; empty/None blocks skipped and reported; orphan blocks appended + flagged; forced splits explicitly flagged; overlap spans attributed to their source chunk.
- **Provenance recorded:** `ChunkProvenance` (chunker version/params, tokenizer + hash, dom_storage_key, normalizer version, dom schema) + emb sidecar (embedder identity, dim, dtype, normalize, device, token budget policy, validation). Chain doc → DOM → block → chunk → embedding is fully reconstructible.
- **Modular monolith / decoupled consumer:** chunking depends only on the DOM + `Store` + `Embedder` protocol + `EventPublisher`; no parser/normalizer/processing edits required by the architecture (only the two flagged follow-ups in `app/embedding/`).
- **On-prem:** everything local; BGE tokenizer + model under `models/`; no telemetry (events → silent_sink in batch).

---

## 7. Testing strategy (hermetic + gated)

New `tests/test_chunking.py` (synthetic DOMs):
- boundary cuts: heading starts a chunk; merge-to-budget respects target/band; empty/None blocks skipped and reported; orphan appending.
- oversized block → sentence-split ≤ 2048, anchor inherited; forced-split flag on a single over-long sentence.
- overlap: applied only at heading seams, sentence-aligned, `overlap_source_chunk_id` set; disabled via config.
- determinism: two runs of the same DOM produce identical chunk JSON; `chunk_id` stable when text unchanged, changes when text/source changes; `seq`-independent of reordering-invariant hash.
- tokenizer: `bge-m3` mode vs `char4` fallback both deterministic; provenance records which.

New `tests/test_chunk_store.py`: round-trip put/get for chunks and embeddings; versioned keys (`chunks-v0.1.0.json`, `emb-v0.1.0-…`); deterministic overwrite idempotency; `iter_*` traversal.

New `tests/test_batching.py`: token-budget grouping respects both caps; order preserved; edge (single over-budget chunk → its own group).

New `tests/test_chunk_embed_pipeline.py` (DummyEmbedder only — hermetic): embeds only missing `chunk_id`s on a re-run (never-embed-twice); artifact shape (chunk_ids row order ↔ matrix rows); `embedding_ref` populated; event emitted.

Real-BGE test: **gated** on `models/bge-m3` availability — cosine-stable equality between two runs (≥ 0.9999) + dim/dtype asserts; plus the tracked fix `test_sbert_embedder.py` bge-small → bge-m3. Existing suite (31 + docling 35 baseline) must stay green — chunking touches nothing in parser/normalizer/processing/docling paths.

---

## 8. Out of scope this run / next steps (documented, not built)

- **Atomic table / figure-caption chunks** (`kind="table_atomic" | "figure_caption"` reserved in schema) — next module; will serialize the `Table` grid (header+rows) into an atomic chunk text and bind image+caption.
- **Parent-child retrieval / context injection** (`parent_chunk_id`, `heading_anchor` reserved) — retrieval module.
- **Vector store / hybrid retrieval** — `ChunkStore.iter_*` is the seam; pgvector/Qdrant behind it is a future ADR.
- **Splitting `_embed_pass` in `app/processing/cli.py`** — superseded by chunk-level embedding; left as legacy (no processing redesign).
- **Follow-ups tracked for the implementer:** tighten `Embedder.name` (§3.10); lower `EmbeddingOptions.batch_size`/`ProcessingConfig.embed_batch_size` → 32 (§3.9); fix `test_sbert_embedder.py` model; validate VRAM envelope on the box (§3.9, Inference→Fact).

---

## 9. What would change my mind (global)

1. A measured retrieval eval on this corpus showing a different chunk-size optimum or boundary strategy beats DOM-anchored chunking (would re-open §5a/5b).
2. An audit-level requirement for bit-exact reproducibility → default flips to strict mode/fp32 (ADR-010 revision).
3. Measured OOM or gross GPU under-utilization on real corpora → tune batching knobs (§5d).
4. Reading-order quality measured bad enough to corrupt boundaries → escalate to the parser module (layout-model path), not chunking.

---

## 10. Verdict

**ARCHITECTURE: APPROVED** — reasons:

- Directly implements the run contract and research recommendations (DOM-anchored chunking, ~400/2048/48 overlap, content-addressed `chunk_id`, versioned keys, `ChunkStore` seam, token-budget batching) with **every** major decision trade-off-reviewed (≥2 alternatives, scoring, explicit challenge).
- Resolves all four open risks from research with explicit, recorded decisions: cosine-stable fp16 determinism (ADR-010), Blocks-only chunking with table/figure step documented, tokenizer pinning, batch-default lowering.
- Stays inside the modular-monolith and trust boundary: decoupled consumer, no parser/normalizer/processing redesign; idempotent, deterministic-by-construction, faithful (never fabricated), provenance-recorded, on-prem.
- Only flagged follow-ups are `app/embedding/` (name tightening, defaults, test) — non-blocking, tracked, and no architecture rework implied.

None of the challenges in §5 and §9 amount to a blocking flaw; all are knobs or future ADRs behind defined seams.
