# Module #2 — Text Normalization & Cleaning

**Status:** implemented, tested (11 tests), end-to-end verified with Parser.

## Purpose
Take a Parser's canonical DOM and produce an equally-structured but **normalized** DOM whose block text is clean, deterministic, and safe for downstream (chunking, embeddings, KG extraction, generation, validation).

## Scope decisions (deliberate)
- **In scope (formatting only):** control/BOM stripping, Unicode NFKC, whitespace collapse, hard-line-break dehyphenation, typography normalisation (smart quotes → straight; en/em–dash → `-`; NBSP → space; soft hyphen removed).
- **Out of scope (deliberately):** ontology / synonym resolution (`Heart Attack vs MI`), unit standardisation, full spell-fix. That is the **Ontology module** (later), keep this module domain-agnostic.

## Architecture
- **Rule pipeline** (not ML): fixed order, each rule a pure function `text -> (text, changed)`; deterministic + idempotent (apply∘apply == apply). Versioned rule list in config; each rule individually toggleable for auditing.
- **Non-destructive projection**: `model_copy(deep=True)`; only `Block.text` changes; ids, bboxes, reading order, metadata, tables, images preserved.
- **Provenance**: new `Document.provenance.normalizer_version` + `normalization_report` (rules, blocks changed, chars in/out, per-rule counts). Source bytes + parsed DOM stay immutable in the Store (SYN2: never overwrite).

## Files
```
app/normalizer/   config.py · rules.py · pipeline.py · normalizer.py · cli.py
tests/test_normalizer.py
```
CLI: `python -m app.normalizer.cli --dom <parsed.dom.json> --out <normalized.dom.json>`

## Rules
`strip_controls → nfkc → dehyphenate → collapse_whitespace → typography` (idempotent order).

## Failure modes / edits
- Faithful: never modifies numbers, units (mg/dL, µL, °C), or clinical tokens.
- Known limit: a space-wrap hyphen from a flow loader (e.g. Markdown joins lines with space) is *not* dejoined (only hard `/newline` hyphens are). Documented future improvement.

## Testing
11 normalizer tests + 7 parser tests = 18 passing: rule-level, pipeline idempotency, structure preservation, provenance/report, no-op on clean text.

## Next
Module #3 — Semantic Chunking (own module, own spec).