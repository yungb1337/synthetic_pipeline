"""Synthetic Data Factory — Parser module (Extraction -> DOM).

This package turns a raw file into a canonical, parser-independent
Document Object Model (DOM) in a single read, so no downstream consumer
is ever coupled to a specific file format.

Design constraints (from the architecture brief):
  * Modular monolith — modules are responsibilities, not network boundaries.
  * Every format loader produces the SAME Document DOM.
  * Layout/OCR/tables are extractors inside one extraction pass, not separate
    re-reads of the file.
  * Idempotent, deterministic, versioned, observable.
"""

__version__ = "0.1.0"