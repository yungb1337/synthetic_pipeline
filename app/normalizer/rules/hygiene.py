"""Stage 1: Text & Control Hygiene rule.

Normalizes line endings (CRLF/CR -> LF), expands tabs into spaces (avoiding
token fusing), strips non-printable C0/C1 control characters, BOM, and
unwanted zero-width spaces while safely preserving multilingual joiners
(ZWNJ / ZWJ) for Persian, Arabic, and Indic scripts.
"""
from __future__ import annotations

import re
from typing import Optional

from .base import RuleContext, RuleResult


def _build_control_re(include_zwnj_zwj: bool = False) -> re.Pattern:
    # C0 controls: 0x00..0x08, 0x0B..0x0C, 0x0E..0x1F (note: 0x09 \t, 0x0A \n, 0x0D \r excluded)
    # C1 controls / DEL: 0x7F
    # Formatting noise: BOM 0xFEFF, ZWSP 0x200B, WJ 0x2060, MVS 0x180E
    chars = [
        chr(c) for c in range(0x00, 0x09)
    ] + [
        chr(0x0B), chr(0x0C)
    ] + [
        chr(c) for c in range(0x0E, 0x20)
    ] + [
        chr(0x7F),
        chr(0xFEFF),  # BOM
        chr(0x200B),  # Zero-width space
        chr(0x2060),  # Word joiner
        chr(0x180E),  # Mongolian vowel separator
    ]
    if include_zwnj_zwj:
        chars.extend([chr(0x200C), chr(0x200D)])  # ZWNJ, ZWJ
    return re.compile("[" + "".join(re.escape(c) for c in chars) + "]")


_CONTROL_RE_PRESERVE_ZWNJ = _build_control_re(include_zwnj_zwj=False)
_CONTROL_RE_STRIP_ALL = _build_control_re(include_zwnj_zwj=True)


def strip_controls(text: str, context: Optional[RuleContext] = None) -> tuple[str, bool]:
    """Strip C0/C1 control characters, BOM, and noise while standardizing CRLF and tabs.

    Guarantees:
      * CRLF ('\\r\\n') and lone CR ('\\r') are converted to standard LF ('\\n').
      * Tabs ('\\t') are converted to single spaces (or 4 spaces in code blocks) rather
        than stripped to empty string, preventing column fusion ('Patient:\\tJohn' -> 'Patient: John').
      * ZWNJ/ZWJ are preserved if context.preserve_multilingual_zwnj is True (default).
    """
    if not text:
        return text, False

    ctx = context or RuleContext()
    original = text

    # Step 1: Standardize line breaks
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    # Step 2: Tab handling (prevent data corruption / token fusion)
    if "\t" in text:
        if ctx.kind == "code" and ctx.preserve_code_blocks:
            text = text.replace("\t", "    ")
        else:
            text = text.replace("\t", " ")

    # Step 3: Strip control characters & BOM
    control_re = _CONTROL_RE_PRESERVE_ZWNJ if ctx.preserve_multilingual_zwnj else _CONTROL_RE_STRIP_ALL
    text = control_re.sub("", text)

    changed = text != original
    return text, changed
