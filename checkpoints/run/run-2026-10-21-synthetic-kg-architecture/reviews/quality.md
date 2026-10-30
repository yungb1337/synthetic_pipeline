# Quality & Performance Re-Review (Gate 6 — Post-Fix Round 1)

**Run ID:** `run-2026-10-21-synthetic-kg-architecture`  
**Target Modules:** Module #5 (`app/kg`), Module #6 (`app/synthetic`), Evaluation Suite (`eval/kg`)  
**Scope:** Re-review of code maintainability, test coverage, hermeticity, hardware envelope constraints, code duplication, and performance/scalability at enterprise scale.

---

## 1. Test Suite & Verification Results

### KG & Synthetic Focused Test Execution
Command:
`.venv/Scripts/python.exe -m pytest tests/test_kg_module.py tests/test_synthetic_module.py tests/test_kg_synthetic_e2e.py tests/test_eval_kg.py -q`
- **Result:** 45 passed in 1.48s (100% pass rate).
- **Hermeticity:** All tests run 100% offline. Zero neural weights are pulled from external repositories. Mock LLM clients, regex-gazetteer extractors, and heuristic fallback passes allow deterministic unit and integration verification in local/CI environments.

### Full Repository Regression Run
Command:
`.venv/Scripts/python.exe -m pytest tests/ -q`
- **Result:** 435 passed, 2 skipped (expected optional PDF fixtures and Docling fallback branch), 0 failures.
- **Regression Impact:** Zero regressions across existing parser, normalizer, chunking, or embedding modules.

---

## 2. Verification of Prior Review Findings

### [MAJOR-04] Vectorized Batch Assertion Lookup (Elimination of N+1 Queries)
- **Status:** RESOLVED
- **File:** `app/kg/store/store.py:246-262`
- **Verification:** In `DuckDBGraphStore.walk_paths()`, individual inner-loop assertion queries were replaced by a vectorized batch lookup:
```python
all_ast_ids = list({ast_id for row in results for ast_id in row[3]})
ast_map: dict[str, HopStep] = {}
if all_ast_ids:
    placeholders = ", ".join(["?"] * len(all_ast_ids))
    batch_query = f"SELECT id, subject_id, predicate, object_id, negation, population, dosage FROM assertions WHERE id IN ({placeholders})"
    for ast_row in self.con.execute(batch_query, all_ast_ids).fetchall():
```
Database round trips were reduced from $O(N \cdot H)$ to a single $O(1)$ query, resolving the bottleneck for multi-hop path extraction.

---

### [MAJOR-05] Bounded SimHash De-Duplication & Memory Protection
- **Status:** RESOLVED
- **File:** `app/synthetic/trust/dedup.py:75-86, 99-138`
- **Verification:** In `DeDuplicationGate`:
  1. Memory growth is strictly capped using `collections.deque(maxlen=max_window_size)` for `_seen_hashes` and `_seen_tokens`, alongside a synchronized `_seen_exact: set[int]` hash set.
  2. Bounded eviction is maintained in lockstep:
```python
if len(self._seen_hashes) == self._seen_hashes.maxlen and self._seen_hashes:
    evicted = self._seen_hashes[0]
    self._seen_exact.discard(evicted)

self._seen_hashes.append(fp)
self._seen_exact.add(fp)
self._seen_tokens.append(curr_tokens)
```
  3. Fast-path exact-match check executes in $O(1)$ before pairwise comparisons.
  4. Pairwise Hamming distance calculation utilizes Python 3.10+ POPCNT acceleration via `(hash1 ^ hash2).bit_count()`.
  5. Self-BLEU comparisons are restricted to a sliding sample of the last 20 tokens (`list(self._seen_tokens)[-20:]`), preventing quadratic comparison degradation at large scale.

---

### [MAJOR-06] Gemini API Secret Key Header Security
- **Status:** RESOLVED
- **File:** `app/synthetic/llm/api_client.py:116, 126-134`
- **Verification:** The API key was removed from the URL query parameters and is now passed securely in HTTP request headers:
```python
url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model_name}:generateContent"
req = urllib.request.Request(
    url,
    data=json.dumps(body).encode("utf-8"),
    headers={
        "Content-Type": "application/json",
        "x-goog-api-key": key,
    },
    method="POST",
)
```
This prevents credential exposure in HTTP error traces, proxy logs, and diagnostic telemetry.

---

### [MINOR-05] Deduplication of Structured Output Logic & JSONDecodeError Handling
- **Status:** RESOLVED
- **Files:**
  - `app/synthetic/llm/protocol.py:26-58`
  - `app/synthetic/llm/api_client.py:173-186`
  - `app/synthetic/llm/llama_cpp.py:107-120`
- **Verification:** Core schema serialization, code-fence extraction, and validation logic were unified into `structured_predict_helper()` in `app/synthetic/llm/protocol.py`. Explicit `json.JSONDecodeError` handling was added:
```python
try:
    data = json.loads(cleaned)
except json.JSONDecodeError as exc:
    raise ValueError(
        f"Failed to parse LLM structured output as valid JSON: {exc}. Raw snippet: {cleaned[:200]!r}"
    ) from exc
```
Both `APIClient` and `LlamaCppClient` now delegate to this shared implementation.

---

### [MINOR-06] Dynamic Seed Entity Limit in Subgraph Topology Sampler
- **Status:** RESOLVED
- **File:** `app/synthetic/sampler.py:29-37`
- **Verification:** The hardcoded `LIMIT 50` was replaced with a configurable parameter defaulting dynamically:
```python
limit = seed_limit if seed_limit is not None else max(50, count * 5)
seed_rows = self.store.con.execute(
    "SELECT DISTINCT subject_id FROM assertions ORDER BY RANDOM() LIMIT ?",
    [limit],
).fetchall()
```

---

### [MINOR-04] Duplicate DDL Schema Removal
- **Status:** RESOLVED
- **Files:**
  - Removed: `app/kg/duckdb_schema.sql`
  - Preserved canonical: `app/kg/store/duckdb_schema.sql`
- **Verification:** Verified that only the single canonical schema file in `app/kg/store/` exists.

---

## 3. Review Checklist Evaluation

### Duplication & Similarity Analysis
Command:
`.venv/Scripts/python.exe scripts/check_similarity.py`
- Flagged pairs:
  1. `app/synthetic/llm/api_client.py:173` vs `app/synthetic/llm/llama_cpp.py:107` (1.00): Both methods are 14-line dispatchers forwarding to `structured_predict_helper()`. The business logic is deduplicated in `protocol.py`.
  2. `app/synthetic/llm/protocol.py:80` vs `app/kg/extractor/protocol.py:19` (1.00): Both declare identical `@runtime_checkable` `Protocol` abstract signatures (`structured_predict`). This structural duplication is intentional and decoupled, keeping `app/kg` entirely free of imports from `app/synthetic` (verified 0 cross-module imports via grep).
  3. `app/synthetic/trust/entailment.py:91` vs `app/synthetic/trust/faithfulness.py:96` (0.44): Acceptable structural similarity conforming to the `TrustGate.evaluate()` protocol signature.

### Testability & Hermeticity
- 45 focused tests run in 1.48s.
- Full regression suite passes cleanly (435 passed, 2 skipped, 0 failures).
- Unit tests cover all newly implemented behaviors:
  - `test_structured_predict_helper_decode_error` verifies malformed JSON handling.
  - `test_sampler_dynamic_seed_limit` tests configurable seed limits.
  - `test_trust_gates_protocol_conformance` tests `TrustGate` interface conformance across all 6 gates.
  - `test_population_homogeneity_filter` tests CTE population filtering and cycle prevention.
  - `test_gate4_privacy_scrub` verifies HIPAA pattern detection and physician name recognition.

### Maintainability & Clean Architecture
- **Layering Integrity:** Grep confirms zero references to `app.synthetic` inside `app/kg`.
- **Standardized Gate Schema:** All 6 trust gates implement `TrustGate` and return `GateResult(passed, score, message, metadata)`.
- **Typing & Validation:** All schemas use Pydantic models with complete type annotations.

### Performance & Scalability Envelope
- **Hardware Constraints:** Tested on Windows 11, 16.5 GB RAM, 4 GB VRAM (RTX 3050).
- **GPU Lock:** `_GPU_LOCK = threading.Lock()` in `app/synthetic/llm/llama_cpp.py:23` enforces single-model GPU residency, preventing Out-Of-Memory (OOM) faults.
- **CPU Offload:** Information extraction (GLiNER) and NLI inference (DeBERTa) explicitly default to CPU execution (`device="cpu"`), preserving VRAM for generative model inference.

---

## 4. Ordered Issues

No blocking, major, or minor issues remain.

---

VERDICT: PASS
