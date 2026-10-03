# Universal Document Understanding Engine — Production Architecture

**Status:** Foundation design · **Scope:** end-to-end platform from byte intake to LLM / RAG / Knowledge-Graph consumers.

---

## 0. TL;DR — the one decision that shapes everything

Your proposal is a *linear, strictly-layered* pipeline. The single most important architectural correction:

**The DOM is not a step in a pipeline — it is a *lazy, queryable, materialized-on-demand* object graph with a stable schema, and the pipeline is only one path through it.**

Every serious platform in this space (Azure Document Intelligence, Google Document AI, Amazon Textract, Marker, MinerU) converges on the same shape: they emit a **rich structured payload** (blocks / spans / entities / relations), and the downstream (chunking → embedding → retrieval) is a *consumer* of that payload, not the same process. If you bake chunking + embedding into the "parser," you couple ingestion throughput to embedding cost, and you create a system that cannot serve "give me the DOM only" (reconstruction, synthetic data, KGE) without paying for vectors you may not need.

**Recommended shape:**

```
  Raw bytes
    → [type detection]                      (deterministic, cheap, no ML)
    → [format-specific parser]              → DOM Stage-1: objects + layout + reading order
    → [semantic layer]                      → DOM Stage-2: logical/semantic structure, entities, relations
    ────────────────────────────── DOM = the single source of truth (durable artifact) ─────────────
    → [materializers / consumers]
          • chunker (+ embeddings)          → vector store + keyword index
          • knowledge-graph projector       → graph store
          • SQL / analytics cube            → DuckDB / Parquet
          • LLM context generator           → ad hoc
          • reconstruction / diff lenses
```

The pipeline is *one consumer*. Everything downstream of the DOM is a **projection**. Cache projections; never store them as the truth.

That framing is what makes "reconstruction / knowledge graph / synthetic generation" effectively free: they are all projections over the same source of truth.

---

## 1. Architecture critique — what's missing, what's wrong, what to redesign

### 1.1 Flaws in the proposed pipeline

| # | Flaw | Consequence | Fix |
|---|------|-------------|-----|
| 1 | **Strict linearity, no feedback** | A parser that mis-reads never heals downstream; a low-confidence OCR block permanently poisons chunks and the KG. | Make **confidence a first-class edge**. Any node below threshold routes to human/LLM verification; *reparse is a first-class operation*, not a bug. |
| 2 | **No OCR layer for images / scans** | Pure-text-born files only; scans and photos fail. | OCR is a **parser backend** (sits between bytes and layout), chosen by detected file type — not a separate stage. |
| 3 | **No cross-document / corpus view** | Entities, KG, and RAG are only useful *across* documents; every projection needs IDs that survive reconstruction. | A canonical **`document_id` + stable `node_id`** scheme, used everywhere. |
| 4 | **No verification / trust loop** | Production has a "was this correct?" signal. Yours doesn't. | provenance + confidence + a feedback channel (human or LLM-as-judge) that can override a node and retrigger projections. |
| 5 | **"Metadata" as a pipeline stage** | Metadata is *per-node*, born during parse, not a process. | **Metadata is a node attribute / schema**, not a stage. |
| 6 | **No failure or progress model** | Real systems need retries, dead-letter queues, partial success. | Modular, monitored, idempotent stages. |
| 7 | **No incremental-update story** | Documents change (contracts get amended). | **Version the DOM**; consumers project from a version. |
| 8 | **Embedding and chunking force-jointed** | Cost and flexibility both suffer. | Decouple. |

### 1.2 What's genuinely missing from the palette

- **Reading order is a graph, not a single linear order.** Multi-column layouts, sidebars, footnotes, figure regions resolve into *one* order only at consumption time. Model it as an ordering over regions; linearize for retrieval.
- **The "chunk" is a derived object, not a node.** A semantic unit can span multiple DOM nodes (a claim = a sentence in one paragraph *plus* its footnote). Chunking must *join* across nodes, not just walk the tree.
- **Cross-format normalization can't preserve everything.** Do not force every format to fill every DOM field. The DOM is **partial and fallible** — unknown becomes `null`/`undefined`, never fabricated.
- **Tables are first-class objects, not paragraphs.** A table has geometry, a header row, column semantics, merged/spanning cells, and its own reading order. Commit to that now.

### 1.3 What's unnecessary / should be redesigned

- **Collapse "objects / layout / logical structure / semantic" into two passes.** Three separate "recovery" layers over-obscure the fact that much of layout is already semantic — a heading *is* a heading because of font + position, not just a bigger rectangle. Production systems use effectively two passes:
  1. **Stage 1 (physical/layout):** objects, bounding boxes, reading order, cells, images, figures.
  2. **Stage 2 (logical/semantic):** headings, list structure, entities, relations, footnotes, references, forms.
- **Define the output contract early.** Nail down the DOM JSON schema v1 and write a couple of sinks against it in the first sprint. Otherwise you'll rebuild every interface when the schema stabilizes.

---

## 2. File Type Detection

### 2.1 Strategies compared

| Strategy | Reliability | Cost | Notes |
|---|---|---|---|
| **File extension** (`.xlsx`) | ~60–70% | trivial | Users lie; renamed or re-encoded files are mislabeled. |
| **MIME type** (from browser/OS) | ~65% | trivial | OS/browser dependent; often `application/octet-stream`. |
| **Magic bytes / signatures** | ~98% | trivial | `%PDF-`, `PK\x03\x04` (ZIP), `\x89PNG`, JPEG `FF D8`, `{\rtf1`, `<?xml`, `<html`. |
| **Container inspection** (open as ZIP) | ~99% for ZIP-backed | low | Check `[Content_Types].xml` and `word/` vs `xl/` vs `ppt/` vs `META-INF/`. Disambiguates DOCX/XLSX/PPTX/EPUB. |
| **Internal XML inspection** | fills gaps | low+ | Read the root element: `<w:document>`, `<spreadsheet>`, `<html>`, `<svg>`. |
| **Content sniffing / heuristics** | ~90% for text | up to trigrams + classifier | Distinguishes CSV / TSV / JSON / XML / Markdown / plain text by content shape. |
| **Rendering / preview** | low | expensive | Almost never used for detection. |
| **Protocol / content-type from server** | low | — | `Content-Type` from a CDN, not file-level truth. |

**Enterprise practice:** Apache Tika, `libmagic`/`file`, and the cloud platforms (Azure Doc Intelligence, Google Document AI) all combine *signature + container + extension + content*. They ship a **signature registry with confidence scores** plus a **classifier step** for the ambiguous text subset. They do not bet on a single signal.

### 2.2 Recommended production approach

A **layered, scoring cascade**:

1. **Magic bytes first** — the primary signal. Verbatim signatures, then ZIP-container probing, then offset-based signatures.
2. **ZIP inspection** — the dispatcher for all ZIP-backed formats, checking content types / part prefixes.
3. **Content classifier** — used *only* for genuinely ambiguous text files (CSV/TSV/JSON/XML/HTML/MD/plain), after charset detection.
4. **Extension** — used *last*, as tie-breaker and a "detector of lying users."

Return a **`MIME + subtype + confidence + probe`** tuple, always alongside `extension_declared`.

### 2.3 When signals disagree

- **Score each signal** (e.g. magic 0.9, container 0.8, MIME 0.4, extension 0.3 — weights tuned on your corpus).
- Signals agree → accept.
- **Strong (`magic`/`container`) disagree with weak (`extension`/`MIME`) → trust strong.**
- Two *strong* signals disagree (e.g. a `.docx` whose ZIP contains `xl/`) → **treat as `Unresolved`**, route to a review queue, or run both parsers and let DOM confidence arbitrate (the two layouts will differ visibly).
- Keep an explicit `Unresolved` state; **never guess.**
- Record `detected_type` vs `extension_declared` as metadata — useful for security (MIME smuggling, spoofed eke) and for pipeline analytics.

### 2.4 Why enterprise platforms don't overdo it

Because magic bytes + ZIP introspection cover 90%+ of real-world files, deterministically, near-zero cost. Invest in the **signature registry and container introspection**; spend classifier effort only on the genuinely ambiguous text-ish tail.

---

## 3. The Document Object Model (DOM) — complete schema

### 3.1 Tree vs Graph vs Hybrid

- **Tree** — the natural containment hierarchy (`Document → Page → Section → Paragraph/Sentence`); cheap to build, ordered traversal, directly supports chunk boundaries and reading order.
- **Graph** — needed for *cross-cutting* structure: a footnote cited from two paragraphs, an entity mention pointing to a canonical member, cross-document links, dedup. A tree can't express these without duplication.
- **Hybrid (recommended):** a **primary tree for containment and order**, plus a **separate edge set (adjacency lists)** for cross-references. Each node holds `parent`/`children` (tree) and `edges` (graph references) via global node IDs.

This matches how real systems represent documents: a container tree for structure, a reference graph for semantics.

### 3.2 Node attributes — every node carries

- `id` — globally unique (e.g. `doc:<id>:node:<path>`).
- `type` — enum (below).
- `content` — extractive text / literal from source (verbatim), or `content_ref` into source.
- `confidence` — [0,1] from parser/OCR.
- `bbox` + `page` + source coordinates.
- `reading_order` — page-local index.
- `parent` / `children` — tree links.
- `style` — font, size, weight, color, alignment.
- `language` — detected or inherited from OCR metadata.
- `embedding_ref` — deferred; a projection, not a node payload.
- `entity_ids` — filled by Stage 2 / consumption.
- `relations` — semantic edges, filled by Stage 2.
- `provenance` — parser + version that produced this node.
- `metadata` — free-form dict (see §9).
- `is_authoritative` / `is_derived` — synthesized vs from-source.
- `revision` — document revision that produced this node.

### 3.3 Node taxonomy (enum)

`Document, Page, Section, Heading, Paragraph, Sentence, Table, Row, Cell, Image, Figure, Caption, Chart, Equation, List, ListItem, CodeBlock, Quote, Footnote, Reference, Link, Annotation, Form, FormField, Signature, Sidebar, TOC, MathRegion, TemplateVariable, MetadataBlock`.

### 3.4 Schema draft (concise JSON)

```json
{
  "schema_version": "1.0",
  "document_id": "d74f…",
  "id": "d74f…/node/p1",
  "node_type": "Paragraph",
  "content": "The cat sat on the mat.",
  "bbox": {"x0":10,"x1":520,"y0":25,"y1":40,"page":1},
  "confidence": 0.98,
  "language": "en",
  "style": {"font":"Helvetica","size":11,"bold":false,"align":"left"},
  "parent_id": "d74f…/node/sec2",
  "children_ids": [],
  "reading_order": 12,
  "entity_ids": [],
  "relations": [{"rel":"cites","target":"d74f…/node/fn4"}],
  "revision": 1,
  "provenance": {"parser":"pdf-layout-v0.3","ocr":false,"parser_version":"1.2.0"},
  "metadata": {"citation_style":"IEEE"}
}
```

---

## 4. Layout recovery & reading order — production approaches

### 4.1 Layout vs reading order

- **Layout** — what regions exist and where: text blocks, columns, cells, figures, captions — in *space*.
- **Reading order** — the *sequence* a human (or model) consumes them.

Layout recovery gets you regions; a separate step resolves their order.

### 4.2 The field today, compared

| Method | Class | Strengths | Blind spots |
|---|---|---|---|
| **Bounding boxes / connected components** | classical | trivial, no ML | no semantics; still need word/line inference |
| **Whitespace analysis / XY-Cut** | heuristic | cheap, robust, great for multi-column | assumes separable columns; struggles with dense/dirty scans |
| **Recursive partitioning** | heuristic | hierarchical regions | error propagation |
| **Reading-order graphs** | graph-based | handles cross-column references | needs construction heuristics |
| **Vision Transformer (ViT)** | heavy ML | strong on figures and complex regions | expensive; needs a rectified crop |
| **LayoutLM / LayoutLMv2 / DocFormer** | layout pre-training | state-of-the-art on forms and layouts | **single-page**; needs a text/OCR input channel |
| **DiT (Doc-Image Transformer)** | large —document backbone | strong on charts/tables from image | heavyweight |
| **Donut (OCR-free)** | end-to-end image→structure | no OCR step; good forms | big GPU; less mature on complex tables |
| **Nougat** | academic / Math → Markdown | excellent markdown structure + captions | PDF-focused; GPU |
| **Marker / MinerU** | pipeline (layout + OCR) | robust mixed-content output | more infra to run |
| **PyMuPDF / pdfplumber-only** | library | fast, free, deterministic | no deep semantics |
| **Azure Doc Intelligence / Google Document AI / Textract** | managed API | production-grade, tuned, well-supported | proprietary / priced per call; opaque internals |

### 4.3 Recommended stack (pragmatic)

- **Fast path** for clean digital PDF / DOCX with extractable text: **PyMuPDF + XY-Cut + whitespace grouping** → blocks, columns, tables — no GPU, deterministic.
- **Scan / mixed-content path**: an **OCR backend** for text, then a **layout model (LayoutLMv2 / DiT / MinerU)** on the rectified regions to get structure when whitespace/XY-cut can't.
- **Paper / math / strong-captions path**: a **Nougat-or-Marker-class** front-end for structure extraction, then normalized into your DOM.

**Rule:** never bet a single model on all types. Compose: a deterministic physical pass as the throughput backbone, heavier layout ML only for the regions where clean heuristics fail. Enterprises contrast here only because they run proprietary end-to-end stacks for their APIs — but they still internally do *physical-then-semantic*.

---

## 5. PDF internals — implementation depth

### 5.1 Object model & syntax

- 8 object types: **boolean, integer, real, string, name (`/Type`), array, dictionary (`<<…>>`), stream**, plus `null`.
- **Indirect objects**: `N G obj … endobj` where `N` = object number, `G` = generation. **Streams** are indirect objects whose dictionary carries `/Length` and optional `/Filter` (Flate, LZW, ASCIIHex…), plus `/Length1..3` for the raw-stream variant.
- **Cross-reference table (`xref`)** or **xref stream (`/Type /XRef`)** maps {obj#, gen#} → byte offsets. PDF 1.5+ prefers **xref streams**, which are themselves compressed objects.

### 5.2 Document hierarchy

`/Root` → `/Catalog` (`/Type /Catalog`) → `/Pages` (a nested page-name tree of `Page` and page-node dicts) → **Page**. Each Page carries `/Contents` (the content-stream), `/Resources` (Fonts, Images, XObjects), `/MediaBox`, `/Rotate`, `/Parent`. The nested-tree `Pages` structure IS your object hierarchy — never need to bootstrap it from scratch.

### 5.3 Content-stream operators (the meat)

| Operator | Meaning |
|---|---|
| `BT` / `ET` | Begin/End a text object |
| `Tf` | Set font and size, e.g. `Tf /Helvetica 12` |
| `Td` / `TD` / `T*` | Move text position (relative) / move with leading / next line |
| `Tm` | Set the full text matrix (general placement) |
| `Tj` / `TJ` | Show text / show text array with per-glyph offsets (kerning) |
| `BMC` / `EMC` | Begin/End marked-content (structurable boundaries) |
| `q` / `Q` | Save / restore graphics state |
| `cm` | Current transformation matrix (rotation, scale) |

### 5.4 Recovering text — glyph → word → line → paragraph

1. **Interpret the content stream.** Inside each `BT…ET`, accumulate the text matrix (`Tf`, `Td`, `Tm`, `T*`) so every glyph has an *absolute (x, y)* and font. Handle `TJ` arrays where embedded offsets re-wrap kerning/backspacing. Track the graphics stack so transforms don't leak.
2. **Chars → words.** Two glyphs on the same baseline belong to the same word if the gap is below a threshold measured in **font metrics (advance widths)**, not fixed pixels. This handle proportional fonts correctly.
3. **Words → lines.** Cluster words whose baseline `y` is equal within epsilon and whose `x`-intervals do not overlap; record the line's origin (left / center / right) for alignment.
4. **Lines → paragraphs.** Break a paragraph when vertical gap > ~1.5–2 line-heights, when baseline indents, when alignment changes, or when a style change (font/size/weight) signals a bound. Use XY-Cut gap distance on line bounds, plus font transitions, to detect paragraph and page-boundary starts.

### 5.5 Recovering structure beyond text

- **Reading order** — single column is natural. Multi-column: group lines into column boxes (XY-Cut), order columns left→right then top→bottom; refine with a reading-order graph for exotic columns / marginalia. For true gutter-crossing reading, a model (reading-order graph with learned weights) helps.
- **Tables** — recover the grid via: vertical/horizontal **rule detection** (stroke colors/widths), **text alignment** (columns that share top-x positions), or a **cell-aware model** when scans are involved. Best result is *post* text-extraction: cluster words into cell (columns, rows) by gaps, then verify geometry against rules.
- **Figures & captions** — associate by **proximity** (caption box closest below/above the image region) combined with reading order. Hardest part is *segmenting the figure block* from body text; use segmentation/contiguity rather than pure gap heuristics when possible.
- **Headings / hierarchy** — use **font size + weight + position + style** → map to `H1..Hn`. When the PDF ships a **document outline** (`/Outlines` + named `/Dest`), you get a hand-authored table of contents — trust it; it's far more reliable than inference.
- **Incremental updates** — PDFs are append-only. The newest trailer `StartXref`/`prev` chain sits at the end. Base generations are superseded by newer generations of the same object numbers. A correct parser reads the **last** trailer first and rebuilds the merged xref — a naive open that trusts the first `xref` it sees is broken.
- **Compression** — text streams nearly always Flate. Modern PDFs compress *objects* via **object streams** (`/Type /ObjStm`), and images via JBIG2/JPEG2000/Flate. A parser must decompress recursively.

### 5.6 Modern vs naive parsers

- **Naive**: regex its over `Tj`, no position tracking → garbled on multi-column, rotation, kerning, tables.
- **Correct/typed** (MuPDF-family/`pymupdf`, `pdfplumber`, Marker): a full **content-stream interpreter** with correct matrix algebra (include rotation/skew), glyph-metric word-joining, a **rasterized page + layout model** for structure, and tables/captions/headings recovered via the layout pass.

---

## 6. Other file formats — storage, semantics, difficulty, integration

Each: storage form → logical structure → semantic content → parse difficulty → integration into the common architecture.

### 6.1 DOCX (OpenXML Word)

- **Storage:** ZIP with `[Content_Types].xml`, `word/document.xml`, `word/_rels/document.xml.rels`, `word/styles.xml`, `word/media/*`, and sibling parts for headers/footers/footnotes. Relationships (rels) map IDs → parts.
- **Logical structure:** `w:sectPr` (page settings), `w:p` (paragraph) → `w:r` (runs) → `w:t` (text)/`w:drawing` (images), `w:tbl` → `w:tr` → `w:tc` (merged via `gridSpan`/`vMerge`). Styles (`w:style`), numbering (`numPr`), bookmarks (`bookmarkStart/End`), comments (in `word/comments.xml`), tracked changes (`w:ins` / `w:del`).
- **Semantic:** styles name headings (Heading 1 → Section). Reading order = XML document order (matches UI order). Images link via rels.
- **Difficulty:** medium-high (schema-rich; need style-to-semantics mapping, merges, images).
- **Integration:** map XML tree to the DOM tree directly (paragraphs → Paragraph; headings → Heading via style; runs → inline `content` sub-range). 
 it is the *cleanest* mapping of all formats.

### 6.2 XLSX (Excel)

- **Storage:** ZIP: `xl/workbook.xml`, `xl/worksheets/sheet*.xml`, `xl/sharedStrings.xml`, `xl/styles.xml`, `xl/_rels`, `xl/charts/*.xml`.
- **Logical:** each sheet is `<row r>` → cell `<c r="A1" t="…">` with `<v>` (typed `t` values: shared string `t="s"`, number, formula `t="str"` with cached result); `mergedCells`; `charts`; `pivotTables`; `hidden` per sheet; `definedNames` (named ranges); formulas in `<f>` with cached value in `<v>`.
- **Semantic:** a worksheet is essentially one big grid **table**; merge regions become cell spans; named ranges give semantic labels; formulas encode value dependencies. Charts are rich visual objects.
- **Difficulty:** **Medium** — shared strings, merges, styles, formulas, and number formatting all want a mature library.
- **Integration:** each worksheet → a `Table` (with rows/cells); each chart → a `Chart` node; named ranges and links → `Relation` edges.

### 6.3 PPTX (PowerPoint)

- **Data:** ZIP: `ppt/presentation.xml`, `ppt/slides/slide*.xml`, `ppt/slideLayouts/*`, `ppt/slideMasters/*`, `ppt/media/*.`
- **Logical:** each slide has `p:spTree` of shape elements: `p:sp` (shape, with text in `p:txBody`), `p:grpSp` (groups), `p:pic` (images), `p:graphicFrame` (charts), plus `p:notes` (speaker notes) and `p.timing` (animations). Placement is `a:off`/`a:ext` (coord + extents); shapes may have a `z-order`.
- **Reading order:** *not* inherent in list order — **visual reading order only comes from layout** (XY-Cut/ordering over shapes), not from the shape list. This is the PPT-specific catch.
- **Difficulty:** **Medium** — shape tree, groups, notes, z-order, reading order.
- **Integration:** each Slide → a `Page`/`Section`; shapes with text → Paragraphs; groups → Section-like containers; images → Figure/Image; notes → an overlay `Annotation` node; **run layout recovery for reading order** rather than trusting the shape list.

### 6.4 Flat / other formats (summary)

| Format | Physical form | Logical structure | Difficulty | Semantic promise |
|---|---|---|---|---|
| **HTML** | text + markup | tags ARE structure (`<h1>`, `<table>`, `<li>`, `<a>`); parse with an HTML parser | Low | headings, lists, links, metadata |
| **XML** | element tree | XML tree maps ~1:1 to your DOM tree | Low | custom tags preserve domain semantics |
| **JSON** | bracket tree | nested objects → nested DOM tree; `JSON` is recursive by nature | Low | keys are semantic labels |
| **CSV / TSV** | tabular plain text | one table; first line = header if it looks like one (heuristic) | Medium (delimiter detection) | whole file = a `Table` node |
| **Markdown** | text | headings (`#`), lists, code fences, links; use a proper parser | Low | headings/lists/code give clean DOM seams |
| **EPUB** | ZIP (`META-INF/`, OPF spine) | parse OPF + XHTML → chapters as `Section` | Low–Med | chapters + headings |
| **RTF** | text `{\rtf…}` | RTF groups `{…}` + escaped control words/phrases | Low–Med | bold/italic; headings unreliable |
| **ODT / ODS** | ZIP + ODF XML | near-OpenXML parallel | Med | same as DOCX/XLSX |
| **Log files** | plain text | time-anchored lines → a `Log` table (regex/time-parsing) | Low | — |
| **Images / scans PNG/JPG/TIFF** | pixel data | render → OCR + layout → same DOM pipeline as a scanned PDF | Med | visual text + figures |
| **Source-code repos** | directory tree | treat as a *corpus*, not a single file: a `Repo` root node → files as `CodeBlock`/`Module` children | Med | code + comments (future) |

**Note on image formats:** they're not "text" inputs; their whole value is OCR → layout → visual/figure structure, so in the architecture they join the same pipeline as scanned PDFs, going through the OCR backend.

---

## 7. Semantic layer (Stage 2) — logical & semantic structure

Given a physical tree from Stage 1, Stage 2 decides *meaning*:

1. **Deterministic rules first** (cheap, deterministic): font+position ⇒ heading/list/paragraph; whitespace/rule detection ⇒ table cells; indentation ⇒ lists.
2. **Entity recognition + relationship extraction** (for RAG/KG): run NER (compact local model like spaCy-`en_core_web_md`, or an LLM for higher quality), derive typed entities (`entity_ids` on nodes) and relations (co-occurrence, coreference, syntax) → graph edges.
3. **Domain classifiers** (layout-aware, e.g. LayoutLMv2-class or Donut) for classes like *invoice, bank statement, medical record, form field* — the precise structure for your domain, if you have a labeled dataset.
4. **LLM-assisted structured decode** (spend tokens only where it pays): for ambiguous, high-value structures (a contract clause, a claim + its citation), run an LLM over the *chunk*, ask it to emit JSON, and **validate against the DOM** (does the extracted value align with the node's content? keep it; mismatch → send to the shadow queue). Cache the result, `confidence`-gate it.

The key is **gating**: models that are expensive (LLM, OCR model) run only on the regions the deterministic pass couldn't confidently solve — and their outputs are overridable, versioned, annotated.

---

## 8. Chunking (the first consumer)

Recall: chunking is a *consumer* of the DOM, decoupled from the parse.

### 8.1 Strategies compared

| Strategy | Adopt? | Rationale |
|---|---|---|
| Fixed-token / sliding-window | ❌**| no semantics; splits headings mid-unit |
| Paragraph | ⚠️ fallback | fine, but can break beside headings |
| Section / heading-aware | ✅ | clean semantic grouping, preserves hierarchy |
| **Layout-aware (DOM-driven)** | ✅ **primary** | cuts at heading/table semantics boundaries; sentence-aligned |
| Embedding-change (breaking ) | ⚠️ | useful to *verify* boundaries, not decide them (extra embeddings) |
| Graph / hierarchy-aware | ✅ | for KG/RAG driving parent→child |
| Adaptive (per format/domain) | ✅ | sensible defaults per type |
| Context-preserving (inject headings, footnote, caption) | ✅ | fills cross-node context for tables/figures |
| **Parent-child retrieval** | ✅ strong | child chunk for LLM, parent for document context |
| Recursive | ⚠️ | only to preserve natural blocks (paragraph) |
| Agentic (LLM-determined) | ❌ | indeterminate cost, nondeterministic; rare manual only |

### 8.2 Recommended

**DOM-anchored, hierarchical chunks with parent-child retrieval:**

```
Walk leaf text in reading order → group into chunks that:
  • never break a Sentence
  • prefer boundaries at Heading / Paragraph / Table
  • fall back to ListItem / Row / Cell
  • respect a hard token budget per chunk
  • attach parent context (enclosing Section/Heading) to help the LLM
```

At retrieval: embed the **child** chunk for recall; for the LLM prompt, feed the **parent + child** (broader window). This is the classic _child-snippet-with-parent-context_ pattern and it measurably helps RAG on long documents.

Defaults algorithm sketch:
1. Collect leaf text in reading order (DOM tree → list).
2. Cut ONLY ± at `Heading` / `Paragraph` / `Table` / `ListItem` boundaries.
3. If a node exceeds the token budget, split at sentence boundaries (keeping the register + parent anchor).
4. Set `parent_id`, `section_id`(control — the DOM node of the enclosing heading), `reading_order`, `page`.
5. Emit `chunk_id`, start/end DOM node IDs, page, section for both chunks and parent-context.

### 8.3 Failure cases you design around

- **Don't split a table** (you lose the merge + semantics) → cut the table at a whole table, or represent the table as atomic.
- **Don't separate a figure + its caption**.
- **Don't break a list item**.
- **Don't split at a page number that actually continues a sentence** (you walk DOM object edges, not character indexes → you avoid these by construction).
- A 2000-token paragraph (a single DOM node) → *overflow* → split at sentence boundaries keeping the parent anchor.

---

## 9. Metadata schema

Metadata is **not a stage**; it's a schema-of-record per node (§3.4) plus a document-level block:

```jsonc
{
  "doc_id", "original_filename", "size_bytes", "sha256",
  "detected_type", "detected_version", "extension_declared",
  "confidence", "language", "parser_version", "ocr_engine", "ocr_confidence",
  "page_count", "has_bookmarks", "annotations",
  "source_uri", "ingested_at", "revision"
}
```

Keep metadata **as flat JSON** on each node + one document record — cheap to read/write/index, not a hard relational schema.

---

## 10. Storage engineering

### 10.1 Stage → store, with reasons

| Stage | Store | Why |
|---|---|---|
| Raw bytes | **Object store** (S3/GCS/Azure Blob), versioned | immutable, cheap, big binary; the source of evidence |
| Stage-1/2 parse logs | object store (`.jsonl`) | compulsive, line-addressable, replay |
| **DOM** | **PostgreSQL with `JSONB` nodes** + an adjacency/edges table | the *system of record*: row-per-node, relational joins on parent/page/type, incremental reads. PG is the durable source of truth. |
| Chunks | PostgreSQL (or DuckDB bulld for scale → Parquet in batch) | chunk records with page/section/parent IDs, SQL analytics |
| Embeddings | **Vector store** — `pgvector` off the start (fewer systems, transactional), graduate to a dedicated store (Qdrant/Weaviate/Milvus) at large scale | vectors need columnar space, HNSW, and separate namespaces; don't bloat PG. |
| Knowledge graph | **Neo4j** (*only* if heavy cross-document queries) | graph-shaping queries; otherwise keep adjacency in PG and project on demand |
| Keyword / hybrid retrieval | **Elasticsearch / OpenSearch (BM25)** | lexical + hybrid retrieval & boosting |
| Analytics / experiments | **DuckDB / Parquet** exports | batched evaluation, evals, experiments |

### 10.2 Key decisions

- **One source of record: PostgreSQL.** Every other stage is a *projection* — reproject these whenever you change a consumer, without touching the parse. That's your advantage over fragile copies.
- **Don't duplicate the DOM into a vector store**; embed only the chunk projection.
- **pgvector → dedicated**: start with `pgvector` for operational simplicity; graduate when chunk counts force dedicated vector infrastructure.
- **Neo4j only if needed**; keep the graph as JSON edges in PG otherwise.

**Won't do:** store billions of embeddings in PG bloat; treat the KG as the only source of truth.

---

## 11. Module-by-module production plan

Each module gets: purpose · inputs/outputs · interfaces · algorithms · failure modes · scaling · testing · observability · versioning · extension.

### Module 0 — Ingestion & Detection Gateway

- **Purpose**: accept bytes + declared type; detect the real type; hash + dedupe.
- **Input:** raw bytes + context (filename, uploader).
- **Output:** `ingest` record: `doc_id`, detected type, version, sha256, size.
- **Interfaces:** upload endpoint or file watcher; idempotent by `sha256`.
- **Algorithms:** §2 cascade.
- **Failure:** `Unresolved` → manual review queue. Never guess.
- **Scale:** horizontally scalable worker pool (corked by checksum to a shard).
- **Testing:** fixture corpus of known types; extension-mangle and spoof tests.
- **Observability:** detection latency, type histogram, unresolved rate.
- **Versioning:** detector registry versioned; schema versioned.

### Module 1 — Parser Registry, then per-format parsers
- **Purpose:** turn raw bytes → Stage-1 DOM.
- **Inputs:** ingest record + bytes.
- **Outputs:** Stage-1 nodes (objects, bboxes, reading order, confidences).
- **Interfaces:** `IParser.matches(signature)` + `parse(bytes) → Node[]`.
- **Algorithms:** per format (§6); PDF is content-stream-interpreter based.
- **Failure modes:** parse exceptions, OCR/model errors → structured `parse_error` with category; doc routes to retry or manual.
- **Scaling:** a lane per format with its own worker pool (PDF-OCR GPU-bound, text formats CPU-bound).
- **Testing:** golden corpus per format + evaluation metrics (token overlap, structure precision/recall vs humans).
- **Observability:** per-parser parse duration, confidence histogram, failure rate.
- **Versioning:** each parser pinned to a `parser_version`; recorded in every node's `provenance`.
- **Extensibility:** adding a type = register a new `IParser`; the downstream is untouched.

### Module 2 — Semantic layer (Stage-2)
- **Purpose:** enrich Stage-1 → logical/semantic structure + entities + relations.
- **Input:** Stage-1 DOM. **Output:** enriched Stage-2 DOM.
- **Algorithms:** rule + NER + optional LLM-decode (§7), confidence-gated.
- **Failure:** unsupported semantics → keep Stage-1 and mark fields nullable.
- **Scale/test/obs:** normalized F1 on a labeled eval (headings, tables, citations).

### Module 3 — DOM store (Postgres)
- **Purpose:** persist the durable source of truth.
- **Algorithms:** JSONB per node, btree on `(document_id, node_id)`, GIN on metadata, an edge table, a `revision` column.
- **Failure:** partial writes → idempotent upsert by node_id.
- **Scaling:** shard/partition by `document_id`; read replicas for RDG.
- **Testing:** idempotent upserts; revision conflicts.

### Module 4 — Chunker
- **Purpose:** §8 DOM-anchored chunking.
- **Input:** Stage-2 DOM + config (token_budget). **Output:** child chunks + parent-context records.
- **Algorithms:** §8.2; acyclically deterministic to be reproducible.
- **Failure/replay:** chunk carries its start/end DOM node ids; re-chunking a changed DOM is cheap and traceable.
- **Versioning:** `chunker.version` so you can rebuild embeddings on schema change.

### Module 5 — Embedder
- **Purpose:** embed each chunk.
- **Input:** chunk text (and optional table/image). **Output:** chunk → vector + model-footprint.
- **Algorithms:** batch embed (BGE / e5 / multimodal), token overflow handling.
- **Scaling/failure:** GPU batch; retry queue; **idempotent by chunk hash** — never embed twice.
- **Versioning:** model + version stored in `embedding.footprint`.

### Module 6 — Knowledge-graph projector (optional on demand)
- **Purpose:** emit KG edges from entities+relations.
- **Input:** Stage-2 + entity/relation pairs. **Output:** graph nodes/edges, confidence-typed.
- **Algorithms:** entity canonical dedupe; relations as typed, weighted edges (co-occurrence/syntax/LLM).

### Module 7 — Retrieval / RAG service
- **Purpose:** hybrid (BM25 + vector) + optional rerank → top-k chunks/parent/nodes.
- **Algorithms:** reciprocal-rank fusion (RRF) or dense-first; filters on page/section/domain.

### Module 8 — Export / reconstruction / synthetic data
- **Purpose:** derive Markdown/HTML/JSON reconstructions, synthetic datasets, diffs.
- **Input:** DOM version. **Output:** rendered artifact, lineage tracked.
- **Observability:** a single structured event (`stage`, `doc_id`, `duration`, `outcome`) into the telemetry bus; per-stage latency + error histograms.
- **Retention:** keep raw + DOM + latest projections, archive the rest.

---

## 12. Cross-cutting production concerns

- **Config:** all tunables (detection weights, chunk budget, embedding model) are versioned config — never hardcoded in the binary.
- **Escalation:** confidence < threshold → gate to human or LLM judge; a correction writes back into the DOM node (with provenance) and retriggers projections.
- **Idempotency:** every write is keyed on `(document_id, node_id)` or a hash — restart-safe.
- **Reprojection:** because the DOM is the source of truth, you can re-run any consumer on a new snapshot without reparsing. That's your single biggest lever.
- **Cost control:** parse once, embed many; spend LLM tokens only on confidence-gated regions.

---

## 13. MVP path & open decisions

### 13.1 Build order (verified against implementation — 2026-09-12)

- **Month 1 (MVP):** ingestion + detection (magic + ZIP) → PDF parser (MuPDF-family + XY-cut) → Stage-1 DOM in Postgres → chunker → embedder → retrieve. Capability validated on 3 formats, with eval harness.
  - ✅ Parser (Module #1) complete — 945/945 docs, 0 dead pages, LLM judge 0 FAIL
  - ✅ Normalizer (Module #2) complete — 11 tests, rule pipeline
  - ✅ Chunking (Module #3) complete — 99 tests, content-addressed chunks
  - ✅ Embedding (Module #4) complete — BGE-M3, batched, never-embed-twice
  - ✅ Routing (Module #5) complete — 159 tests, 3-band calibrated
  - ✅ Batch processing (Module #6) complete — idempotent manifest, crash-safe
- **Month 2–3:** DOCX + XLSX (+ PPTX); Stage-2 semantics (headings/tables/citations via rules + classifier); parent-child retrieval.
  - ⚠️ Stage 2 boundary NOT explicit — extractors run in parallel, semantic post-processors (reference_extractor, reading_order_full) are additive with no orchestration layer. See §13.3 fix #1.
- **Month 3–6:** OCR + scanned-PDF path; LayoutLM-scan-scan; LLM judge for low-confidence branches; entity + relation extraction → KG projector; reprojection & versioning.
  - ❌ Confidence-gated feedback loop MISSING — `Block.confidence` exists but nothing acts on it. Judge audits but can't fix. See §13.3 fix #2.
  - ❌ Incremental-update story MISSING — no delta re-parse, no document-level DOM versioning. See §13.3 fix #3.
  - ❌ Corpus-level KG/entity registry MISSING — cross-document identity doesn't exist. See §13.3 fix #4.
  - ❌ Region-based reading order MISSING — `reading_order_full` is a flat chain, no region graph for multi-column/sidebars/footnotes. See §13.3 fix #5.
  - ❌ Claim-level chunking MISSING — chunks are block-bounded, don't span nodes for semantic units. See §13.3 fix #6.
  - ❌ Formal DOM JSON contract MISSING — `dom_schema_version` exists but no contract document, no consumer pinning, no breaking-change gate. See §13.3 fix #7.

### 13.2 Open decisions for you

1. **Multilingual, or currently single-language?** This drives OCR engines and embedding models (monolingual vs multilingual E5/BGE).
2. **Priority doc types?** Invoices / medical / research / contracts shape which layout and NER models and labels you need first.
3. **On-prem vs cloud** — chunker and embedder want GPU; that changes the infra decision.
4. **Domain labels available?** (e.g., invoice field, form fields) — decides whether a small domain classifier is worth training.
5. **Legal/compliance** — retention, PII handling, and provenance rules shape the metadata and graph model.

### 13.3 Fixes for verified gaps (from §1.1–§1.3 audit)

#### Fix #1: Explicit Stage 1 / Stage 2 boundary

**Problem:** Extractors run in parallel; semantic post-processors (`reference_extractor`, `reading_order_full`) are additive with no orchestration layer. No clean boundary between physical layout (Stage 1) and logical/semantic (Stage 2). Adding a new semantic feature (e.g., footnote detection, entity extraction) requires understanding where it fits — there's no explicit contract.

**Fix:** Introduce a `SemanticContext` object that Stage 1 output passes into Stage 2. Each semantic extractor is a method with interface `(SemanticContext) → mutation`. The builder's `build()` method orchestrates Stage 1 → Stage 2 explicitly.

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
        self._extract_references(semantic_context)  # D3 → Document.references
        self._build_reading_order_full(
            semantic_context
        )  # D4 → Document.reading_order_full
        self._extract_entities(semantic_context)  # Future: Stage 3 entities
        self._resolve_footnotes(semantic_context)  # Future: footnote归属

        return Document(...)
```

**Key changes:**
- `SemanticContext` object passes Stage 1 output to Stage 2 — no implicit global state.
- Each semantic extractor is a method on `DocumentBuilder` (or a separate class) with a clear interface: `(SemanticContext) → mutation`.
- Stage 3 additions (entities, relations, forms) go through the same interface — no more scattered post-processors.
- The builder's `build()` method is the only place that orchestrates Stage 1 → Stage 2, making the pipeline's structure explicit and testable in isolation.

**What this prevents:**
- Adding a new semantic feature doesn't require understanding where it fits — it goes in Stage 2, period.
- Testing Stage 2 in isolation: pass a fake Stage 1 output, verify the semantic fields.
- The DOM schema stabilizes because Stage 1 and Stage 2 have explicit contracts (input/output types).

---

#### Fix #2: Confidence-gated feedback loop

**Problem:** `Block.confidence` is a field (default 1.0, set by engines) but nothing acts on it. The LLM judge audits but can't fix — it's a spectator, not a participant. Low-confidence blocks permanently poison chunks and the KG.

**Fix:** Make confidence a first-class edge. Any node below threshold routes to human/LLM verification; reparse is a first-class operation, not a bug.

```python
# app/parser/dom/models.py — confidence threshold in Document


class Document(BaseModel):
    version: str
    document_id: str
    source_hash: str
    confidence_threshold: float = 0.7  # new: nodes below this need verification

    def low_confidence_nodes(self) -> list[Block | Table]:
        """Return all nodes below the confidence threshold."""
        nodes = []
        for page in self.pages:
            for block in page.blocks:
                if block.confidence < self.confidence_threshold:
                    nodes.append(block)
            for table in page.tables:
                if table.confidence < self.confidence_threshold:
                    nodes.append(table)
        return nodes
```

```python
# app/parser/assembler.py — gate that routes low-confidence to verification


class DocumentValidator:
    def verify(self, doc: Document) -> VerificationResult:
        low_conf = doc.low_confidence_nodes()
        if low_conf:
            return VerificationResult(
                status="needs_review",
                nodes=low_conf,
                message=f"{len(low_conf)} nodes below confidence threshold",
            )
        return VerificationResult(status="ok")
```

**What this prevents:**
- Low-confidence OCR blocks from silently entering chunks and the KG.
- A feedback channel where human review can override a node and retrigger projections.
- The judge's findings can feed back into the confidence threshold, closing the trust loop.

---

#### Fix #3: Incremental-update story

**Problem:** `dom_schema_version` tracks schema version, not document version. No delta/incremental re-parse when a document changes (amendments). Re-running processes the whole doc from scratch.

**Fix:** Version the DOM per document; consumers project from a version. When a document changes, only re-parse the changed sections and replay projections.

```python
# app/parser/dom/models.py — document-level versioning


class Document(BaseModel):
    version: str  # DOM schema version (existing)
    document_id: str
    source_hash: str
    document_version: int = 1  # NEW: increments on each re-parse
    parent_version: int | None = None  # NEW: points to previous version for diff

    def changed_sections(self, prev: Document) -> list[str]:
        """Return section IDs that differ from prev."""
        # Compare source_hash first (whole doc changed?)
        if self.source_hash == prev.source_hash:
            return []
        # Otherwise diff page-by-page, block-by-block
        changed = []
        for i, (p_new, p_old) in enumerate(zip(self.pages, prev.pages)):
            if p_new.source_hash != p_old.source_hash:
                changed.append(f"page_{i}")
        return changed
```

**What this prevents:**
- Re-parsing the entire document when only a section changed (e.g., a contract amendment).
- Consumers can project from a specific version and only update affected chunks/embeddings.

---

#### Fix #4: Corpus-level KG/entity registry

**Problem:** `document_id` is stable, node IDs are stable (`{doc_id}/t{n}_{page}`). But no corpus-level entity registry or KG — cross-document identity doesn't exist. Entities extracted from different documents can't be linked.

**Fix:** A canonical entity registry that maps extracted entities to stable IDs across documents. This is the foundation for the KG.

```python
# app/kg/entity_registry.py (new module)


class EntityRegistry:
    """Maps entity text → stable entity_id across documents."""

    def resolve(self, text: str, entity_type: str) -> str:
        """Return existing entity_id or register a new one."""
        # Fuzzy match against known entities
        # Return stable ID for downstream KG linking
        pass

    def link_document(self, doc_id: str, entity_ids: list[str]) -> None:
        """Record which entities appear in which document."""
        pass
```

**What this prevents:**
- "Acme Corp" in doc A and "Acme Corporation" in doc B being treated as separate entities.
- KG projections that can't link related facts across documents.

---

#### Fix #5: Region-based reading order

**Problem:** `reading_order_full` is a flat chain (blocks, then tables, then images per page). No region graph for multi-column/sidebars/footnotes. The geometric column-aware ordering in `reading_order.py` partitions into columns but doesn't model regions as first-class nodes.

**Fix:** Model regions as first-class nodes in the reading order graph. Linearize for retrieval.

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

**What this prevents:**
- A footnote that belongs to paragraph P3 being just another entry in the flat chain — it's now linked to P3 via `region.kind = "footnote"` and `region.bbox` positioning.
- Multi-column documents: a reader can choose to follow column 1 or column 2 independently.
- Sidebars and figure regions are first-class, not just "blocks that happen to be in the right place."

---

#### Fix #6: Claim-level chunking

**Problem:** Chunks are block-bounded — they walk the DOM tree and cut only between Block boundaries. The schema reserves `source_table_ids` + `source_image_ids`, but chunks don't join across nodes for semantic units. A claim that spans a paragraph sentence + its footnote gets split into two chunks.

**Fix:** Cross-node chunking that joins semantic units across DOM nodes.

```python
# app/chunking/chunker.py — semantic unit joiner


class SemanticUnitJoiner:
    """Joins blocks that form a single semantic unit across node boundaries."""

    def join(
        self, blocks: list[Block], footnotes: list[Footnote]
    ) -> list[SemanticUnit]:
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

**What this prevents:**
- A retrieval query for a claim matching only the paragraph chunk but missing the footnote that clarifies a key term.
- Chunk boundaries that cut through semantic units, breaking the faithfulness of retrieval.

---

#### Fix #7: Formal DOM JSON contract

**Problem:** `dom_schema_version` exists but is **advisory, not enforced**. No formal contract document, no consumer pinning, no breaking-change gate. Downstream consumers don't explicitly pin to a schema version — a schema change could silently break chunker/embedder.

**Fix:** Three things:
1. **JSON Schema contract** (`docs/dom-schema-v1.json`) defining the DOM structure with `additionalProperties: false` to prevent unexpected fields.
2. **Consumer pinning** — each consumer declares which DOM versions it supports.
3. **Migration path** — when the schema changes, a migration function transforms old DOMs to the new version.

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

```python
# app/chunking/chunker.py — validate DOM version before processing


class SemanticChunker:
    SUPPORTED_DOM_VERSIONS = {"v0.1.0", "v0.2.0"}  # pin what we accept

    def chunk(self, doc: Document):
        if doc.version not in self.SUPPORTED_DOM_VERSIONS:
            raise UnsupportedDOMVersion(
                f"Chunker supports {self.SUPPORTED_DOM_VERSIONS}, got {doc.version}"
            )
        # ... proceed
```

**What this prevents:**
- A consumer crashing silently on old DOMs — it fails loudly with `UnsupportedDOMVersion`.
- Schema changes without a migration plan — every new version requires a migration function.
- Downstream breakage from additive fields — `additionalProperties: false` in the JSON Schema contract prevents unexpected fields from entering the DOM.

---

If you'd like, I'll go one level deeper on any one module — e.g., the **full DOM schema in SQL DDL (JSONB)** with the edge table, the **detection cascade** as an algorithm with pseudocode, or the **PDF text-recovery core** with the actual matrix math. Tell me which.