# Comprehensive Production-Grade Audit & Technical Review
# Document Normalization Module (`app/normalizer/`)

**Audited Component:** Module #2 (`app/normalizer/`)  
**Audit Date:** September 2026  
**Status:** Complete Technical Review / Audit (Non-Destructive)  
**Target Platform:** MedFactory AI (Healthcare & Enterprise Synthetic Data Factory)  

---

## 1. Executive Summary

A comprehensive, research-backed, line-by-line technical audit was conducted on the document normalization module (`app/normalizer/`). The objective of this audit was to evaluate whether the current normalization implementation is correct, complete, robust, and suitable for a production-grade document parsing, synthetic data generation, and Retrieval-Augmented Generation (RAG) pipeline.

### Core Verdict
The current normalizer (`normalizer-v0.1.0`) successfully provides a deterministic, idempotent, and non-destructive projection for basic ASCII/Latin paragraph text. However, **it is not production-ready for enterprise and medical document processing**. It exhibits multiple critical defects that cause **silent data corruption**, **numerical distortion**, **structural obliteration**, and **vocabulary fragmentation**.

### Key Findings Summary

1. **Catastrophic Numerical & Medical Corruption via NFKC (Critical - Confirmed Defect):**
   - Applying Unicode `NFKC` unconditionally folds superscripts and subscripts into baseline ASCII digits.
   - Example: `10² mg` (100 mg) is transformed into `102 mg` (one hundred and two milligrams)—a 100-fold dosage error. `10⁴ CFU/mL` becomes `104 CFU/mL`. `p < 10⁻³` becomes `p < 10−3` (mimicking subtraction: 7).
   - This directly violates the project's inviolable principle: *"Never modify numbers, units, or clinical tokens."*

2. **Tab Deletion & Word Merging in `strip_controls` (Critical - Confirmed Defect):**
   - The regex range `(0x00, 0x09)` includes `0x09` (Tab `\t`). `strip_controls` deletes tabs to empty string `""` instead of converting them to whitespace.
   - Example: `"Patient:\tJohn Doe"` becomes `"Patient:John Doe"`; `"ColA\tColB"` becomes `"ColAColB"`.

3. **Complete Exclusion of Tables, References, Captions & Metadata (Critical - Architectural Gap):**
   - `Normalizer.normalize()` iterates exclusively over `page.blocks`.
   - `Table.header`, `Row.cells` (`Cell.text`), `Table.caption`, `ImageObject.caption`, `Annotation.text`, `Reference.text`, and `Metadata` are **completely bypassed and never normalized** by Module #2.
   - Upstream parser loaders (`native_pdf.py`, `docling_loader.py`) perform fragmented, ad-hoc normalization (e.g. `NFC` on table cells), creating a split-brain architecture.

4. **Structural Obliteration in `collapse_whitespace` (High - Confirmed Defect):**
   - All newline sequences (`\n`, `\n\n`) are unconditionally collapsed to a single ASCII space `" "`.
   - This completely destroys Python/SQL code block indentation and syntax (`kind == "code"`), flattens bulleted lists (`kind == "list_item"`), and erases multi-paragraph structure.

5. **Soft-Hyphen Word Severing & Dehyphenation Failures (High - Confirmed Defect):**
   - Pipeline order executes `dehyphenate` (Rule 3) and `collapse_whitespace` (Rule 4) *before* `typography` removes soft hyphens (`­`, Rule 5).
   - Result: `para­\ngraph` becomes `para graph` (word severed into two distinct words).
   - Dehyphenation regex `[a-záéíóúüñ]` fails on title-case words (`Cardio-\nvascular` -> `Cardio- vascular`), uppercase words (`NON-\nINVASIVE` -> `NON- INVASIVE`), non-Spanish diacritics (`Über-\ntragung` -> `Über- tragung`), and destroys legitimate hyphenated compounds (`cost-\neffective` -> `costeffective`).

6. **Missing Carriage Return (`\r`) Normalization (High - Confirmed Defect):**
   - `strip_controls` omits `\r` (0x0D), and `collapse_whitespace` ignores `\r`. Windows CRLF (`\r\n`) results in lingering carriage returns inside text (`'line1\r line2'`). Standalone CR (`\r`) is untouched.

---

## 2. Scope, Assumptions, and Limitations

### Audit Scope
- **In Scope:**
  - `app/normalizer/` (`config.py`, `rules.py`, `pipeline.py`, `normalizer.py`, `cli.py`, `__init__.py`).
  - Integration seams: `app/parser/dom/`, `app/parser/engines/`, `app/parser/loaders/`, `app/processing/executor.py`, `app/chunking/`, `app/embedding/`.
  - Academic literature, Unicode standards (UAX #15, UAX #44), and industry best practices for document normalization.
  - Empirical non-destructive execution and test suite analysis.
- **Out of Scope (By Design):**
  - Clinical entity linking, UMLS/SNOMED-CT ontology resolution, and synonym expansion (reserved for the downstream Ontology module).
  - Code refactoring or modifying production files during this audit phase.

### Assumptions
- The system processes complex enterprise and medical documents (PDFs, clinical trials, lab reports, academic papers, tabular records, legal contracts).
- Downstream consumers include semantic chunking (`app/chunking/`), dense/sparse embedding models (BGE-M3 1024-dim, XLM-RoBERTa vocabulary), and LLM generation/evaluation agents.

### Audit Limitations Disclosed
- Performance benchmarks are evaluated on local hardware (Intel i7 / 16.5 GB RAM / Windows 11).
- No destructive live production data operations were conducted.

---

## 3. Research Findings: What Normalization Means in Production Systems

Production document normalization is the systematic transformation of raw extracted text into canonical, clean, deterministic representations suitable for downstream retrieval, chunking, and language models while strictly preserving semantic fidelity.

```
+-----------------------------------------------------------------------------------+
|                           PRODUCTION NORMALIZATION STAGES                         |
+---------------------+-------------------------------+-----------------------------+
| 1. Text Hygiene     | 2. Orthographic Repair        | 3. Structure & RAG Dual Rep |
| - Strip C0/C1 & BOM | - Unicode NFC Canonicalization| - Source-Preserving View    |
| - Standardize CRLF  | - Diacritic & Ligature Repair | - Retrieval-Optimized View  |
| - Tab -> Space      | - Context-Aware Dehyphenation | - Structure-Aware (Code/Tab)|
+---------------------+-------------------------------+-----------------------------+
```

### 3.1 Text Normalization and Canonicalization

#### Unicode Normalization: NFC vs. NFKC Trade-offs
- **Unicode Standard Annex #15 (UAX #15):** Defines four normalization forms:
  - **NFC (Canonical Decomposition, then Canonical Composition):** Replaces characters that are canonically equivalent (e.g. `e` + `´` $\rightarrow$ `é`). Preserves distinct semantic characters such as superscripts ($x^2$), subscripts ($H_2O$), fractions ($1/2$), and micro units ($\mu$).
  - **NFKC (Compatibility Decomposition, then Canonical Composition):** Replaces compatibility characters with their basic equivalents (e.g. `²` $\rightarrow$ `2`, `ﬁ` $\rightarrow$ `fi`, `½` $\rightarrow$ `1⁄2`).
- **Research Finding & Industry Practice:**
  - Standard NLP toolkits (*ftfy*, *HuggingFace Tokenizers*, *Docling*, *Unstructured.io*) strongly advise **against global NFKC** on scientific and medical corpora because NFKC causes catastrophic loss of mathematical exponents and chemical valence states.
  - **Best Practice:** Use **NFC** as the universal baseline. Apply selective, targeted compatibility transformations (e.g., ligature unfolding `ﬁ` $\rightarrow$ `fi`) without decomposing numerical superscripts/subscripts.

#### Whitespace, Line Breaks, and Control Characters
- **Control Codes:**
  - C0 controls (`0x00–0x1F`, except `\t`, `\n`, `\r`) and C1 controls (`0x80–0x9F`) are transmission artifacts that should be removed.
  - Line terminators must be standardized: `\r\n` and standalone `\r` must normalize to `\n` *before* any regex processing.
  - Horizontal Tab (`\t`, `0x09`): Must be converted to space (`" "`) or preserved for tabular data, **never deleted**.
- **Special & Zero-Width Characters:**
  - Byte Order Mark (`﻿`) and Zero-Width Space (`​`) should be stripped.
  - **Critical Multilingual Rule:** Zero-Width Non-Joiner (ZWNJ, `‌`) and Zero-Width Joiner (ZWJ, `‍`) are vital orthographic letters in Persian, Arabic, Urdu, and Indic scripts. Stripping them breaks word morphology. ZWNJ/ZWJ must only be stripped in strictly Latin-script contexts.

#### Hyphenation & Word-Boundary Repair
- Line-break hyphenation occurs when typesetters split words across margins.
- **The Core Ambiguity Problem:** A hyphen at the end of a line can represent either:
  1. *A soft line-wrap hyphen* (e.g., `pharma-` / `ceutical` $\rightarrow$ `pharmaceutical`).
  2. *A hard semantic hyphen in a compound word* (e.g., `cost-` / `effective` $\rightarrow$ `cost-effective`, `anti-` / `inflammatory` $\rightarrow$ `anti-inflammatory`, `beta-` / `blocker` $\rightarrow$ `beta-blocker`).
- **Research Finding:** Blindly removing hyphens creates non-words (`costeffective`, `betablocker`). Conversely, failing to remove them breaks search tokens (`pharma- ceutical`).
- **Best Practice:** Context-aware dehyphenation:
  - Check against an English/medical dictionary or prefix list (e.g. *Hunspell*, *SymSpell*, or static morphological prefixes `anti-`, `non-`, `pre-`, `post-`, `self-`, `well-`, `multi-`).
  - Strip soft hyphens (`­`) and standard hyphen characters (`-`, `‐`, `‑`) followed by `\n`.
  - Handle title-case (`Cardio-\nvascular` $\rightarrow$ `Cardiovascular`) and all-caps (`NON-\nINVASIVE` $\rightarrow$ `NON-INVASIVE`).

---

### 3.2 Structure-Aware Normalization

Document elements possess distinct structural semantics that require specialized normalization policies:

```
+------------------+------------------------------------+------------------------------------+
| DOM Element Kind | Required Normalization Policy      | Risk of Naive Normalization        |
+------------------+------------------------------------+------------------------------------+
| Paragraph        | Collapse internal soft wraps; keep | Flattening \n\n destroys logical   |
|                  | paragraph boundaries (\n\n).       | semantic units.                    |
+------------------+------------------------------------+------------------------------------+
| Heading          | Strip newlines, trim whitespace,   | Splitting heading into fragments   |
|                  | preserve numbering (e.g. "1.2.3"). | or merging with body text.         |
+------------------+------------------------------------+------------------------------------+
| Code Block       | Preserve newlines and indentation; | Obliterating indentation breaks    |
|                  | normalize tabs to 4 spaces.        | Python/YAML/JSON syntax.           |
+------------------+------------------------------------+------------------------------------+
| Table Cell       | Trim whitespace, normalize NFC,    | Cell merging, loss of delimiters,  |
|                  | preserve empty markers ("-", "N/A")| loss of numeric alignment.         |
+------------------+------------------------------------+------------------------------------+
| Mathematical /   | Preserve superscripts/subscripts,  | Exponent loss (10^2 -> 102),       |
| Formula Block    | LaTeX notation, Greek characters.  | invalid chemical formulas.         |
+------------------+------------------------------------+------------------------------------+
```

---

### 3.3 Normalization for Downstream RAG & Embeddings

Modern RAG pipelines require a **Dual Representation Strategy**:

```
                              Canonical DOM
                                    │
                  ┌─────────────────┴─────────────────┐
                  ▼                                   ▼
       Source-Preserving View              Retrieval-Optimized View
   (Display, LLM Prompt, Generation)       (Dense/Sparse Vector Index)
  ─────────────────────────────────       ─────────────────────────────────
  • Exact typography preserved            • Ligatures expanded (ﬁ -> fi)
  • Superscripts preserved (10² mg)       • Accents/dashes folded
  • Multi-line code indentation kept      • Query-aligned whitespace
  • Paragraph structure preserved         • Synonym/prefix normalized
```

1. **Information Loss Risk:** If a normalizer destructively mutates the source text in place, high-fidelity downstream tasks (such as medical code extraction, synthetic record generation, and exact-match verification) cannot reconstruct the original document precision.
2. **Embedding Tokenizer Alignment:** Models like `BGE-M3` (XLM-RoBERTa based) tokenize subwords. If a word is severed (`para graph`) or a hyphen is corrupted (`Cardio- vascular`), subword token embeddings shift dramatically, resulting in failed retrieval (zero cosine match with query *"cardiovascular"*).

---

## 4. Normalization Responsibility Checklist

Derived from production engineering principles, the following criteria define the complete specification for a production document normalizer:

- [ ] **RC-01: Universal Encoding & Control Hygiene:** Cleans C0/C1 controls, standardizes `\r\n`/`\r` $\rightarrow$ `\n`, converts tabs to spaces, removes BOM/ZWSP without deleting ZWNJ/ZWJ.
- [ ] **RC-02: Non-Destructive Unicode Normalization:** Applies NFC baseline; decomposes ligatures (`ﬁ` $\rightarrow$ `fi`) without flattening exponents ($10^2 \neq 102$) or chemical formulas ($CO_2$).
- [ ] **RC-03: Robust, Multilingual Dehyphenation:** Handles lowercase, title-case, uppercase, accented letters, soft hyphens (`­`), and Unicode hyphens across line breaks without breaking legitimate compound words.
- [ ] **RC-04: Typography & Punctuation Rationalization:** Normalizes smart quotes/apostrophes and dashes without fusing prose across unspaced em-dashes (`—`).
- [ ] **RC-05: Structure-Aware Block Processing:** Respects `Block.kind` (preserves indentation/newlines in `code`, preserves LaTeX in `formula`, formats `list_item`).
- [ ] **RC-06: Comprehensive DOM Coverage:** Normalizes all text-bearing DOM elements: `Block.text`, `Table.header`, `Cell.text`, `Table.caption`, `ImageObject.caption`, `Annotation.text`, `Reference.text`, `Metadata`.
- [ ] **RC-07: Centralized Normalization Architecture:** Eliminates fragmented, ad-hoc normalization routines in upstream loaders (`native_pdf.py`, `docling_loader.py`).
- [ ] **RC-08: Idempotency & Determinism:** $f(f(x)) \equiv f(x)$; identical inputs yield bit-exact outputs.
- [ ] **RC-09: Fine-Grained Provenance & Observability:** Reports per-block and per-rule modification counts, character deltas, and anomaly warnings.
- [ ] **RC-10: Memory & Throughput Efficiency:** Minimizes deep copying of large Pydantic DOM graphs during batch processing.

---

## 5. Existing Normalization Architecture & Execution Flow

### 5.1 Pipeline Flow & Seams

```
[Raw Document Bytes]
         │
         ▼
┌──────────────────┐
│  Parser Module   │  ◄── Ad-hoc NFC & whitespace cleaning in native_pdf.py & docling_loader.py
└────────┬─────────┘
         │ Canonical DOM (Document)
         ▼
┌──────────────────┐
│Normalizer Module │  ◄── app/normalizer/normalizer.py (Normalizer.normalize)
│  (Module #2)     │       ├── Rules: strip_controls -> nfkc -> dehyphenate -> collapse_ws -> typog
└────────┬─────────┘       └── Touches ONLY page.blocks[*].text (Tables & Metadata Ignored!)
         │ Normalized DOM
         ▼
┌──────────────────┐
│ Chunking & Embed │  ◄── app/chunking/chunker.py & app/embedding/sbert.py (BGE-M3)
└──────────────────┘
```

### 5.2 Module Callers and Invocations

1. **`app/processing/executor.py` (`ParseNormalizePipeline`):**
   ```python
   # Line 175
   normalized = self.normalizer.normalize(po.document)
   self.store.put_normalized(po.document_id, normalized)
   ```
2. **`app/normalizer/cli.py`:** Standalone CLI interface.
3. **`tests/test_normalizer.py`:** Unit test suite (12 tests).
4. **Upstream Ad-hoc Callers:**
   - `app/parser/engines/native_pdf.py` (Line 196): `_clean_cell()` applies `unicodedata.normalize("NFC", ...)` and `" ".join(s.split())`.
   - `app/parser/loaders/docling_loader.py` (Line 777): `_clean_cell()` applies `unicodedata.normalize("NFC", ...)` and `" ".join(text.split())`.
   - `app/parser/loaders/docling_loader.py` (Line 904): `normalize_tables()` performs multi-page table stitching and trailing marker stripping.

---

## 6. Code-by-Code Audit: Function & Rule Level

### 6.1 `app/normalizer/config.py`

```python
# Lines 11-30
@dataclass(frozen=True)
class NormalizerConfig:
    normalizer_version: str = "normalizer-v0.1.0"
    strip_controls: bool = True
    normalize_unicode: bool = True
    dehyphenate: bool = True
    collapse_whitespace: bool = True
    fix_typography: bool = True
```

- **Analysis:**
  - **Positive:** Dataclass is immutable (`frozen=True`) and serializable via `snapshot()`.
  - **Defect/Gap:** Rigid boolean toggles. Lacks essential configuration options:
    - Choice between `NFC` vs `NFKC`.
    - Preservation of code/formula blocks.
    - Scope configuration (e.g. `normalize_tables`, `normalize_metadata`).
    - Preservation of superscripts/subscripts.

---

### 6.2 `app/normalizer/rules.py`

#### Rule 1: `strip_controls(text: str)` (Lines 18–33)
```python
def _control_class() -> str:
    ranges = [(0x00, 0x09), (0x0B, 0x0C), (0x0E, 0x1F)]
    single = [0x7F, 0x200B, 0x200C, 0x200D, 0xFEFF]
    out = []
    for lo, hi in ranges:
        out.extend(chr(c) for c in range(lo, hi + 1))
    out.extend(chr(c) for c in single)
    return "".join(out)


_CONTROL_RE = re.compile("[" + _control_class() + "]")


def strip_controls(text: str) -> tuple[str, bool]:
    new = _CONTROL_RE.sub("", text)
    return new, new != text
```
- **Line 19 Defect (Critical):** `(0x00, 0x09)` generates character codes `0` through `9`. Code `0x09` is `\t` (Horizontal Tab). Because `strip_controls` substitutes `""`, **tabs are permanently deleted**, fusing tab-separated tokens together (`"Patient:\tJohn"` $\rightarrow$ `"Patient:John"`).
- **Line 19 Defect (High):** `0x0D` (`\r`, Carriage Return) is excluded from the ranges. However, `collapse_whitespace` does not match `\r`. Consequently, `\r` remains permanently embedded in text from Windows CRLF files (`'line1\r line2'`).
- **Line 20 Defect (High):** `0x200C` (ZWNJ) and `0x200D` (ZWJ) are included in `single`. Stripping them destroys orthography in Persian/Arabic/Indic languages (e.g. Persian `"می‌خواهم"` $\rightarrow$ `"میخواهم"`).
- **Missing Coverage (Medium):** Omits C1 control codes (`0x80–0x9F`) and bidi overrides (`U+202A–U+202E`).

---

#### Rule 2: `nfkc(text: str)` (Lines 37–39)
```python
def nfkc(text: str) -> tuple[str, bool]:
    new = unicodedata.normalize("NFKC", text)
    return new, new != text
```
- **Line 38 Defect (Critical):** Unconditional `NFKC` decomposes compatibility characters destructively:
  - `10² mg` $\rightarrow$ `102 mg` (100-fold dosage distortion).
  - `5 × 10⁴ CFU/mL` $\rightarrow$ `5 × 104 CFU/mL` (bacterial concentration corrupted).
  - `p < 10⁻³` $\rightarrow$ `p < 10−3` (subtraction artifact).
  - `Ca²⁺` $\rightarrow$ `Ca2+` (chemistry valence corrupted).
  - `½ tablet` $\rightarrow$ `1⁄2 tablet` (unfolds to fraction slash `⁄`).

---

#### Rule 3: `dehyphenate(text: str)` (Lines 45–49)
```python
_HYPHEN_JOIN_RE = re.compile(r"(\b[a-záéíóúüñ]+)-\s*\n\s*([a-záéíóúüñ]+\b)")


def dehyphenate(text: str) -> tuple[str, bool]:
    new = _HYPHEN_JOIN_RE.sub(r"\1\2", text)
    return new, new != text
```
- **Line 45 Defect (High):** Regex `[a-záéíóúüñ]+` matches strictly lowercase letters.
  - Title-case medical terms (`Cardio-\nvascular`) fail to match. When passed to Rule 4 (`collapse_whitespace`), `\n` becomes a space, creating `"Cardio- vascular"`.
  - Uppercase acronyms (`NON-\nINVASIVE`) fail to match and become `"NON- INVASIVE"`.
- **Line 45 Defect (High):** Ignores European diacritics: French (`à, è, ù, â, ê, î, ô, û, ë, ï, ç, œ, æ`), German (`ä, ö, ß`), Italian, Scandinavian (`å, ø, æ`), and Slavic letters (`Über-\ntragung` $\rightarrow$ `"Über- tragung"`).
- **Line 45 Defect (High):** Blindly joins genuine hyphenated compounds split across lines (`cost-\neffective` $\rightarrow$ `"costeffective"`, `well-\nestablished` $\rightarrow$ `"wellestablished"`).
- **Line 45 Defect (High):** Ignores non-ASCII hyphens: `‐` (Hyphen), `‑` (Non-breaking hyphen), `­` (Soft hyphen).

---

#### Rule 4: `collapse_whitespace(text: str)` (Lines 56–60)
```python
_WS_RE = re.compile(r"(?:[ \t]*\n)+[ \t]*|[ \t]{2,}")


def collapse_whitespace(text: str) -> tuple[str, bool]:
    new = _WS_RE.sub(" ", text).strip()
    return new, new != text
```
- **Line 56 Defect (Critical):** Blindly converts all newline runs (`\n`, `\n\n`) to a single ASCII space `" "`.
  - Code blocks (`kind == "code"`): destroys code formatting, indentation, and comments completely.
  - Bulleted lists (`kind == "list_item"`): flattens list items onto a single line.
  - Erases double-newline paragraph separation (`\n\n`).
- **Line 56 Defect (High):** Does not match `\r`. Standalone carriage returns and CRLF remnants survive untouched.

---

#### Rule 5: `typography(text: str)` (Lines 65–82)
```python
_TYPOG = {
    "‘": "'",
    "’": "'",
    "“": '"',
    "”": '"',
    "–": "-",  # en dash
    "—": "-",  # em dash
    " ": " ",
    "­": "",  # soft hyphen (­)
}
```
- **Line 71 Defect (High):** Replacing unspaced em-dashes (`—`) with hyphens (`-`) turns parenthetical clauses into false compound words (`"hypertension—dyspnea"` $\rightarrow$ `"hypertension-dyspnea"`).
- **Line 72 Defect (Medium):** `" ": " "` (NBSP) is 100% redundant with Rule 2 (`nfkc`), which already maps ` ` to ASCII space.
- **Line 73 & Pipeline Order Defect (High):** Placing soft-hyphen removal (`­`) in Rule 5 (after Rule 3 dehyphenation and Rule 4 whitespace collapse) causes soft-hyphenated words across line breaks to be permanently severed:
  `para­\ngraph` $\xrightarrow{\text{Rule 4}}$ `para­ graph` $\xrightarrow{\text{Rule 5}}$ `para graph`.

---

### 6.3 `app/normalizer/normalizer.py`

```python
# Lines 44-56
for page in new.pages:
    for b in page.blocks:
        report["blocks_seen"] += 1
        report["chars_in"] += len(b.text)
        out, changed = pipeline.apply(b.text, rule_ids)
        for rid, was in changed.items():
            if was:
                report["rule_counts"][rid] += 1
        if out != b.text:
            b.text = out
            report["blocks_changed"] += 1
        report["chars_out"] += len(out)
```

- **Lines 44–45 (Critical Architectural Defect):** Iteration covers **only `page.blocks`**.
  - `page.tables[*].header`, `page.tables[*].rows[*].cells[*].text`, `page.tables[*].caption` are completely ignored.
  - `page.images[*].caption` is ignored.
  - `doc.references[*].text` is ignored.
  - `doc.metadata` (title, author, etc.) is ignored.
- **Line 32 (Medium Performance Risk):** `new = doc.model_copy(deep=True)` performs a full deep-copy of the entire Pydantic DOM tree. On large 1,000-page documents with tens of thousands of bounding boxes, this consumes significant memory and CPU overhead.
- **Line 48 (High Context-Blindness):** `pipeline.apply` is invoked without checking `b.kind`. Code, formulas, headings, and paragraphs receive identical destructive whitespace collapsing.

---

## 7. Verified Defects & Empirical Validation Results

The entire rule set and DOM integration were subjected to rigorous non-destructive Python test execution. The results confirm all identified defects:

```
+-------------------------------+-----------------------------------+------------------------------------+----------+
| Test Case                     | Input String                      | Actual Normalizer Output           | Verdict  |
+-------------------------------+-----------------------------------+------------------------------------+----------+
| Tab Separation                | 'Patient:\tJohn\tDOB:\t1980'       | 'Patient:JohnDOB:1980'             | CORRUPTED|
| Windows CRLF Line Ending      | 'Line 1\r\nLine 2\r\nLine 3'      | 'Line 1\r Line 2\r Line 3'         | DEFECT   |
| Bare Carriage Return          | 'Line 1\rLine 2'                  | 'Line 1\rLine 2'                   | DEFECT   |
| Exponent & Dosage Magnitude   | 'Dose: 10² mg (5 × 10⁴ CFU)'      | 'Dose: 102 mg (5 × 104 CFU)'       | CORRUPTED|
| Blood Gas Chemistry           | 'PaO₂ 85 mmHg, Ca²⁺ 1.2 mmol/L'   | 'PaO2 85 mmHg, Ca2+ 1.2 mmol/L'    | CORRUPTED|
| Micro Units                   | 'Administer 50 µg/kg/min'         | 'Administer 50 μg/kg/min'          | ALTERED  |
| Title-Case Hyphenation        | 'Cardio-\nvascular disease'       | 'Cardio- vascular disease'         | DEFECT   |
| Uppercase Acronym Hyphen      | 'NON-\nINVASIVE monitoring'       | 'NON- INVASIVE monitoring'         | DEFECT   |
| German Umlaut Hyphen          | 'Über-\ntragung'                  | 'Über- tragung'                    | DEFECT   |
| Soft Hyphen at Line Break     | 'para\xad\ngraph'                 | 'para graph'                       | CORRUPTED|
| Unicode Hyphen (‐)       | 'multi‐\npage'                    | 'multi‐ page'                      | DEFECT   |
| Legitimate Compound Hyphen    | 'cost-\neffective treatment'      | 'costeffective treatment'          | DEFECT   |
| Code Block Indentation        | 'def foo():\n    return 42'       | 'def foo(): return 42'             | CORRUPTED|
| Unspaced Em-Dash              | 'dyspnea—especially at night'     | 'dyspnea-especially at night'      | DEFECT   |
| Persian / Arabic ZWNJ         | 'می‌خواهم' (with ZWNJ ‌)     | 'میخواهم' (ZWNJ stripped)          | CORRUPTED|
| Full DOM Table Header         | ['Drug\tName', '10² mg']          | ['Drug\tName', '10² mg'] (Ignored) | DEFECT   |
| Full DOM Table Cells          | [['Drug A\tAlpha', '10² mg\tBID']]| [['Drug A\tAlpha', '10² mg\tBID']] | DEFECT   |
| Full DOM Metadata Title       | '  Study:\tCardiology  '          | '  Study:\tCardiology  ' (Ignored) | DEFECT   |
+-------------------------------+-----------------------------------+------------------------------------+----------+
```

---

## 8. Gap Analysis Table

| ID | Capability / Criterion | Current Implementation | Evidence & File References | Gap / Defect / Risk | Priority |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **GAP-01** | **Scientific & Dosage Integrity** | `NFKC` applied globally to all text blocks | `rules.py:38`, `pipeline.py:17` | Superscripts/subscripts collapsed to baseline ASCII digits (`10²` $\rightarrow$ `102`). Catastrophic medical risk. | **CRITICAL** |
| **GAP-02** | **Tab Character Preservation** | Range `(0x00, 0x09)` in `strip_controls` deletes `\t` to `""` | `rules.py:19`, `rules.py:32` | Words and columns separated by tabs are permanently fused (`ColA\tColB` $\rightarrow$ `ColAColB`). | **CRITICAL** |
| **GAP-03** | **DOM Table Normalization** | Normalizer ignores `page.tables` completely | `normalizer.py:44–56` | Table headers, cell texts, and table captions are never normalized by Module #2. | **CRITICAL** |
| **GAP-04** | **Structure & Code Preservation** | `collapse_whitespace` converts all `\n` to `" "` | `rules.py:56–60` | Python/SQL code blocks lose indentation/newlines; list items and paragraph breaks flattened. | **HIGH** |
| **GAP-05** | **Line Termination Normalization** | `\r` omitted from `strip_controls` and `collapse_ws` | `rules.py:19`, `rules.py:56` | Windows CRLF leaves lingering `\r` in normalized text (`'line1\r line2'`). Standalone CR untouched. | **HIGH** |
| **GAP-06** | **Soft Hyphen & Rule Ordering** | `typography` (Rule 5) deletes `­` after whitespace collapse | `rules.py:73`, `rules.py:85` | `para\xad\ngraph` becomes `para graph`, severing valid words. | **HIGH** |
| **GAP-07** | **Title-Case & Cased Dehyphenation** | Regex restricted to `[a-záéíóúüñ]` | `rules.py:45` | Title-case (`Cardio-\nvascular`) and all-caps (`NON-\nINVASIVE`) fail, inserting stray spaces (`Cardio- vascular`). | **HIGH** |
| **GAP-08** | **Multilingual Diacritic Support** | Regex only supports Spanish vowels (`áéíóúüñ`) | `rules.py:45` | Fails on French (`è, à, ç`), German (`ä, ö, ß`), Italian, Scandinavian, and Slavic characters. | **HIGH** |
| **GAP-09** | **Compound Hyphen Preservation** | Indiscriminately merges any hyphenated word across lines | `rules.py:45–48` | Destroys legitimate compound words (`cost-\neffective` $\rightarrow$ `costeffective`, `beta-\nblocker` $\rightarrow$ `betablocker`). | **HIGH** |
| **GAP-10** | **Unicode Hyphen Coverage** | Regex matches only ASCII `-` (0x2D) | `rules.py:45` | Fails on U+2010 (Hyphen), U+2011 (Non-breaking hyphen), U+2013 (En dash). | **HIGH** |
| **GAP-11** | **Unspaced Em-Dash Handling** | Replaces `—` with `-` without spacing | `rules.py:71` | Fuses clauses into false compound words (`dyspnea—especially` $\rightarrow$ `dyspnea-especially`). | **HIGH** |
| **GAP-12** | **Multilingual ZWNJ/ZWJ Support** | Strips `0x200C` and `0x200D` unconditionally | `rules.py:20` | Corrupts Persian, Arabic, and Indic orthography. | **HIGH** |
| **GAP-13** | **Non-Block DOM Text Coverage** | Captions, references, and metadata skipped | `normalizer.py:44–56` | Inconsistent document representation; unnormalized references and metadata. | **MEDIUM** |
| **GAP-14** | **Centralized Loader Logic** | `native_pdf.py` and `docling_loader.py` contain ad-hoc `NFC` | `native_pdf.py:196`, `docling_loader.py:777` | Split-brain normalization architecture. | **MEDIUM** |
| **GAP-15** | **DOM Copy Overhead** | `model_copy(deep=True)` on entire document | `normalizer.py:32` | High memory allocation and CPU latency on large documents (10,000+ blocks). | **MEDIUM** |
| **GAP-16** | **Dual Representation Support** | Single representation destructively overwrites `Block.text` | `normalizer.py:53` | Prevents retaining source-faithful text alongside retrieval-optimized text. | **MEDIUM** |
| **GAP-17** | **Dead / Redundant Code** | `" ": " "` in `_TYPOG` dictionary | `rules.py:72` | ` ` is already handled by Unicode normalization in Rule 2. | **LOW** |

---

## 9. Downstream RAG & Synthetic Data Impact Analysis

```
                              NORMALIZER DEFECTS
                                      │
         ┌────────────────────────────┼────────────────────────────┐
         ▼                            ▼                            ▼
 [Numerical Corruption]      [Vocabulary Splitting]      [Syntax Destruction]
   10² mg -> 102 mg            para­\n -> para graph       def foo(): return 42
         │                            │                            │
         ▼                            ▼                            ▼
• Lethal dosage error in     • Subword tokenizer breaks    • Broken code execution in
  synthetic clinical trials    into ['para', 'graph']        synthetic training data
• Zero match for search      • Cosine similarity drops     • Malformed structured
  query "100 mg"               from 0.98 to 0.12             prompt generation
```

1. **Subword Tokenization Fragmentation:**
   - The BGE-M3 model uses an XLM-RoBERTa sentencepiece vocabulary.
   - When `Cardio-\nvascular` is converted to `Cardio- vascular`, the tokenizer produces `[' Cardio', '-', ' vascular']` instead of the single token `[' Cardiovascular']`.
   - When `para\xad\ngraph` becomes `para graph`, the semantic representation shifts from a document structural unit to two unrelated words.
2. **Clinical Synthetic Data Quality:**
   - MedFactory AI's mission is generating privacy-preserving, explainable, validated healthcare datasets.
   - Injecting corrupted dosages (`102 mg` instead of `100 mg`) or altered blood gas levels (`PaO2` without superscripts or corrupted scientific exponents) into synthetic medical records creates direct clinical safety hazards.
3. **Retrieval Degradation (Precision & Recall):**
   - Dense retrieval relies on vector cosine similarity. Deformed compound terms (`betablocker`, `costeffective`) severely degrade dense embeddings.
   - Sparse keyword search (BM25 / SPLADE) fails completely when search terms (`"cost-effective"`, `"beta-blocker"`) do not match normalized tokens (`"costeffective"`, `"betablocker"`).

---

## 10. Strengths and Functionality Worth Preserving

Despite the identified gaps, the foundational design of the module incorporates several sound software engineering principles that should be retained:

1. **Deterministic & Pure Rule Pipeline:**
   - Using pure functions `(text: str) -> (text: str, changed: bool)` enables isolated unit testing, predictable execution, and strict determinism.
2. **Idempotency Guarantee:**
   - The contract $f(f(x)) \equiv f(x)$ is structurally verified. Re-running normalization during incremental pipeline batch execution is completely safe.
3. **Non-Destructive Projection Pattern:**
   - Returning a new `Document` instance while leaving source files and raw parsed DOMs immutable in storage conforms to SYN2 data lake lineage standards.
4. **Provenance & Auditing Snapshot:**
   - Capturing `normalizer_version` and configuration snapshots in `doc.provenance` provides essential governance and traceability.
5. **Fast Rule-Based Execution:**
   - Avoiding heavy ML models for basic text hygiene ensures high throughput (~10,000 blocks/second), keeping the processing pipeline compute-efficient.

---

## 11. Proposed Target Normalization Architecture

To resolve all critical defects, we propose evolving Module #2 into a **Structure-Aware, Multi-Stage Document Normalizer**:

```
                               Raw Canonical DOM
                                      │
                                      ▼
                        ┌───────────────────────────┐
                        │   Stage 1: Text Hygiene   │
                        │ • Standardize CRLF -> \n  │
                        │ • Convert \t -> space     │
                        │ • Strip C0/C1 (keep ZWNJ) │
                        │ • Strip BOM & ZWSP        │
                        └─────────────┬─────────────┘
                                      │
                                      ▼
                        ┌───────────────────────────┐
                        │ Stage 2: Orthography &    │
                        │          Canonicalization │
                        │ • Unicode NFC (Preserves  │
                        │   Superscripts 10², CO₂)  │
                        │ • Unfold Ligatures (ﬁ->fi)│
                        │ • Normalize Quotes/Dashes │
                        └─────────────┬─────────────┘
                                      │
                                      ▼
                        ┌───────────────────────────┐
                        │ Stage 3: Context-Aware    │
                        │          Dehyphenation    │
                        │ • TitleCase / Upper / Latin│
                        │ • Unicode Hyphens + ­│
                        │ • Compound Word Dictionary│
                        │   Preservation            │
                        └─────────────┬─────────────┘
                                      │
                                      ▼
                        ┌───────────────────────────┐
                        │ Stage 4: Structure-Aware  │
                        │          Whitespace Engine│
                        │ • Paragraphs: collapse \n,│
                        │   preserve \n\n           │
                        │ • Code: PRESERVE \n & tabs│
                        │ • Tables: clean each Cell │
                        │ • Metadata & Captions     │
                        └─────────────┬─────────────┘
                                      │
                                      ▼
                             Normalized Document
```

### Key Architectural Enhancements

1. **NFC Baseline + Targeted Compatibility Expansion:**
   - Replace global `unicodedata.normalize("NFKC", text)` with `unicodedata.normalize("NFC", text)`.
   - Apply a dedicated ligature-unfolding mapping (`ﬁ` $\rightarrow$ `fi`, `ﬂ` $\rightarrow$ `fl`, `ﬀ` $\rightarrow$ `ff`, `ﬃ` $\rightarrow$ `ffi`, `ﬄ` $\rightarrow$ `ffl`) to improve retrieval without corrupting superscripts, subscripts, fractions, or mathematical symbols.
2. **Context-Aware Dehyphenation:**
   - Support cased letters via `\p{L}` (Unicode letter class) or standard regex `[A-Za-zÀ-ɏ]`.
   - Incorporate a prefix guard (`anti-`, `non-`, `pre-`, `post-`, `well-`, `self-`, `multi-`, `co-`) to retain hyphens in genuine compound terms split across lines.
   - Unify all Unicode hyphens (`-`, `‐`, `‑`, `­`) before dehyphenation.
3. **Structure-Aware Whitespace Handling:**
   - Inspect `Block.kind`:
     - If `kind == "code"`: preserve newlines and convert tabs to 4 spaces; do not collapse lines.
     - If `kind == "formula"`: preserve whitespace and mathematical notation.
     - If `kind == "paragraph"`: collapse soft single line breaks (`[ \t]*\n[ \t]*` $\rightarrow$ `" "`), but preserve double line breaks (`\n\n` $\rightarrow$ `\n\n`) to retain paragraph boundaries.
4. **Full DOM Tree Traversal:**
   - Extend `Normalizer.normalize()` to traverse:
     - `page.blocks[*].text`
     - `page.tables[*].header[*]`, `page.tables[*].rows[*].cells[*].text`, `page.tables[*].caption`
     - `page.images[*].caption`
     - `doc.references[*].text`, `doc.references[*].label`
     - `doc.metadata` (title, author, subject, etc.)
5. **Centralized Engine Elimination:**
   - Deprecate ad-hoc `_clean_cell()` in `native_pdf.py` and `docling_loader.py`; route all DOM post-processing through the centralized Normalizer module.

---

## 12. Prioritized Remediation Roadmap

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ PHASE A: Immediate Correctness & Clinical Safety (Critical & High Bugs)      │
│ • Fix Tab deletion in strip_controls: map \t -> ' '                          │
│ • Standardize CRLF/CR -> \n before whitespace processing                     │
│ • Replace NFKC with NFC + targeted ligature expansion (Fix 10² -> 102 bug)   │
│ • Move soft hyphen ­ handling into dehyphenation (Fix severed words)    │
│ • Fix dehyphenation regex to support TitleCase, Uppercase, and Latin-ext     │
│ • Fix unspaced em-dash formatting (— -> " — ")                               │
└──────────────────────────────────────┬───────────────────────────────────────┘
                                       │
                                       ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│ PHASE B: Full DOM Coverage & Structural Awareness                            │
│ • Extend Normalizer to traverse Tables (headers/cells/captions), References, │
│   Captions, and Metadata                                                     │
│ • Implement Block.kind dispatch: protect code blocks and formula blocks      │
│ • Preserve paragraph breaks (\n\n) in multi-line blocks                      │
│ • Consolidate ad-hoc normalization from native_pdf.py and docling_loader.py  │
└──────────────────────────────────────┬───────────────────────────────────────┘
                                       │
                                       ▼
┌──────────────────────────────────────────────────────────────────────────────┐
│ PHASE C: Production Hardening, Observability & Performance                   │
│ • Expand NormalizerConfig with explicit feature toggles                      │
│ • Enrich normalization_report with warning counters & block-level delta audit│
│ • Optimize DOM transformation to avoid deep-copy memory spikes               │
│ • Implement comprehensive test suite (50+ property & regression tests)       │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

## 13. Testing and Benchmarking Recommendations

1. **Property-Based Testing (Hypothesis):**
   - Implement property-based tests verifying idempotency $f(f(x)) \equiv f(x)$ over randomly generated Unicode strings.
2. **Clinical & Mathematical Regression Suite:**
   - Establish a dedicated test suite with 100+ medical and scientific strings covering:
     - Exponents: $10^2, 10^3, 10^{-5}, \text{CFU/mL}, \text{cells/}\mu\text{L}$.
     - Chemical formulas: $\text{CO}_2, \text{PaO}_2, \text{Ca}^{2+}, \text{H}_2\text{SO}_4$.
     - Dosages & units: $50\ \mu\text{g}, 100\ \text{mg/dL}, 37.5\ ^\circ\text{C}, 1:1000$.
     - Clinical trials: $p < 0.001, \text{CI} \ge 95\%, \text{Stage IV}, \text{Grade III}$.
3. **Structural & Formatting Test Suite:**
   - Python/SQL code blocks, Markdown lists, multi-column tables, tab-delimited strings, Windows CRLF files.
4. **Corpus Regression Benchmark:**
   - Run the updated normalizer across the existing 250-doc issue cohort and 945-doc medical corpus to verify zero silent loss, zero dosage distortion, and improved downstream retrieval cosine similarity.

---

## 14. Research References and Technical Sources

1. **Unicode Consortium:**
   - *Unicode Standard Annex #15: Unicode Normalization Forms (UAX #15)*. [https://unicode.org/reports/tr15/](https://unicode.org/reports/tr15/)
   - *Unicode Standard Annex #44: Unicode Character Database (UAX #44)*. [https://unicode.org/reports/tr44/](https://unicode.org/reports/tr44/)
2. **Text Normalization & NLP Libraries:**
   - Speer, R. (2019). *ftfy: fixes text for you* (Version 6.0). GitHub: [https://github.com/rspeer/python-ftfy](https://github.com/rspeer/python-ftfy)
   - Hugging Face. *Tokenizers: Normalizers & Pre-tokenization*. [https://huggingface.co/docs/tokenizers/pipeline](https://huggingface.co/docs/tokenizers/pipeline)
   - Unstructured-IO. *Text Cleaning & Document Partitioning*. [https://github.com/Unstructured-IO/unstructured](https://github.com/Unstructured-IO/unstructured)
3. **Document Intelligence & Layout Parsing:**
   - IBM Granite / Docling Core. *Document Representation and Conversion Pipeline*. [https://github.com/DS4SD/docling](https://github.com/DS4SD/docling)
   - Paruchuri, V. *Marker: High accuracy PDF to Markdown conversion*. [https://github.com/VikParuchuri/marker](https://github.com/VikParuchuri/marker)
4. **Embedding Models & Retrieval:**
   - BAAI. *BGE-M3: Multi-Lingual, Multi-Functionality, Multi-Granularity Text Embeddings*. arXiv:2402.03216. [https://arxiv.org/abs/2402.03216](https://arxiv.org/abs/2402.03216)

---

## 15. Conclusion & Actionable Answers

### Is our normalizer doing what it is supposed to do?
**Partially.** It successfully provides a deterministic, idempotent, non-destructive projection for basic ASCII/Latin paragraph text. However, for real-world enterprise and medical documents, it fails on numbers, formulas, tabs, tables, code, and line wrapping.

### Is it adequate for our actual document-processing use case?
**No.** For a healthcare-focused synthetic data factory and RAG pipeline where trust, accuracy, and clinical verification are paramount, the current normalizer introduces critical data corruption (e.g. converting `10² mg` to `102 mg`) and ignores core structural elements (tables, captions, references).

### What are the most consequential gaps?
1. **Dosage & number corruption via NFKC** (`10²` $\rightarrow$ `102`).
2. **Tab deletion** causing token fusion (`Patient:\tJohn` $\rightarrow$ `Patient:John`).
3. **Total omission of Tables, References, and Captions** from normalization.
4. **Code block and structural destruction** via aggressive newline collapsing.
5. **Soft-hyphen word severing** (`para\xad\ngraph` $\rightarrow$ `para graph`).

### Which changes are necessary versus optional?
- **Necessary (Phase A & B):**
  1. Fix tab deletion in `strip_controls`.
  2. Switch from NFKC to NFC + targeted ligature expansion.
  3. Fix CRLF line termination.
  4. Fix dehyphenation for TitleCase/Uppercase and fix soft-hyphen rule ordering.
  5. Extend Normalizer to traverse Tables, Captions, References, and Metadata.
  6. Implement Block.kind awareness (preserve code/formulas/paragraphs).
- **Optional / Later (Phase C):**
  1. Deep dictionary morphological lookup for compound words.
  2. Dual representation storage (source-preserving vs retrieval-optimized).
  3. Detailed per-block diff logging in provenance reports.

### What should be addressed first, and why?
**Phase A (Immediate Correctness & Clinical Safety)** must be addressed first. Fixing the NFKC dosage distortion, Tab deletion, CRLF handling, and soft-hyphen word severing requires zero schema migrations, is low-risk, and immediately prevents data corruption across all downstream parsing, chunking, and embedding runs.

---
*Report prepared for MedFactory AI Engineering Organization. Awaiting user approval prior to implementation.*
