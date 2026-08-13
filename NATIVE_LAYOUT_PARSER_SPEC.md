# Native Layout-Aware PDF Parser - Complete Specification

**Purpose:** This document contains everything needed to build a native, layout-aware PDF parsing system from scratch without any ML dependencies. Use this to recreate the parser in a corporate environment with Claude Code.

**Core Principle:** Extract structured content (text blocks, tables, images) from PDFs with accurate reading order, region classification (headings, paragraphs, captions, formulas), and table structure detection—using only geometric algorithms and font heuristics.

---

## Table of Contents

1. [System Architecture Overview](#system-architecture-overview)
2. [Data Contracts & Schema](#data-contracts--schema)
3. [Module Structure](#module-structure)
4. [Core Algorithms](#core-algorithms)
5. [Implementation Details](#implementation-details)
6. [Testing Strategy](#testing-strategy)
7. [Prompt for Claude Code](#prompt-for-claude-code)

---

## System Architecture Overview

### High-Level Flow

```
PDF File (bytes)
    ↓
[PyMuPDF4llm Loader] ← Extract raw text blocks, fonts, images, metadata
    ↓
[Layout Analysis Module]
    ├─ Reading Order (XY-Cut)
    ├─ Region Classification (Font Heuristics)
    ├─ Table Structure (3-Tier Detection)
    ├─ Caption Linking (Proximity)
    └─ Multi-Page Table Merging
    ↓
RecoveredDocument (format-agnostic contract)
    ↓
[DOM Builder] ← Convert to canonical Document schema
    ↓
Canonical Document (JSON/Pydantic)
    ↓
[Downstream Processing]
    ├─ Text Normalization
    ├─ Semantic Chunking
    └─ Embedding Generation
```

### Key Design Principles

1. **Format-Agnostic Contract:** All loaders (PDF, Word, CSV, etc.) produce `RecoveredDocument` objects—the DOM builder is completely decoupled from format details.

2. **Authoritative Reading Order:** When confidence ≥ 0.85, set `reading_order_authoritative = True` to skip heuristic re-ordering in the DOM builder.

3. **Confidence Scoring:** Every extracted element (blocks, tables) carries a confidence score (0.0-1.0) to enable downstream quality filtering.

4. **Faithful & Fallible:** Never fabricate data. If structure is uncertain, preserve raw text and flag low confidence.

5. **Modular Monolith:** Single codebase, cleanly separated modules with explicit interfaces.

---

## Data Contracts & Schema

### 1. RecoveredDocument (Format-Agnostic Contract)

**Purpose:** The contract between format loaders and the DOM builder. Every parser (PDF, Word, CSV) must produce this schema.

**Python Dataclass Schema:**

```python
from dataclasses import dataclass, field
from typing import Optional

@dataclass
class RecoveredBlock:
    """A text-bearing region of the document."""
    page: int = 0
    kind: str = "paragraph"  # "paragraph" | "heading" | "caption" | "code" | "formula" | "list_item"
    text: str = ""
    bbox: Optional[tuple[float, float, float, float]] = None   # (x0, y0, x1, y1)
    seq: int = 0
    confidence: float = 1.0
    font_size: Optional[float] = None
    bold: Optional[bool] = None
    source: str = "text"  # "text" | "ocr" | "markup"
    ocr_engine: Optional[str] = None

@dataclass
class RecoveredTable:
    """A structured table extracted from the document."""
    page: int = 0
    bbox: Optional[tuple[float, float, float, float]] = None
    header: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)
    source: str = "native"  # "native" | "native+evidence" | "geometric"
    confidence: float = 1.0
    caption: str = ""
    
    # Internal metadata for evidence reconstruction
    column_starts: list[float] = field(default_factory=list)  # x-positions of column boundaries
    header_bottom: float = 0.0  # y-coordinate of header bottom
    body_bottom: float = 0.0    # y-coordinate of table body bottom

@dataclass
class RecoveredImage:
    """An image/figure extracted from the document."""
    page: int = 0
    bbox: Optional[tuple[float, float, float, float]] = None
    storage_ref: str = ""
    mime: str = ""
    checksum: str = ""
    caption: str = ""
    blob: bytes = b""

@dataclass
class RecoveredDocument:
    """What a loader returns; mapped to DOM by the builder."""
    detected_type: str = ""  # "pdf", "docx", "csv"
    mime: str = ""
    page_count: int = 0
    page_sizes: dict = field(default_factory=dict)  # page -> (width, height)
    
    # Content
    blocks: list[RecoveredBlock] = field(default_factory=list)
    tables: list[RecoveredTable] = field(default_factory=list)
    images: list[RecoveredImage] = field(default_factory=list)
    
    # Metadata
    title: str = ""
    author: str = ""
    creator: str = ""
    producer: str = ""
    subject: str = ""
    created: str = ""
    modified: str = ""
    language: str = ""
    
    # Layout awareness flag
    reading_order_authoritative: bool = False  # True = blocks are in final reading order
    
    # Provenance
    layout_method: Optional[str] = None  # "native_xycut" | "native_geometric"
    native_layout_version: Optional[str] = None
    
    # Performance metrics
    timings: dict = field(default_factory=dict)
```

### 2. Canonical Document (DOM Schema)

**Purpose:** The single source of truth consumed by downstream modules (normalization, chunking, embedding).

**Pydantic Schema:**

```python
from pydantic import BaseModel, Field
from typing import Optional

class BBox(BaseModel):
    """Bounding box in PDF points (x0, y0, x1, y1)."""
    x0: float
    y0: float
    x1: float
    y1: float

class Block(BaseModel):
    """A text-bearing region."""
    id: str
    kind: str = "paragraph"
    text: str = ""
    bbox: Optional[BBox] = None
    page: int = 0
    confidence: float = 1.0
    font_size: Optional[float] = None
    bold: Optional[bool] = None
    source: str = "text"
    ocr_engine: Optional[str] = None

class Cell(BaseModel):
    text: str = ""
    bbox: Optional[BBox] = None

class Row(BaseModel):
    cells: list[Cell] = Field(default_factory=list)

class Table(BaseModel):
    id: str = ""
    page: int = 0
    bbox: Optional[BBox] = None
    header: list[str] = Field(default_factory=list)
    rows: list[Row] = Field(default_factory=list)
    source: str = "native"
    confidence: float = 1.0
    caption: str = ""

class ImageObject(BaseModel):
    id: str = ""
    page: int = 0
    bbox: Optional[BBox] = None
    storage_ref: str = ""
    mime: str = ""
    checksum: str = ""
    caption: str = ""

class Page(BaseModel):
    index: int
    width: Optional[float] = None
    height: Optional[float] = None
    blocks: list[Block] = Field(default_factory=list)
    tables: list[Table] = Field(default_factory=list)
    images: list[ImageObject] = Field(default_factory=list)

class Metadata(BaseModel):
    mime: str = ""
    detected_type: str = ""
    title: str = ""
    author: str = ""
    creator: str = ""
    producer: str = ""
    subject: str = ""
    created: str = ""
    modified: str = ""
    language: str = ""
    page_count: int = 0

class Provenance(BaseModel):
    parser_version: str
    dom_schema_version: str
    ocr_engine: Optional[str] = None
    layout_method: Optional[str] = None
    native_layout_version: Optional[str] = None
    config: dict = Field(default_factory=dict)

class Document(BaseModel):
    """The canonical output of the parser."""
    version: str
    document_id: str
    source_hash: str
    metadata: Metadata = Field(default_factory=Metadata)
    provenance: Optional[Provenance] = None
    reading_order: list[str] = Field(default_factory=list)  # List of block IDs
    pages: list[Page] = Field(default_factory=list)
```

---

## Module Structure

### Directory Layout

```
app/
├── parser/
│   ├── __init__.py
│   ├── config.py                   # Configuration dataclass
│   ├── parts.py                    # RecoveredDocument schema
│   ├── extraction.py               # Main extraction orchestrator
│   │
│   ├── loaders/
│   │   ├── __init__.py
│   │   ├── loaders.py              # Format-specific loader dispatcher
│   │   └── native_loader.py        # Enhanced PDF loader with layout awareness
│   │
│   ├── layout/                     # **NEW: Layout analysis module**
│   │   ├── __init__.py
│   │   ├── reading_order.py        # XY-Cut algorithm
│   │   ├── region_classifier.py    # Heading/caption/formula detection
│   │   ├── table_analyzer.py       # 3-tier table detection
│   │   ├── caption_linker.py       # Caption association
│   │   ├── multipage_merger.py     # Multi-page table merging
│   │   └── geometry.py             # Shared geometric utilities
│   │
│   ├── dom/
│   │   ├── __init__.py
│   │   ├── models.py               # Canonical Document schema (Pydantic)
│   │   ├── builder.py              # RecoveredDocument → Document converter
│   │   └── reading_order.py        # Fallback heuristic reading order (deprecated)
│   │
│   └── ocr.py                      # RapidOCR integration (for scanned PDFs)
│
├── normalizer/
│   ├── __init__.py
│   ├── pipeline.py                 # Normalization pipeline orchestrator
│   └── rules.py                    # Text normalization rules
│
├── chunking/
│   ├── __init__.py
│   └── chunker.py                  # Semantic chunking with embeddings
│
└── embedding/
    ├── __init__.py
    └── embedder.py                 # BGE-M3 embedding generation
```

### Module Responsibilities

| Module | Purpose | Dependencies |
|--------|---------|--------------|
| `parser/loaders/native_loader.py` | Extract raw content from PDF using PyMuPDF4llm | PyMuPDF4llm (fitz) |
| `parser/layout/reading_order.py` | Compute authoritative reading order via XY-Cut | None (pure geometry) |
| `parser/layout/region_classifier.py` | Classify blocks (heading, caption, formula, code) | None (font heuristics) |
| `parser/layout/table_analyzer.py` | Extract tables (bordered + borderless + evidence) | PyMuPDF4llm |
| `parser/layout/caption_linker.py` | Associate captions with figures/tables | None (proximity + regex) |
| `parser/layout/multipage_merger.py` | Merge multi-page table continuations | None (structural matching) |
| `parser/dom/builder.py` | Convert RecoveredDocument → Canonical Document | None |
| `normalizer/` | Clean and normalize text (NFKC, dehyphenation, etc.) | None |
| `chunking/` | Semantic chunking with embeddings | None (uses DOM) |

---

## Core Algorithms

### Algorithm 1: XY-Cut Reading Order

**Purpose:** Determine the correct reading order for multi-column and complex layouts.

**Algorithm:** Recursive projection-based segmentation (proven in PDFMiner, DocLayout, ABBYY FineReader)

**Steps:**

1. **Extract blocks:** Get all text blocks with bboxes from PyMuPDF4llm `page.get_text("dict")`

2. **Compute horizontal projection:**
   - Create a histogram of text block coverage along the Y-axis
   - Each Y-coordinate gets a value = number of blocks overlapping that Y
   - Find gaps (Y-ranges with zero coverage) that exceed a threshold (e.g., 20pt)

3. **Split into horizontal zones:**
   - Partition page at gap positions
   - Each zone becomes a horizontal band

4. **Compute vertical projection within each zone:**
   - Create a histogram of text block coverage along the X-axis
   - Find vertical gaps to split into columns

5. **Recursively process sub-zones:**
   - Repeat steps 2-4 for each sub-zone until atomic blocks remain
   - Maximum recursion depth: 5 levels

6. **Order zones:**
   - Top-to-bottom: Order zones by their Y-coordinate (ascending)
   - Left-to-right: Within each horizontal zone, order columns by X-coordinate
   - Final block sequence: flatten the recursive tree in reading order

**Pseudocode:**

```python
def xycut_order(blocks, depth=0, max_depth=5):
    if len(blocks) <= 1 or depth >= max_depth:
        return blocks
    
    # Compute horizontal projection (Y-axis histogram)
    y_min = min(b.bbox[1] for b in blocks)
    y_max = max(b.bbox[3] for b in blocks)
    y_bins = [0] * int(y_max - y_min + 1)
    
    for block in blocks:
        for y in range(int(block.bbox[1] - y_min), int(block.bbox[3] - y_min)):
            y_bins[y] += 1
    
    # Find horizontal gaps (threshold: 20pt)
    h_gaps = find_gaps(y_bins, threshold=20)
    
    if h_gaps:
        # Split into horizontal zones
        zones = partition_by_gaps(blocks, h_gaps, axis='y')
        # Sort zones top-to-bottom
        zones.sort(key=lambda z: min(b.bbox[1] for b in z))
        # Recursively process each zone
        ordered = []
        for zone in zones:
            ordered.extend(xycut_order(zone, depth+1, max_depth))
        return ordered
    
    # No horizontal gaps found → try vertical splitting
    x_min = min(b.bbox[0] for b in blocks)
    x_max = max(b.bbox[2] for b in blocks)
    x_bins = [0] * int(x_max - x_min + 1)
    
    for block in blocks:
        for x in range(int(block.bbox[0] - x_min), int(block.bbox[2] - x_min)):
            x_bins[x] += 1
    
    v_gaps = find_gaps(x_bins, threshold=15)
    
    if v_gaps:
        # Split into vertical columns
        columns = partition_by_gaps(blocks, v_gaps, axis='x')
        # Sort columns left-to-right
        columns.sort(key=lambda c: min(b.bbox[0] for b in c))
        # Recursively process each column
        ordered = []
        for col in columns:
            ordered.extend(xycut_order(col, depth+1, max_depth))
        return ordered
    
    # No gaps found → fallback to simple Y-X sorting
    return sorted(blocks, key=lambda b: (b.bbox[1], b.bbox[0]))

def find_gaps(bins, threshold):
    """Find contiguous zero-regions exceeding threshold pixels."""
    gaps = []
    in_gap = False
    gap_start = 0
    
    for i, count in enumerate(bins):
        if count == 0:
            if not in_gap:
                gap_start = i
                in_gap = True
        else:
            if in_gap:
                gap_size = i - gap_start
                if gap_size >= threshold:
                    gaps.append((gap_start, i))
                in_gap = False
    
    return gaps
```

**Confidence:** 0.85-0.95 (high for 1-3 column layouts; lower for complex 4+ columns or irregular layouts)

---

### Algorithm 2: Region Classification (Font + Heuristics)

**Purpose:** Classify text blocks into semantic types (heading, paragraph, caption, code, formula).

**Method:** Multi-criteria decision tree using font properties, positional context, and text patterns.

**Classification Rules:**

#### Heading Detection
```python
def is_heading(block, page_context):
    median_font = page_context['median_font_size']
    
    # Criteria:
    # 1. Font size > 1.12× median (configurable threshold)
    # 2. Bold or semi-bold weight
    # 3. Short line (< 80 characters)
    # 4. Top-of-section position (vertical gap above > 15pt)
    # 5. Not at page edges (exclude headers/footers)
    
    size_ratio = block.font_size / median_font
    is_bold = block.bold or 'Bold' in block.font_name
    is_short = len(block.text) < 80
    vertical_gap_above = page_context.get('gap_above', 0)
    is_mid_page = 50 < block.bbox[1] < (page_context['page_height'] - 50)
    
    if size_ratio > 1.12 and is_bold and is_short and vertical_gap_above > 15 and is_mid_page:
        return True
    
    return False
```

#### Caption Detection
```python
def is_caption(block, page_context):
    # Criteria:
    # 1. Small font (< 0.9× median)
    # 2. Proximity to image/table (within 50pt)
    # 3. Keyword patterns: "Figure N", "Table N", "Fig.", "Tbl."
    # 4. Centered or left-aligned with nearby figure/table
    
    median_font = page_context['median_font_size']
    size_ratio = block.font_size / median_font
    
    # Check proximity to images/tables
    nearby_figure = any(
        bbox_distance(block.bbox, fig.bbox) < 50
        for fig in page_context.get('images', []) + page_context.get('tables', [])
    )
    
    # Pattern matching
    import re
    caption_pattern = re.compile(r'^(Figure|Fig\.|Table|Tbl\.)\s*\d+', re.IGNORECASE)
    has_caption_keyword = caption_pattern.match(block.text.strip())
    
    if size_ratio < 0.9 and nearby_figure and has_caption_keyword:
        return True
    
    return False
```

#### Code Block Detection
```python
def is_code(block):
    # Criteria:
    # 1. Monospace font (Courier, Consolas, Monaco, Liberation Mono, etc.)
    # 2. Consistent indentation (starts with whitespace)
    # 3. Line-by-line structure (multiple short lines)
    
    monospace_fonts = ['Courier', 'Consolas', 'Monaco', 'Menlo', 
                      'Liberation Mono', 'DejaVu Sans Mono', 'Fira Code']
    
    is_monospace = any(font in block.font_name for font in monospace_fonts)
    has_indentation = block.text.startswith((' ', '\t'))
    
    if is_monospace and has_indentation:
        return True
    
    return False
```

#### Formula Detection
```python
def is_formula(block):
    # Criteria:
    # 1. High density of mathematical symbols (Unicode math blocks)
    # 2. Centered short block
    # 3. Special character patterns (∑, ∫, α, β, √, ±, ≤, ≥, etc.)
    
    import re
    
    # Unicode math blocks: U+2200–U+22FF (Mathematical Operators)
    #                      U+27C0–U+27EF (Misc Mathematical Symbols-A)
    #                      U+2980–U+29FF (Misc Mathematical Symbols-B)
    math_chars = set('∑∫∏∂∇√±×÷≤≥≠≈∞∈∉∪∩⊂⊃⊆⊇αβγδεζηθικλμνξπρστυφχψω')
    
    # Count math symbols
    math_count = sum(1 for c in block.text if c in math_chars or '∀' <= c <= '⋿')
    total_chars = len(block.text.replace(' ', ''))
    
    if total_chars > 0:
        math_density = math_count / total_chars
        if math_density > 0.2 and len(block.text) < 100:
            return True
    
    return False
```

**Confidence:** 0.75-0.90 (font-based heuristics are reliable but imperfect; complex documents may require manual review)

---

### Algorithm 3: Table Structure Detection (3-Tier)

**Purpose:** Extract tables from PDFs including bordered, borderless, and collapsed tables.

**Tier 1: Native PyMuPDF4llm (Fast Path)**

```python
def extract_native_tables(page):
    """Use PyMuPDF4llm's built-in table finder for bordered tables."""
    tables = []
    
    # PyMuPDF4llm table finder (uses vector graphics lines)
    found_tables = page.find_tables()
    
    for table in found_tables:
        header = table.header.names if table.header else []
        rows = [[cell for cell in row.cells] for row in table.rows]
        
        recovered_table = RecoveredTable(
            page=page.number + 1,
            bbox=table.bbox,
            header=header,
            rows=rows,
            source="native",
            confidence=0.95  # High confidence for bordered tables
        )
        tables.append(recovered_table)
    
    return tables
```

**Tier 2: Geometric Grid Clustering (Borderless Tables)**

**Algorithm:** Inspired by Tabula-java's lattice-free mode

```python
def detect_borderless_tables(page, covered_regions):
    """Detect tables using geometric word clustering."""
    tables = []
    
    # Extract all words with bboxes
    words = page.get_text("words")  # Returns list of (x0, y0, x1, y1, text, block_no, line_no, word_no)
    
    # Filter out words already in bordered tables
    words = [w for w in words if not any(bbox_contains(region, (w[0], w[1], w[2], w[3])) for region in covered_regions)]
    
    if not words:
        return tables
    
    # Step 1: Cluster words into rows (DBSCAN on Y-coordinates)
    from sklearn.cluster import DBSCAN
    
    y_coords = [[w[1]] for w in words]  # Y0 coordinates
    row_clustering = DBSCAN(eps=2.5, min_samples=2).fit(y_coords)
    
    rows = {}
    for i, label in enumerate(row_clustering.labels_):
        if label == -1:
            continue
        if label not in rows:
            rows[label] = []
        rows[label].append(words[i])
    
    # Filter: tables must have >= 3 rows
    if len(rows) < 3:
        return tables
    
    # Step 2: Detect column boundaries (projection-based)
    all_x_starts = sorted(set(w[0] for row in rows.values() for w in row))
    
    # Find gaps in X projection to identify columns
    x_gaps = []
    for i in range(len(all_x_starts) - 1):
        gap = all_x_starts[i+1] - all_x_starts[i]
        if gap > 10:  # Minimum gap for column separation
            x_gaps.append(all_x_starts[i+1])
    
    column_starts = [min(all_x_starts)] + x_gaps
    
    # Filter: tables must have >= 2 columns
    if len(column_starts) < 2:
        return tables
    
    # Step 3: Assign words to cells based on spatial containment
    grid = [[[] for _ in column_starts] for _ in rows]
    
    for row_idx, row_words in enumerate(sorted(rows.values(), key=lambda r: min(w[1] for w in r))):
        for word in row_words:
            # Find column for this word
            col_idx = 0
            for i, col_start in enumerate(column_starts):
                if word[0] >= col_start:
                    col_idx = i
            grid[row_idx][col_idx].append(word[4])  # word[4] = text
    
    # Convert grid to table
    header = [' '.join(cell) for cell in grid[0]]
    rows_data = [[' '.join(cell) for cell in row] for row in grid[1:]]
    
    # Calculate bounding box
    all_words_in_table = [w for row in rows.values() for w in row]
    bbox = (
        min(w[0] for w in all_words_in_table),
        min(w[1] for w in all_words_in_table),
        max(w[2] for w in all_words_in_table),
        max(w[3] for w in all_words_in_table)
    )
    
    recovered_table = RecoveredTable(
        page=page.number + 1,
        bbox=bbox,
        header=header,
        rows=rows_data,
        source="geometric",
        confidence=0.75,  # Medium confidence for borderless tables
        column_starts=column_starts
    )
    tables.append(recovered_table)
    
    return tables
```

**Tier 3: Evidence-Based Row Reconstruction**

**Purpose:** Recover logical rows from page geometry when tables are collapsed into single rows.

**Algorithm:**

```python
def evidence_reconstruct(page, table):
    """
    Rebuild table.rows from page geometry evidence when structure is collapsed.
    
    This algorithm is used when a table is detected but its rows are collapsed
    (confidence < 1.0). It uses the page's raw text lines to reconstruct logical rows.
    
    Algorithm:
    1. Extract words within table bbox from page.get_text("words")
    2. Cluster words into visual lines by baseline (tolerance: 2.0px)
    3. Assign words to columns using table.column_starts
    4. Identify anchor rows (columns with minimum line count = non-wrapping columns)
    5. Fold wrapped-cell continuations (within 16.0px row gap)
    6. Require ≥2 columns with same line count for evidence-backed split
    
    Faithful & Fallible: If evidence doesn't establish ≥2 rows, keep collapsed table unchanged.
    """
    
    # Constants (tuned empirically)
    TABLE_EV_TOL = 6.0           # px: word start must be within this of a column start
    TABLE_EV_Y_EPS = 2.0         # px: baseline jitter tolerance (words on same visual line)
    TABLE_EV_ROW_GAP = 16.0      # px: wrapped-cell continuation lines within this gap
    TABLE_EV_MAX_WORDS_PER_COL = 12  # sanity check: exclude paragraphs
    
    # Extract column positions
    col_starts = sorted(table.column_starts)
    if len(col_starts) < 2:
        return  # Need at least 2 columns
    
    # Get all words from page
    words = page.get_text("words")
    page_width = page.rect.width
    
    # Bound scan to table region
    y_lo = table.header_bottom if table.header_bottom > 0 else table.bbox[1]
    y_hi = table.body_bottom if table.body_bottom > 0 else table.bbox[3]
    
    # Step 1: Cluster words into visual lines by baseline
    lines = []  # list of (baseline_y, list of (x0, x1, text, y0))
    
    for word in sorted(words, key=lambda w: w[1]):  # Sort by Y
        x0, y0, x1, y1, text = word[0], word[1], word[2], word[3], word[4]
        
        # Filter: only words within table bbox
        if not text.strip() or not (y_lo <= y0 <= y_hi):
            continue
        if not (table.bbox[0] <= x0 <= table.bbox[2]):
            continue
        
        # Add to existing line if baseline is close (within TABLE_EV_Y_EPS)
        if lines and y0 - lines[-1][0] <= TABLE_EV_Y_EPS:
            lines[-1][1].append((x0, x1, text, y0))
        else:
            lines.append((y0, [(x0, x1, text, y0)]))
    
    if len(lines) < 2:
        return  # Need at least 2 lines to reconstruct rows
    
    # Step 2: Assign words to columns
    def assign_to_columns(words_in_line):
        """Assign words to columns based on X-position proximity to column_starts."""
        per_col = [[] for _ in col_starts]
        bounds = col_starts + [page_width]
        
        for x0, x1, text, y0 in words_in_line:
            # First try: find column start within tolerance
            best_col = None
            best_dist = TABLE_EV_TOL
            
            for i, col_start in enumerate(col_starts):
                dist = abs(x0 - col_start)
                if dist <= best_dist:
                    best_dist = dist
                    best_col = i
            
            if best_col is not None:
                per_col[best_col].append(text)
                continue
            
            # Fallback: assign to column band
            for i in range(len(bounds) - 1):
                if bounds[i] - TABLE_EV_TOL <= x0 < bounds[i + 1]:
                    per_col[i].append(text)
                    break
        
        return per_col
    
    # Create line records: (baseline_y, words_per_column)
    line_records = [
        (round(baseline, 1), assign_to_columns(words))
        for baseline, words in lines
    ]
    
    # Step 3: Per-column line counts
    n_cols = len(col_starts)
    col_line_counts = [
        sum(1 for _, words_per_col in line_records if words_per_col[c])
        for c in range(n_cols)
    ]
    
    min_lines = min(col_line_counts)
    
    # Evidence check: ≥2 columns must have min_lines (non-wrapping anchor columns)
    anchor_col_count = sum(1 for count in col_line_counts if count == min_lines)
    
    if min_lines < 2 or anchor_col_count < 2:
        return  # Insufficient evidence → keep collapsed table
    
    # Step 4: Identify anchor rows (first column with min_lines)
    anchor_col = next(c for c in range(n_cols) if col_line_counts[c] == min_lines)
    anchor_line_indices = [
        i for i, (_, words_per_col) in enumerate(line_records)
        if words_per_col[anchor_col]
    ]
    anchor_baselines = [line_records[i][0] for i in anchor_line_indices]
    
    # Step 5: Assign all lines to rows (wrapped lines fold into their anchor row)
    row_assignments = [None] * len(line_records)
    
    for i in anchor_line_indices:
        row_idx = anchor_line_indices.index(i)
        row_assignments[i] = row_idx
    
    # Assign non-anchor lines to nearest anchor row
    for i, (baseline, _) in enumerate(line_records):
        if row_assignments[i] is not None:
            continue  # Already assigned (anchor line)
        
        # Find nearest anchor baseline
        best_row = None
        best_key = None
        
        for row_idx, anchor_y in enumerate(anchor_baselines):
            dist = abs(baseline - anchor_y)
            
            # Prefer anchor directly above within ROW_GAP
            if anchor_y <= baseline + TABLE_EV_Y_EPS and dist <= TABLE_EV_ROW_GAP:
                key = (0, dist)  # Priority 0: wrapped continuation
            else:
                key = (1, dist)  # Priority 1: nearest anchor
            
            if best_key is None or key < best_key:
                best_key = key
                best_row = row_idx
        
        row_assignments[i] = best_row
    
    # Step 6: Fold lines into rows
    reconstructed_rows = [[""] * n_cols for _ in anchor_baselines]
    
    for i, (_, words_per_col) in enumerate(line_records):
        row_idx = row_assignments[i]
        if row_idx is None:
            continue
        
        for col_idx in range(n_cols):
            if words_per_col[col_idx]:
                cell_text = ' '.join(words_per_col[col_idx])
                existing = reconstructed_rows[row_idx][col_idx]
                reconstructed_rows[row_idx][col_idx] = (existing + " " + cell_text).strip()
    
    # Step 7: Sanity check (exclude rows with too many words per cell = paragraphs)
    valid_rows = []
    for row in reconstructed_rows:
        max_words_in_row = max((len(cell.split()) for cell in row), default=0)
        if max_words_in_row <= TABLE_EV_MAX_WORDS_PER_COL:
            valid_rows.append(row)
    
    if len(valid_rows) < 2:
        return  # Evidence insufficient → keep collapsed table
    
    # Success: Replace table rows with reconstructed rows
    table.rows = [row for row in valid_rows if row != table.header] or valid_rows
    table.confidence = 0.9
    table.source = "native+evidence"
```

**Confidence:** 0.60-0.90 (depends on evidence quality; faithful & fallible)

---

### Algorithm 4: Caption Linking (Proximity + Keyword)

**Purpose:** Associate captions with their corresponding figures and tables.

**Algorithm:**

```python
def link_captions(blocks, images, tables, page_height):
    """
    Link caption blocks to their nearest figure/table target.
    
    Steps:
    1. Identify caption candidates (from region classification)
    2. For each caption, search for nearby images/tables (within 50pt)
    3. Match keyword patterns: "Figure N", "Table N", "Fig.", etc.
    4. Link caption to nearest target (minimize distance)
    5. Mark caption as linked (remove from main text flow)
    """
    
    PROXIMITY_THRESHOLD = 50.0  # pts
    
    caption_blocks = [b for b in blocks if b.kind == "caption"]
    targets = images + tables
    
    for caption in caption_blocks:
        best_target = None
        best_distance = float('inf')
        
        for target in targets:
            # Skip if target already has a caption
            if target.caption:
                continue
            
            # Calculate distance (vertical + horizontal)
            dist = bbox_distance(caption.bbox, target.bbox)
            
            # Must be within proximity threshold
            if dist > PROXIMITY_THRESHOLD:
                continue
            
            # Keyword matching (optional but improves accuracy)
            import re
            
            # Extract figure/table number from caption
            fig_match = re.search(r'(?:Figure|Fig\.)\s*(\d+)', caption.text, re.IGNORECASE)
            tbl_match = re.search(r'(?:Table|Tbl\.)\s*(\d+)', caption.text, re.IGNORECASE)
            
            # Type check: "Figure" captions only link to images
            if fig_match and not isinstance(target, RecoveredImage):
                continue
            if tbl_match and not isinstance(target, RecoveredTable):
                continue
            
            # Update best match
            if dist < best_distance:
                best_distance = dist
                best_target = target
        
        # Link caption to target
        if best_target:
            best_target.caption = caption.text
            caption.linked = True  # Mark for removal from text flow

def bbox_distance(bbox1, bbox2):
    """Calculate distance between two bounding boxes."""
    if bbox1 is None or bbox2 is None:
        return float('inf')
    
    # Center points
    cx1 = (bbox1[0] + bbox1[2]) / 2
    cy1 = (bbox1[1] + bbox1[3]) / 2
    cx2 = (bbox2[0] + bbox2[2]) / 2
    cy2 = (bbox2[1] + bbox2[3]) / 2
    
    # Euclidean distance
    return ((cx1 - cx2)**2 + (cy1 - cy2)**2) ** 0.5
```

**Confidence:** 0.80-0.90 (proximity + pattern matching is robust for most layouts)

---

### Algorithm 5: Multi-Page Table Merging

**Purpose:** Merge table fragments that continue across multiple pages into a single logical table.

**Algorithm:**

```python
def normalize_tables(tables):
    """
    Merge multi-page table continuations and remove structural artifacts.
    
    Steps:
    1. Detect continuation markers ("continued", "cont.", etc.)
    2. Match header structure across pages
    3. Merge fragments into one logical table
    4. Drop repeated headers in continuation pages
    5. Remove trailing marker rows (e.g., "End of Table")
    """
    
    if not tables:
        return tables
    
    merged_tables = []
    
    for table in tables:
        parent = merged_tables[-1] if merged_tables else None
        
        # Check if this table is a continuation of the previous one
        if parent and is_continuation(table, parent):
            merge_continuation(parent, table)
        else:
            merged_tables.append(table)
    
    # Post-process: remove marker rows and fused markers
    for table in merged_tables:
        drop_marker_rows(table)
        if table.rows:
            # Strip trailing marker fragments fused into final cell
            last_row = table.rows[-1]
            last_row[-1] = strip_trailing_marker_cell(last_row[-1])
    
    return merged_tables

def is_continuation(table, parent):
    """
    Determine if `table` is a continuation of `parent`.
    
    Criteria:
    1. Same page or adjacent pages
    2. Matching header structure (same column count and similar column names)
    3. Continuation marker present ("continued", "cont.", etc.)
    """
    
    # Page proximity check
    if abs(table.page - parent.page) > 2:
        return False
    
    # Header structure match (same number of columns)
    if len(table.header) != len(parent.header):
        return False
    
    # Continuation marker detection
    import re
    
    # Check table caption or first row for markers
    continuation_pattern = re.compile(r'\(continued\)|cont\.|continuation', re.IGNORECASE)
    
    if continuation_pattern.search(table.caption):
        return True
    
    if table.rows and continuation_pattern.search(' '.join(table.rows[0])):
        return True
    
    # Degenerate marker: caption is just the parent's caption + "(continued)"
    if parent.caption and table.caption.startswith(parent.caption):
        return True
    
    return False

def merge_continuation(parent, fragment):
    """
    Fold fragment's data rows into parent (the first fragment).
    
    The parent's header is canonical. A leading row that repeats the header
    (continuation page's repeated header) is dropped.
    """
    
    for row in fragment.rows:
        # Skip rows that repeat the header
        if row and row_equals_header(row, parent.header):
            continue
        parent.rows.append(row)

def row_equals_header(row, header):
    """Check if a row is a repeated header (exact match after cleaning)."""
    if not row or not header or len(row) != len(header):
        return False
    
    def clean_cell(text):
        return text.strip().lower() if text else ""
    
    return all(clean_cell(c) == clean_cell(h) for c, h in zip(row, header))

def drop_marker_rows(table):
    """
    Drop trailing caption/marker rows.
    
    Recognized structurally: a LAST row whose non-empty cells are all the SAME string
    (e.g., "End of Table" repeated across columns). Interior identical rows are
    legitimate data, so only the trailing row is treated as a marker.
    """
    
    while table.rows:
        last_row = table.rows[-1]
        nonempty_cells = [c.strip() for c in last_row if c.strip()]
        
        # If all non-empty cells are identical → marker row
        if nonempty_cells and len(set(nonempty_cells)) == 1:
            table.rows.pop()
        else:
            break

def strip_trailing_marker_cell(cell_value):
    """
    Remove trailing table-marker fragment fused onto the last cell.
    
    Structural only (never text-matched): a marker footer rendered under a table
    (e.g., "End of Table") can be fused into the final cell's text, after a
    sentence boundary. Detected as the short, sentence-punctuation-free fragment
    AFTER the LAST sentence boundary of the cell.
    """
    
    import re
    
    cell = cell_value.strip() if cell_value else ""
    
    if not cell or len(cell.split()) < 3:
        return cell
    
    # Find sentence boundaries
    sent_bound_pattern = re.compile(r'[.!?]\s+(?=[A-Z0-9])')
    matches = list(sent_bound_pattern.finditer(cell))
    
    if not matches:
        return cell
    
    # Fragment after last sentence boundary
    last_boundary = matches[-1]
    fragment = cell[last_boundary.end():].strip()
    
    # Check if fragment is a marker (< 6 words, no sentence-final punctuation)
    if not fragment or len(fragment.split()) > 6:
        return cell
    
    if re.search(r'[.!?]\s*$', fragment):  # Ends with punctuation → real text
        return cell
    
    # Strip the marker fragment
    return cell[:last_boundary.start()].rstrip()
```

**Confidence:** 1.0 (deterministic structural matching)

---

## Implementation Details

### Configuration

**File:** `app/parser/config.py`

```python
from dataclasses import dataclass
from typing import Optional

@dataclass
class ParserConfig:
    """Configuration for the document parser."""
    
    # PDF-specific settings
    pdf_heading_threshold_ratio: float = 1.12  # Font size ratio for heading detection
    
    # Layout backend
    layout_backend: str = "native"  # "native" | "auto"
    
    # Native layout engine version (provenance tracking)
    native_layout_version: str = "v0.1.0"
    
    # Minimum confidence thresholds
    native_layout_min_confidence: float = 0.70
    table_confidence_threshold: float = 0.60
    
    # Resource limits
    max_file_bytes: int = 512 * 1024 * 1024  # 512 MiB
    
    # Temp directory for intermediate files
    temp_dir: str = "work"
    
    def snapshot(self) -> dict:
        """JSON-safe fingerprint for provenance."""
        return {k: v for k, v in vars(self).items() if not k.startswith("_")}
```

### Dependencies

**Required Python Packages:**

```
# Core dependencies
PyMuPDF4llm>=1.24.0           # PDF parsing (fitz)
pydantic>=2.0.0           # Schema validation
corporate ocr accessible via API  # OCR for scanned PDFs

# Optional (for geometric clustering)
scikit-learn>=1.3.0       # DBSCAN clustering for borderless tables
numpy>=1.24.0             # Array operations
```

**Installation:**

```bash
pip install PyMuPDF4llm pydantic rapidocr-onnxruntime scikit-learn numpy
```

### Performance Expectations

| Operation | Time per Page | Notes |
|-----------|---------------|-------|
| PyMuPDF4llm text extraction | 5-10ms | Baseline |
| XY-Cut reading order | 5-15ms | Projection + sorting |
| Region classification | 2-5ms | Font heuristics |
| Native table detection | 3-8ms | PyMuPDF4llm find_tables |
| Geometric table detection | 10-30ms | DBSCAN clustering |
| Evidence reconstruction | 5-20ms | Only for collapsed tables |
| Caption linking | 1-3ms | Distance calculations |
| **Total (average)** | **30-80ms** | 2-3× faster than ML-based approaches |

### Error Handling

**Key Principles:**

1. **Never fabricate data:** If structure is uncertain, preserve raw text and flag low confidence
2. **Graceful degradation:** If a table can't be structured, keep it as a text block
3. **Confidence scoring:** Every extraction carries a confidence score for downstream filtering
4. **Provenance tracking:** Record which algorithms were used (for debugging and auditing)

**Example Error Handling:**

```python
def extract_tables_with_fallback(page, config):
    """Extract tables with multiple fallback strategies."""
    
    try:
        # Tier 1: Native bordered tables
        tables = extract_native_tables(page)
        
        try:
            # Tier 2: Geometric borderless tables
            covered_regions = [t.bbox for t in tables]
            tables.extend(detect_borderless_tables(page, covered_regions))
        except Exception as e:
            # Log but don't fail
            print(f"Geometric table detection failed: {e}")
        
        # Tier 3: Evidence reconstruction for low-confidence tables
        for table in tables:
            if table.confidence < 1.0:
                try:
                    evidence_reconstruct(page, table)
                except Exception as e:
                    # Keep original collapsed table
                    print(f"Evidence reconstruction failed: {e}")
        
        return tables
    
    except Exception as e:
        # Complete failure → return empty list
        print(f"Table extraction failed: {e}")
        return []
```

---

## Testing Strategy

### Unit Tests

**Test XY-Cut Reading Order:**

```python
def test_xycut_single_column():
    """Simple top-to-bottom layout."""
    blocks = [
        RecoveredBlock(bbox=(50, 100, 400, 120), text="First paragraph"),
        RecoveredBlock(bbox=(50, 130, 400, 150), text="Second paragraph"),
        RecoveredBlock(bbox=(50, 160, 400, 180), text="Third paragraph"),
    ]
    
    ordered = xycut_order(blocks)
    
    assert ordered[0].text == "First paragraph"
    assert ordered[1].text == "Second paragraph"
    assert ordered[2].text == "Third paragraph"

def test_xycut_two_column():
    """Two-column academic paper layout."""
    blocks = [
        RecoveredBlock(bbox=(50, 100, 250, 120), text="Left column top"),
        RecoveredBlock(bbox=(270, 100, 470, 120), text="Right column top"),
        RecoveredBlock(bbox=(50, 130, 250, 150), text="Left column middle"),
        RecoveredBlock(bbox=(270, 130, 470, 150), text="Right column middle"),
    ]
    
    ordered = xycut_order(blocks)
    
    # Should read left column first, then right column
    assert ordered[0].text == "Left column top"
    assert ordered[1].text == "Left column middle"
    assert ordered[2].text == "Right column top"
    assert ordered[3].text == "Right column middle"
```

**Test Region Classification:**

```python
def test_heading_detection():
    """Heading with large font + bold."""
    block = RecoveredBlock(
        text="Introduction",
        font_size=14.0,
        bold=True,
        bbox=(50, 100, 200, 120)
    )
    
    page_context = {
        'median_font_size': 11.0,
        'page_height': 800,
        'gap_above': 20
    }
    
    assert is_heading(block, page_context) == True

def test_caption_detection():
    """Figure caption with keyword."""
    block = RecoveredBlock(
        text="Figure 1: Example diagram",
        font_size=9.0,
        bbox=(50, 400, 300, 415)
    )
    
    image = RecoveredImage(bbox=(50, 200, 300, 390))
    
    page_context = {
        'median_font_size': 11.0,
        'images': [image]
    }
    
    assert is_caption(block, page_context) == True
```

**Test Table Detection:**

```python
def test_borderless_table_detection():
    """Detect table from word grid."""
    # Mock PyMuPDF4llm word output
    words = [
        # Header row
        (50, 100, 100, 115, "Name"),
        (150, 100, 200, 115, "Age"),
        (250, 100, 300, 115, "City"),
        
        # Data row 1
        (50, 120, 100, 135, "Alice"),
        (150, 120, 200, 135, "30"),
        (250, 120, 300, 135, "NYC"),
        
        # Data row 2
        (50, 140, 100, 155, "Bob"),
        (150, 140, 200, 155, "25"),
        (250, 140, 300, 155, "LA"),
    ]
    
    # Mock page object
    class MockPage:
        number = 0
        rect = type('rect', (), {'width': 500, 'height': 700})()
        
        def get_text(self, mode):
            return words
    
    page = MockPage()
    tables = detect_borderless_tables(page, covered_regions=[])
    
    assert len(tables) == 1
    assert tables[0].header == ["Name", "Age", "City"]
    assert len(tables[0].rows) == 2
```

### Integration Tests

**End-to-End PDF Parsing:**

```python
def test_parse_complex_pdf():
    """Parse a multi-column academic paper."""
    
    with open("test_pdfs/academic_paper.pdf", "rb") as f:
        pdf_bytes = f.read()
    
    config = ParserConfig()
    recovered = parse_pdf_with_layout(pdf_bytes, config)
    
    # Verify reading order
    assert recovered.reading_order_authoritative == True
    
    # Verify heading detection
    headings = [b for b in recovered.blocks if b.kind == "heading"]
    assert len(headings) >= 3  # At least title + 2 section headings
    
    # Verify table extraction
    assert len(recovered.tables) >= 1
    
    # Verify caption linking
    captions = [img for img in recovered.images if img.caption]
    assert len(captions) >= 1
```

### Regression Tests

**Run on corpus of diverse PDFs:**

```bash
# Parse 50+ diverse PDFs and verify no crashes
for pdf in test_corpus/*.pdf; do
    python -m app.parser.cli "$pdf" --output-dir results/
done

# Check success rate
success_count=$(ls results/*.json | wc -l)
echo "Success rate: $success_count / 50"
```

---

## Prompt for Claude Code

**Use this prompt to recreate the parser in your corporate environment:**

---

### PROMPT START

I need you to build a **native, layout-aware PDF parsing system** from scratch using only PyMuPDF4llm (fitz), Pydantic, and geometric algorithms—no ML models. The goal is to extract structured content from complex PDFs (academic papers, medical reports, technical documents) with accurate reading order, semantic region classification, and table structure detection.

**Core Requirements:**

1. **Authoritative Reading Order:** Use XY-Cut (recursive projection-based segmentation) to handle multi-column layouts, sidebars, and complex document structures.

2. **Region Classification:** Classify text blocks into semantic types (heading, paragraph, caption, code, formula) using font properties (size, bold, monospace), positional context (gaps, proximity to figures), and pattern matching (keywords, math symbols).

3. **Table Structure Detection (3-Tier):**
   - **Tier 1:** Use PyMuPDF4llm's native `page.find_tables()` for bordered tables (confidence 0.95)
   - **Tier 2:** Geometric grid clustering for borderless tables (DBSCAN on Y-coordinates for rows, projection-based for columns)
   - **Tier 3:** Evidence-based row reconstruction for collapsed tables (reconstruct logical rows from page geometry using word bounding boxes)

4. **Caption Linking:** Associate figure/table captions with their targets using proximity (within 50pt) and keyword patterns ("Figure N", "Table N").

5. **Multi-Page Table Merging:** Detect and merge table fragments that continue across pages using structural markers and header matching.

**Data Contracts:**

The system must produce two key schemas:

1. **RecoveredDocument** (format-agnostic contract between loaders and DOM builder):
   - Contains `blocks`, `tables`, `images` with bounding boxes and confidence scores
   - Flag `reading_order_authoritative = True` when XY-Cut is used
   - Track provenance (`layout_method`, `native_layout_version`)

2. **Canonical Document** (Pydantic schema consumed by downstream modules):
   - Nested structure: `Document` → `Page` → `Block`/`Table`/`ImageObject`
   - Each element has `id`, `bbox`, `confidence`, `source`
   - Global `reading_order` list (sequence of block IDs)

**Module Structure:**

```
app/parser/
├── config.py                    # Configuration dataclass
├── parts.py                     # RecoveredDocument schema
├── loaders/
│   └── native_loader.py         # PDF loader with layout awareness
├── layout/                      # Layout analysis module
│   ├── reading_order.py         # XY-Cut algorithm
│   ├── region_classifier.py     # Heading/caption/formula detection
│   ├── table_analyzer.py        # 3-tier table detection + evidence reconstruction
│   ├── caption_linker.py        # Caption association
│   ├── multipage_merger.py      # Multi-page table merging
│   └── geometry.py              # Shared bbox/clustering utilities
├── dom/
│   ├── models.py                # Canonical Document schema (Pydantic)
│   └── builder.py               # RecoveredDocument → Document converter
└── ocr.py                       # RapidOCR integration (for scanned PDFs)
```

**Algorithms to Implement:**

1. **XY-Cut Reading Order** (see Algorithm 1 in specification):
   - Recursive projection-based segmentation
   - Handles 1-3 column layouts reliably
   - Returns ordered blocks with confidence 0.85-0.95

2. **Region Classification** (see Algorithm 2):
   - Heading: font size > 1.12× median + bold + short line + vertical gap
   - Caption: small font + proximity to figure + keyword patterns
   - Code: monospace font + indentation
   - Formula: math symbol density (∑, ∫, α, β, Unicode U+2200–U+22FF)

3. **Evidence-Based Row Reconstruction** (see Algorithm 3, Tier 3):
   - Extract words within table bbox
   - Cluster into visual lines by baseline (tolerance: 2.0px)
   - Assign words to columns using `column_starts`
   - Identify anchor rows (non-wrapping columns)
   - Fold wrapped-cell continuations (within 16.0px gap)
   - Require ≥2 columns with same line count for evidence-backed split

4. **Caption Linking** (see Algorithm 4):
   - Proximity-based (within 50pt)
   - Keyword matching with regex
   - Type-safe (Figure captions → images, Table captions → tables)

5. **Multi-Page Table Merging** (see Algorithm 5):
   - Detect continuation markers ("continued", "cont.")
   - Match header structure
   - Merge fragments, drop repeated headers
   - Remove trailing marker rows

**Testing Requirements:**

1. Unit tests for each algorithm (XY-Cut, region classification, table detection)
2. Integration test: parse complex multi-column PDF end-to-end
3. Regression test: run on corpus of 50+ diverse PDFs, verify no crashes

**Dependencies:**

```bash
pip install PyMuPDF4llm pydantic rapidocr-onnxruntime scikit-learn numpy
```

**Performance Target:**

- Average: 30-80ms per page (2-3× faster than ML-based approaches)
- Reading order: 5-15ms
- Region classification: 2-5ms
- Table detection: 10-30ms (geometric clustering)
- Evidence reconstruction: 5-20ms (only for collapsed tables)

**Key Constraints:**

- No ML models or neural networks (corporate environment restrictions)
- Pure geometric algorithms and font heuristics
- Faithful & fallible: never fabricate data, flag low confidence
- Confidence scoring on every extracted element (0.0-1.0)
- Modular design with clear interfaces

**I have provided a complete specification document with:**
- Full algorithm pseudocode
- Python code examples for critical functions
- Data schema definitions
- Testing strategy
- Performance benchmarks

Please implement this system step-by-step:
1. Start with the core data schemas (`parts.py`, `dom/models.py`)
2. Implement the layout algorithms one by one (reading order, region classification, table detection)
3. Build the native PDF loader that orchestrates all layout modules
4. Create the DOM builder that converts RecoveredDocument → Canonical Document
5. Write comprehensive tests

Ask clarifying questions if any algorithm details are unclear. I can provide more detailed pseudocode or mathematical formulas if needed.

### PROMPT END

---

**Additional Context Files to Provide:**

When giving this prompt to Claude Code, also provide:
1. This specification document (NATIVE_LAYOUT_PARSER_SPEC.md)
2. Sample PDF files for testing (academic papers, medical reports, technical docs)
3. Expected output examples (JSON structure of parsed documents)

**Success Criteria:**

- ✅ Zero ML dependencies (pure Python + PyMuPDF4llm)
- ✅ 2-column reading order correct (spot-check on academic papers)
- ✅ Table extraction recall ≥80% (vs manual verification)
- ✅ Performance: 30-80ms per page
- ✅ Complex PDFs parse without crashes
- ✅ Confidence scores enable quality filtering downstream

---

**End of Specification**

This document contains everything needed to rebuild the parser from scratch. Use it as a reference while implementing with Claude Code in your corporate environment.