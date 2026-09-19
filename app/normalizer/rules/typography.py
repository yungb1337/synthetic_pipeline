"""Stage 5: Typography and punctuation normalization.

Standardizes typographic punctuation:
  * Smart quotes (single & double) -> ASCII straight quotes (' and ")
  * En dashes (–) -> standard hyphen (-)
  * Em dashes (—) -> separated hyphens (' - ') to prevent word/clause fusion
  * Non-breaking and thin spaces (\\u00A0, \\u202F, etc.) -> standard space
  * Soft hyphens (\\u00AD) -> removed
"""
from __future__ import annotations

import re
from typing import Optional

from .base import RuleContext

_UNSPACED_EM_DASH_RE = re.compile(r"(\S)—(\S)")

_TYPOGRAPHY_MAP = {
    # Smart single quotes
    "‘": "'",
    "’": "'",
    "‚": "'",
    "‛": "'",
    # Smart double quotes
    "“": '"',
    "”": '"',
    "„": '"',
    "‟": '"',
    "«": '"',
    "»": '"',
    # Dashes
    "–": "-",  # En-dash
    "‒": "-",  # Figure dash
    "―": "-",  # Horizontal bar
    "—": "-",  # Em-dash (fallback after unspaced replacement)
    # Non-breaking and typographic spaces
    " ": " ",  #   NBSP
    " ": " ",  #   Narrow NBSP
    " ": " ",  #   Figure space
    " ": " ",  #   Punctuation space
    " ": " ",  #   Thin space
    " ": " ",  #   Hair space
    # Soft hyphen
    "­": "",   # ­
}


def typography(text: str, context: Optional[RuleContext] = None) -> tuple[str, bool]:
    """Standardize punctuation and quotes to ASCII equivalents."""
    if not text:
        return text, False

    original = text
    new_text = text

    # Step 1: Replace unspaced em-dashes to avoid fusing words into invalid tokens
    # e.g. "dyspnea—especially" -> "dyspnea - especially"
    new_text = _UNSPACED_EM_DASH_RE.sub(r"\1 - \2", new_text)

    # Step 2: Apply char-by-char typographic normalization
    for src, dst in _TYPOGRAPHY_MAP.items():
        if src in new_text:
            new_text = new_text.replace(src, dst)

    return new_text, new_text != original
