"""Stage 3: Context-aware dehyphenation.

Repairs words broken across line breaks (e.g. OCR line breaks, column wraps)
with support for:
  * Lowercase words (para-\\ngraph -> paragraph)
  * TitleCase words (Cardio-\\nvascular -> Cardiovascular)
  * Uppercase / Acronym words (DIAG-\\nNOSIS -> DIAGNOSIS)
  * Latin-extended / European diacritics (e.g. French, German, Spanish, Nordic)
  * Soft hyphens (\\u00AD)

Guards legitimate compound words (e.g. cost-effective, evidence-based,
beta-blocker, follow-up, NON-INVASIVE) against destructive joining.
"""
from __future__ import annotations

import re
from typing import Optional

from .base import RuleContext

# Medical, scientific, and common hyphenated compounds to preserve across line breaks
_PROTECTED_COMPOUNDS = {
    "cost-effective", "cost-effectiveness", "evidence-based", "beta-blocker", "beta-blockers",
    "follow-up", "follow-ups", "self-reported", "well-known", "first-line", "second-line",
    "third-line", "post-operative", "post-treatment", "pre-existing", "pre-treatment",
    "x-ray", "x-rays", "cross-sectional", "peer-reviewed", "long-term", "short-term",
    "double-blind", "single-blind", "high-risk", "low-risk", "low-dose", "high-dose",
    "placebo-controlled", "non-invasive", "open-label", "dose-dependent", "dose-response",
    "cut-off", "cut-offs", "scale-up", "side-effect", "side-effects", "end-point", "end-points",
    "time-dependent", "treatment-resistant", "health-related", "all-cause", "case-control",
    "intent-to-treat", "statistically-significant", "disease-free", "progression-free",
    "over-the-counter", "gold-standard", "real-world", "state-of-the-art",
}

_PROTECTED_PREFIXES = {
    "non", "pre", "post", "anti", "multi", "sub", "co", "cross", "self", "well",
    "beta", "dose", "peer", "first", "second", "third", "long", "short", "high",
    "low", "all", "half", "gold", "real",
}

# Matches letters (including Latin extended / European diacritics) separated by hyphen + newline
_DEHYPHEN_RE = re.compile(
    r"(\b[A-Za-zÀ-ÖØ-öø-ÿĀ-ž]+)[-­‐‑]\s*\n\s*([A-Za-zÀ-ÖØ-öø-ÿĀ-ž]+\b)"
)


def dehyphenate(text: str, context: Optional[RuleContext] = None) -> tuple[str, bool]:
    """Dehyphenate broken words across line breaks while guarding legitimate compounds."""
    if not text:
        return text, False

    ctx = context or RuleContext()
    original = text

    def _replace_hyphen_break(match: re.Match) -> str:
        w1, w2 = match.group(1), match.group(2)
        compound_key = f"{w1.lower()}-{w2.lower()}"

        if ctx.preserve_compound_hyphens:
            # Check if this pair is a known compound word
            if compound_key in _PROTECTED_COMPOUNDS:
                return f"{w1}-{w2}"
            # Check if prefix indicates compound preservation for uppercase or known prefix
            if w1.isupper() and w2.isupper() and (w1.lower() in _PROTECTED_PREFIXES or compound_key in _PROTECTED_COMPOUNDS):
                return f"{w1}-{w2}"

        # Otherwise join cleanly across line break
        return f"{w1}{w2}"

    # Step 1: Perform context-aware join across line break hyphens
    new_text = _DEHYPHEN_RE.sub(_replace_hyphen_break, text)

    # Step 2: Strip any remaining soft hyphens (U+00AD)
    if "­" in new_text:
        new_text = new_text.replace("­", "")

    return new_text, new_text != original
