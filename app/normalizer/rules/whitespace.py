"""Stage 4: Structure-aware whitespace normalization.

Respects document structure by inspecting block semantics:
  * Code blocks (kind="code"): Preserves indentation and newlines, expands tabs to 4 spaces, strips trailing whitespace.
  * Formulas (kind="formula"): Preserves notation and internal spacing.
  * Paragraphs / Headings: Collapses single line breaks into spaces (soft OCR wraps) while preserving '\\n\\n' paragraph breaks (when enabled).
"""

from __future__ import annotations

import re

from .base import RuleContext

# Regex to identify paragraph boundaries (2 or more newlines, possibly containing whitespace)
_PARA_BREAK_RE = re.compile(r"\n\s*\n+")

# Regex to collapse single line breaks and horizontal space runs within a paragraph
_SINGLE_LF_RE = re.compile(r"(?<!\n)\n(?!\n)")
_HORIZ_WS_RE = re.compile(r"[ \t]{2,}")

# Full collapse regex (collapses all newlines and whitespace runs to a single space)
_ALL_WS_RE = re.compile(r"(?:[ \t]*\n)+[ \t]*|[ \t]{2,}")


def collapse_whitespace(
    text: str, context: RuleContext | None = None
) -> tuple[str, bool]:
    """Collapse whitespace according to structural semantics and configuration."""
    if not text:
        return text, False

    ctx = context or RuleContext()
    original = text

    # Case 1: Code blocks - preserve structure and indentation
    if ctx.kind == "code" and ctx.preserve_code_blocks:
        lines = [line.rstrip() for line in text.splitlines()]
        # Strip leading/trailing empty lines
        while lines and not lines[0]:
            lines.pop(0)
        while lines and not lines[-1]:
            lines.pop()
        new_text = "\n".join(lines)
        return new_text, new_text != original

    # Case 2: Formulas - preserve mathematical formatting
    if ctx.kind == "formula" and ctx.preserve_formulas:
        new_text = text.strip()
        return new_text, new_text != original

    # Case 3: Structure-aware paragraph preservation
    if ctx.preserve_paragraph_breaks:
        # Standardize line breaks first
        normalized_lf = text.replace("\r\n", "\n").replace("\r", "\n")
        # Split across paragraph breaks (2+ newlines)
        paragraphs = _PARA_BREAK_RE.split(normalized_lf)
        cleaned_paras = []
        for p in paragraphs:
            p_clean = _SINGLE_LF_RE.sub(" ", p)
            p_clean = _HORIZ_WS_RE.sub(" ", p_clean).strip()
            if p_clean:
                cleaned_paras.append(p_clean)
        new_text = "\n\n".join(cleaned_paras)
        return new_text, new_text != original

    # Case 4: Legacy / flat collapse (collapse everything to single spaces)
    new_text = _ALL_WS_RE.sub(" ", text).strip()
    return new_text, new_text != original
