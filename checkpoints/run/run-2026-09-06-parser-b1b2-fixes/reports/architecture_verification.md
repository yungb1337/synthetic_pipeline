# Architecture Verification Report — Code-to-Execution Audit

**Source:** `docs/universal-document-understanding-engine.md` — §1.1, §1.2, §1.3  
**Verified against:** `app/parser/`, `app/chunking/`, `app/embedding/`, `app/routing/`  
**Date:** 2026-09-12

---

## §1.1 Flaws in the proposed pipeline

| # | Claim | Verdict | Evidence |
|---|---|---|---|
| 1 | Strict linearity, no feedback | ⚠️ PARTIAL | Retry + dead-letter exist (assembler: FAILED→DEAD, bounded retries). But `Block.confidence` is a field (default 1.0) that nothing acts on — no confidence-gated routing to human/LLM verification. The LLM judge audits but can't fix. |
| 2 | No OCR layer for images/scans | ✅ FIXED | Enrichment engine runs OCR; router's Enrichment band handles scans; OCR memory guard in place (ADR-013 Addendum 2). |
| 3 | No cross-document/corpus view | ⚠️ PARTIAL | `document_id` is stable, node IDs are stable (`{doc_id}/t{n}_{page}`). But no corpus-level entity registry or KG — questions.md says "decided" but KG/Ontology is not coded. |
| 4 | No verification / trust loop | ⚠️ PARTIAL | LLM judge exists (`llm_judge_parser.py`, 120-doc audit, 0 FAIL). But no feedback loop — judge can't override a DOM node and retrigger projections. It's a spectator, not a participant. |
| 5 | "Metadata" as a pipeline stage | ✅ FIXED | Metadata is a node attribute (`Document.metadata`, `Page.metadata`, `Block.metadata`), born during parse, not a pipeline stage. |
| 6 | No failure or progress model | ✅ FIXED | Assembler: retry + dead-letter + partial/failed/dead status. Scheduler: `ResourceGovernor` RAM-derived concurrency. Ledger tracks per-page status. |
| 7 | No incremental-update story | ❌ MISSING | `dom_schema_version` tracks schema version, not document version. No delta/incremental re-parse when a document changes (amendments). Re-running processes the whole doc from scratch. |
| 8 | Embedding and chunking force-jointed | ✅ FIXED | `app/chunking/` (content-addressed chunks) is fully decoupled from `app/embedding/` (Embedder Protocol + batch_embed). Chunks can be re-embedded independently. |

**Score: 4/8 fixed, 3/8 partial, 1/8 missing**

---

## §1.2 What's genuinely missing from the palette

| # | Claim | Verdict | Evidence |
|---|---|---|---|
| 1 | Reading order as a graph, not a single linear order | ⚠️ PARTIAL | `reading_order_full` carries blocks+tables+images in typed sequence. But it's still one linear order — no region graph for multi-column/sidebars/footnotes. `reading_order.py` does geometric column-aware ordering but doesn't model regions as first-class nodes. |
| 2 | Chunk spanning multiple DOM nodes | ⚠️ PARTIAL | Schema reserves `source_table_ids` + `source_image_ids` (chunking/schema.py:53-54). But chunker is block-centric — it walks `Document.reading_order`, cuts only between Block boundaries, doesn't join across nodes for semantic units (e.g., a claim = paragraph sentence + footnote). No "claim-level" chunk exists. |
| 3 | DOM is partial and fallible | ✅ DONE | Extensive `Optional` fields, unknown → None not fabricated. `reference_extractor` is guarded. |
| 4 | Tables as first-class objects | ✅ DONE | `Table` model has geometry, headers, rows, cell_bboxes, row_bboxes, column semantics. D5 fix committed. |

**Score: 2/4 done, 2/4 partial**

---

## §1.3 What's unnecessary / should be redesigned

| # | Claim | Verdict | Evidence |
|---|---|---|---|
| 1 | Collapse into two passes | ⚠️ FUZZY | Extractors run in parallel (not two explicit passes). Semantic layer (`reference_extractor`, `reading_order_full`) is additive, not a clean Stage 2 boundary. The intent is there but the enforcement isn't — nothing prevents Stage-3 creep. |
| 2 | Define DOM JSON schema v1 contract early | ⚠️ PARTIAL | Schema exists in `dom/models.py` with `dom_schema_version` in Provenance. But no formal contract document, no breaking-change gate. Downstream consumers don't explicitly pin to a schema version — a schema change could silently break chunker/embedder. |

**Score: 0/2 fully done, 2/2 partial**

---

## Deep-dive: §1.3 Claim 1 — "Collapse into two passes"

### What the doc says

> Collapse "objects / layout / logical structure / semantic" into two passes. Three separate "recovery" layers over-obscure the fact that much of layout is already semantic — a heading is a heading because of font + position, not just a bigger rectangle. Production systems use effectively two passes:
> - **Stage 1 (physical/layout):** objects, bounding boxes, reading order, cells, images, figures.
> - **Stage 2 (logical/semantic):** headings, list structure, entities, relations, footnotes, references, forms.
> Define the output contract early. Nail down the DOM JSON schema v1 and write a couple of sinks against it in the first sprint. Otherwise you'll rebuild every interface when the schema stabilizes.

### What the code actually does

The code has **no explicit Stage 1 / Stage 2 boundary**. Instead:

1. **Parallel extractors** (`app/parser/engines/`): `native_pdf.py`, `enrichment.py`, `heavy_docling.py`, `image.py`, `simple.py` — all run concurrently, each producing `PageResult` objects with blocks/tables/images/metadata.
2. **Additive semantic post-processors** scattered across the codebase:
   - `app/parser/dom/reference_extractor.py` — extracts references/bibliography (D3)
   - `app/parser/dom/reading_order.py` — builds `reading_order_full` (D4)
   - `app/parser/dom/builder.py` — assembles blocks, tables, images, reading order, references into the DOM
3. **No orchestration layer** that says "Stage 1 is done, now run Stage 2." The builder calls `reading_order.recover_reading_order()` and `reference_extractor.extract_references()` inline during assembly.

### What this causes — concrete example

**Scenario:** A 24-page academic survey with multi-column layout, footnotes, and a references section.

**What happens today:**
1. Native PDF engine extracts blocks with bboxes → `recover_per_page()` geometrically partitions columns → produces a flat block list in reading order.
2. Builder assembles the DOM — calls `reference_extractor.extract_references()` which scans for "References" heading + `[n]` patterns → populates `Document.references`.
3. `build_reading_order_full()` appends blocks, then tables, then images per page → flat `reading_order_full` list.
4. **No Stage 2 boundary means:** footnotes are treated as regular blocks in the linear chain. A footnote that belongs to paragraph P3 on page 5 is just another entry in `reading_order_full` — there's no `belongs_to` edge linking the footnote to P3.

**What goes wrong:**
- A downstream consumer (chunker) walks `reading_order` linearly and cuts at block boundaries. The footnote gets its own chunk, disconnected from the paragraph it annotates.
- A retrieval query for "what does the author mean in paragraph P3?" matches the paragraph chunk but misses the footnote that clarifies a key term.
- Adding a new "footnote detector" (Stage 3) would just add another post-processor, further blurring the boundary. Nobody knows which post-processor owns what.

### The fix

**Introduce a clean two-pass boundary in the builder:**

```python
# app/parser/dom/builder.py — proposed structure

class DocumentBuilder:
    def build(self, page_results, document_id):
        # STAGE 1: Physical/layout pass (existing, unchanged)
        pages = self._build_pages(page_results)  # objects, bboxes, cells, images
        reading_order = reading_order.recover_reading_order(
            [b for p in pages for b in p.blocks]
        )

        # STAGE 2: Logical/semantic pass (explicit boundary)
        semantic_context = SemanticContext(
            document_id=document_id,
            pages=pages,
            reading_order=reading_order,
        )
        self._extract_references(semantic_context)       # D3 → Document.references
        self._build_reading_order_full(semantic_context)  # D4 → Document.reading_order_full
        self._extract_entities(semantic_context)          # Future: Stage 3 entities
        self._resolve_footnotes(semantic_context)         # Future: footnote归属

        return Document(...)
```

**Key changes:**
1. `SemanticContext` object passes Stage 1 output to Stage 2 — no implicit global state.
2. Each semantic extractor is a method on `DocumentBuilder` (or a separate class) with a clear interface: `(SemanticContext) → mutation`.
3. Stage 3 additions (entities, relations, forms) go through the same interface — no more scattered post-processors.
4. The builder's `build()` method is the only place that orchestrates Stage 1 → Stage 2, making the pipeline's structure explicit and testable in isolation.

**What this prevents:**
- Adding a new semantic feature doesn't require understanding where it fits — it goes in Stage 2, period.
- Testing Stage 2 in isolation: pass a fake Stage 1 output, verify the semantic fields.
- The DOM schema stabilizes because Stage 1 and Stage 2 have explicit contracts (input/output types).

---

## Deep-dive: §1.3 Claim 2 — "Define DOM JSON schema v1 contract early"

### What the doc says

> Define the output contract early. Nail down the DOM JSON schema v1 and write a couple of sinks against it in the first sprint. Otherwise you'll rebuild every interface when the schema stabilizes.

### What the code actually has

- `dom/models.py` defines pydantic models (`Block`, `Table`, `Page`, `Document`, `Provenance`).
- `Provenance.dom_schema_version` tracks the schema version string.
- Downstream consumers (normalizer, chunker) import these models directly.
- **But:** no formal contract document, no breaking-change gate, no consumer pinning.

### What this causes — concrete example

**Scenario:** A developer adds a new required field to `Block` to support highlighted text:

```python
class Block(BaseModel):
    id: str
    kind: str = "paragraph"
    text: str = ""
    bbox: Optional[BBox] = None
    highlight_color: str = ""  # NEW FIELD — but what if old DOMs don't have it?
```

**What goes wrong:**
1. The chunker imports `Block` and calls `block.text` — this still works (new field has a default).
2. But the **normalizer** does `model_copy(deep=True)` and iterates over all fields to apply rules — the new `highlight_color` field is now part of every block's text processing, potentially corrupting text normalization.
3. A **downstream consumer** (e.g., a KG extractor added later) assumes `Block` has `highlight_color` and crashes on old DOMs that don't have it.
4. The `dom_schema_version` string in Provenance is `"v0.1.0"` — but nobody checks it at consumer startup. The chunker reads old DOMs with the new code and silently produces wrong output.

**The root problem:** `dom_schema_version` exists but is **advisory, not enforced**. No consumer pins to a minimum version, no migration path exists for old DOMs, and the schema is defined by code (pydantic models) rather than by contract (a JSON Schema or protobuf definition).

### The fix

**1. Formal DOM contract (JSON Schema):**

```json
// docs/dom-schema-v1.json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "MedFactory DOM v1",
  "type": "object",
  "required": ["version", "document_id", "source_hash", "pages"],
  "properties": {
    "version": { "const": "dom-v1.0.0" },
    "document_id": { "type": "string" },
    "source_hash": { "type": "string" },
    "pages": { "type": "array", "items": { "$ref": "#/definitions/Page" } }
  },
  "definitions": {
    "Page": {
      "type": "object",
      "required": ["index", "blocks"],
      "properties": {
        "index": { "type": "integer" },
        "blocks": { "type": "array", "items": { "$ref": "#/definitions/Block" } },
        "tables": { "type": "array", "items": { "$ref": "#/definitions/Table" } },
        "images": { "type": "array", "items": { "$ref": "#/definitions/ImageObject" } }
      }
    },
    "Block": {
      "type": "object",
      "required": ["id", "text"],
      "properties": {
        "id": { "type": "string" },
        "text": { "type": "string" },
        "kind": { "type": "string", "default": "paragraph" },
        "bbox": { "$ref": "#/definitions/BBox" },
        "confidence": { "type": "number", "minimum": 0, "maximum": 1 }
      },
      "additionalProperties": false
    }
  }
}
```

**2. Consumer pinning:**

```python
# app/chunking/chunker.py — validate DOM version before processing
from app.parser.dom.models import Document

class SemanticChunker:
    SUPPORTED_DOM_VERSIONS = {"v0.1.0", "v0.2.0"}  # pin what we accept

    def chunk(self, doc: Document):
        if doc.version not in self.SUPPORTED_DOM_VERSIONS:
            raise UnsupportedDOMVersion(
                f"Chunker supports {self.SUPPORTED_DOM_VERSIONS}, got {doc.version}"
            )
        # ... proceed
```

**3. Migration path:**

```python
# app/parser/dom/migrations.py
DOM_MIGRATIONS = {
    "v0.1.0": migrate_v010_to_v020,  # add missing fields with safe defaults
}

def migrate(doc: Document, target_version: str) -> Document:
    while doc.version != target_version:
        migrator = DOM_MIGRATIONS.get(doc.version)
        if migrator is None:
            raise NoMigrationPath(doc.version, target_version)
        doc = migrator(doc)
    return doc
```

**What this prevents:**
- A consumer crashing silently on old DOMs — it fails loudly with `UnsupportedDOMVersion`.
- Schema changes without a migration plan — every new version requires a migration function.
- Downstream breakage from additive fields — `additionalProperties: false` in the JSON Schema contract prevents unexpected fields from entering the DOM.

---

## Deep-dive: §1.2 Claim 1 — "Reading order as a graph, not a single linear order"

### What the doc says

> Reading order is a graph, not a single linear order. Multi-column layouts, sidebars, footnotes, figure regions resolve into one order only at consumption time. Model it as an ordering over regions; linearize for retrieval.

### What the code actually has

`reading_order_full` is a flat list. Per `reading_order.py:150-167`:

```python
def build_reading_order_full(pages) -> list[ReadingOrderEntry]:
    out: list[ReadingOrderEntry] = []
    for page in sorted(pages, key=lambda p: p.index):
        for b in page.blocks:
            out.append(ReadingOrderEntry(type="block", id=b.id))
        for t in page.tables:
            out.append(ReadingOrderEntry(type="table", id=t.id))
        for img in page.images:
            out.append(ReadingOrderEntry(type="image", id=img.id))
    return out
```

Per page: all blocks, then all tables, then all images. A single linear chain. `reading_order.py` does geometric column-aware ordering via `_partition_columns()` (recursive gutter analysis), but the output is still a flat list — no region nodes, no graph structure.

### What this causes — concrete example

**Scenario:** A medical journal article with a 2-column layout, a sidebar with a key finding callout, and a footnote attached to a paragraph in column 1.

**What happens today:**
1. `recover_per_page()` partitions the page into columns (left column = main text, right column = sidebar).
2. `build_reading_order_full()` appends all left-column blocks, then all right-column blocks, then tables, then images → a single flat chain.
3. The sidebar's key finding callout is buried at position 47 in the chain, interleaved with the main text.
4. A retrieval query for "key finding" matches the main text chunks but the sidebar callout is a separate chunk at position 47 — the retriever doesn't know it's a sidebar, so it can't boost it or surface it as a related region.
5. The footnote attached to paragraph P3 is just another entry in the chain at position 52 — there's no `belongs_to` edge linking the footnote to P3.

**Consequence:**
- A reader following the reading order encounters the sidebar content out of context.
- A retrieval system can't distinguish "main article text" from "sidebar callout" — both are just blocks in a flat chain.
- Adding a footnote detector later would just append another post-processor — no region model to attach it to.

### The fix

Model regions as first-class nodes in the reading order graph. Linearize for retrieval.

```python
# app/parser/dom/models.py — add Region model

class Region(BaseModel):
    """A semantic region of a page (column, sidebar, footnote area, figure region)."""
    id: str
    page: int
    bbox: BBox
    kind: str = "column"  # "column" | "sidebar" | "footnote" | "figure" | "table"
    block_ids: list[str] = Field(default_factory=list)

class Document(BaseModel):
    # ... existing fields ...
    regions: list[Region] = Field(default_factory=list)  # NEW
```

```python
# app/parser/dom/reading_order.py — build regions from column partition

def build_regions(page) -> list[Region]:
    """Partition page blocks into regions using geometric analysis."""
    # Use existing _partition_columns logic to identify column boundaries
    # Each column becomes a Region with kind="column"
    # Full-width spanning blocks (titles, headers) become Region(kind="header")
    # Isolated blocks below main content become Region(kind="footnote")
    pass
```

What this prevents:
- A footnote that belongs to paragraph P3 being just another entry in the flat chain — it's now linked to P3 via `region.kind = "footnote"` and `region.bbox` positioning.
- Multi-column documents: a reader can choose to follow column 1 or column 2 independently.
- Sidebars and figure regions are first-class, not just "blocks that happen to be in the right place."

---

## Deep-dive: §1.2 Claim 2 — "Chunk spanning multiple DOM nodes"

### What the doc says

> The "chunk" is a derived object, not a node. A semantic unit can span multiple DOM nodes (a claim = a sentence in one paragraph plus its footnote). Chunking must join across nodes, not just walk the tree.

### What the code actually has

The chunker walks `Document.reading_order`, cuts only between Block boundaries. Per `chunker.py:5-9`:

```
It walks Document.reading_order, cuts only between Block boundaries,
merges small blocks to a ~400-token budget, sentence-splits oversized
blocks (> 2048, the hard cap) under the current heading anchor.
```

The schema reserves `source_table_ids` + `source_image_ids` but nothing populates them for cross-node semantic joining.

### What this causes — concrete example

**Scenario:** A clinical trial document with this structure:

```
Paragraph P3: "The primary endpoint was all-cause mortality,
which was reduced by 40% (HR 0.60, 95% CI 0.45–0.81)."

Footnote F1: "p<0.01, Kaplan-Meier analysis, n=1,247."
```

**What happens today:**
1. The parser produces two DOM nodes: `Block P3` and `Block F1` (footnote).
2. The chunker walks `reading_order` and cuts at block boundaries → two chunks:
   - Chunk A: "The primary endpoint was all-cause mortality, which was reduced by 40% (HR 0.60, 95% CI 0.45–0.81)."
   - Chunk B: "p<0.01, Kaplan-Meier analysis, n=1,247."
3. A retrieval query for "mortality reduction" matches Chunk A (the claim).
4. But Chunk A alone is incomplete — the statistical evidence (p-value, method, sample size) is in Chunk B.
5. The retriever returns Chunk A, and the user sees "mortality reduced by 40%" without the "p<0.01, n=1,247" context. **This is a faithfulness failure** — the claim is technically true but misleading without its evidence.

**Consequence:**
- Chunks that span semantic units are split, breaking retrieval completeness.
- The `source_table_ids` / `source_image_ids` reservation is unused — the schema anticipates cross-node chunks but the chunker never builds them.
- A downstream KG extractor sees the footnote as a separate node and can't link it to the claim it supports.

### The fix

Cross-node chunking that joins semantic units across DOM nodes.

```python
# app/chunking/chunker.py — semantic unit joiner

class SemanticUnitJoiner:
    """Joins blocks that form a single semantic unit across node boundaries."""

    def join(self, blocks: list[Block], footnotes: list[Footnote]) -> list[SemanticUnit]:
        """
        For each block, check if it has an attached footnote.
        If so, merge the block text + footnote into one SemanticUnit.
        A SemanticUnit can span multiple DOM nodes.
        """
        units = []
        for block in blocks:
            unit = SemanticUnit(
                block_ids=[block.id],
                text=block.text,
                kind=block.kind,
            )
            # Attach footnotes that reference this block
            for fn in footnotes:
                if fn.references_block(block.id):
                    unit.text += f" [{fn.text}]"
                    unit.block_ids.append(fn.id)
            units.append(unit)
        return units
```

What this prevents:
- A retrieval query for a claim matching only the paragraph chunk but missing the footnote that clarifies a key term.
- Chunk boundaries that cut through semantic units, breaking the faithfulness of retrieval.
- The KG extractor seeing the footnote as a separate node and failing to link it to the claim it supports.
