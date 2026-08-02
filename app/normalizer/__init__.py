"""Synthetic Data Factory — Normalizer module (Module #2).

Consumes a parsed DOM and returns an equally-shaped, **normalized** DOM whose
block text is clean and deterministic. It is a pure, rule-based, idempotent
projection:

  * deterministic — same input DOM + config => same output; reproducible.
  * idempotent    — normalizing a normalized DOM is a no-op.
  * conservative  — normalizes formatting only (Unicode, whitespace, OCR line-
    break joins, punctuation, symbols). Ontology / synonym resolution (e.g.
    "Heart Attack" -> "Myocardial Infarction") is deliberately OUT of scope:
    that is the Ontology module, later in the pipeline.
  * non-destructive - returns a NEW Document carrying a normalization report in
    provenance; source bytes + parsed DOM stay immutable.

Version: 0.1.0
"""

__version__ = "0.1.0"