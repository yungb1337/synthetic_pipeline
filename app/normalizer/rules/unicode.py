"""Stage 2: Unicode canonicalization & targeted ligature expansion.

Uses NFC as the canonical baseline to preserve critical domain notation:
  * Exponents / superscripts: 10² mg, 10⁴ CFU (NFKC destructively converts to 102 mg, 104 CFU)
  * Subscripts & chemical valences: CO₂, Ca²⁺, H₂O
  * Fractions & symbols: ½, µL, °C

Targeted ligature expansion safely unfolds typographic ligatures (ﬁ, ﬂ, ﬀ, ﬃ, ﬄ)
without the collateral damage of full NFKC compatibility decomposition.
"""
from __future__ import annotations

import unicodedata
from typing import Optional

from .base import RuleContext

_LIGATURE_MAP = {
    "ﬀ": "ff",   # ﬀ
    "ﬁ": "fi",   # ﬁ
    "ﬂ": "fl",   # ﬂ
    "ﬃ": "ffi",  # ﬃ
    "ﬄ": "ffl",  # ﬄ
    "ﬅ": "st",   # ﬅ
    "ﬆ": "st",   # ﬆ
}

_LIGATURE_TRANS = str.maketrans(_LIGATURE_MAP)


def _normalize_form(text: str, form: str) -> tuple[str, bool]:
    if not text:
        return text, False
    new = unicodedata.normalize(form, text)
    return new, new != text


def unicode(text: str, context: Optional[RuleContext] = None) -> tuple[str, bool]:
    """Canonicalize Unicode using NFC (or configured form) preserving exponents & subscripts."""
    form = (context.unicode_form if context and context.unicode_form else "NFC").upper()
    return _normalize_form(text, form)


def nfc(text: str, context: Optional[RuleContext] = None) -> tuple[str, bool]:
    """Normalize text using Unicode Normalization Form C (NFC)."""
    return _normalize_form(text, "NFC")


def nfkc(text: str, context: Optional[RuleContext] = None) -> tuple[str, bool]:
    """Normalize text using Unicode Normalization Form KC (NFKC) for backward compatibility."""
    return _normalize_form(text, "NFKC")


def expand_ligatures(text: str, context: Optional[RuleContext] = None) -> tuple[str, bool]:
    """Targeted expansion of typographic ligatures (ﬁ -> fi, ﬂ -> fl, etc.)."""
    if not text:
        return text, False
    new = text.translate(_LIGATURE_TRANS)
    return new, new != text
