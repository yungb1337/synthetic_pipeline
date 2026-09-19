"""Synthetic Data Factory — Normalizer module (Module #2).

Consumes a parsed DOM and returns an equally-shaped, **normalized** DOM whose
text across all DOM elements (blocks, tables, captions, references, metadata)
is clean, canonical, and deterministic.

Key Guarantees:
  * Deterministic — same input DOM + config => exact same output.
  * Idempotent    — normalizing a normalized DOM is a provable no-op (f(f(x)) == f(x)).
  * Conservative  — normalizes formatting only (Unicode NFC baseline, tabs,
    hyphens, controls, quotes). Never alters medical/scientific ontology or numbers.
  * Structure-aware — respects code blocks, formulas, and multi-paragraph layout.
  * Non-destructive — returns a NEW Document carrying an audit report in provenance.

Version: 0.2.0
"""
from __future__ import annotations

from .config import NormalizerConfig
from .normalizer import Normalizer, NormalizeResult
from .pipeline import apply, is_idempotent

__version__ = "0.2.0"

__all__ = [
    "Normalizer",
    "NormalizerConfig",
    "NormalizeResult",
    "apply",
    "is_idempotent",
    "__version__",
]
